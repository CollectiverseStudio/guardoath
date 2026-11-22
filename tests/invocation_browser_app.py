"""Local browser acceptance fixture; never imported by the production runtime.

The application, authentication routing, tenant compiler, Engine, quota handling,
and on-disk receipt store are real. Only the external identity/provider boundaries
use deterministic synthetic data. No network provider credentials are present.
"""
from __future__ import annotations

import atexit
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Lock
import time

from fastapi import Header, HTTPException, Request

from guardoath.app import create_app, demo_engine
from guardoath.budgets import ResourceLimits
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.invocations.adapters import ProviderResult
from guardoath.invocations.contracts import ConnectionInfo, ModelInfo
from guardoath.invocations.service import ConnectionBinding, InvocationGateway
from guardoath.invocations.store import SQLiteInvocationStore
from guardoath.tenant_record import TenantRecord
from guardoath.tenant_store import MemoryTenantStore


TOKENS = {
    "synthetic-invocation-owner-alpha": "usr-invocation-alpha",
    "synthetic-invocation-owner-beta": "usr-invocation-beta",
    "synthetic-invocation-owner-empty": "usr-invocation-empty",
}
SIGNING_KEY = b"synthetic-browser-receipt-signing-key-not-production-0123456789"


class VerifiedFixtureSessions:
    registration_enabled = False

    def tenant_for(self, token):
        return TOKENS.get(token)

    def public_config(self):
        return {
            "authorize_url": "https://example.invalid/oauth2/authorize",
            "logout_url": "https://example.invalid/logout", "client_id": "synthetic-browser",
            "redirect_uri": "http://127.0.0.1:8778/dashboard", "scope": "openid email",
            "registration_enabled": False,
        }


class RecordingProvider:
    def __init__(self, tenant_id, connection_id):
        self.tenant_id = tenant_id
        self.connection_id = connection_id
        self.connection_version = "fixture-v1"
        self._lock = Lock()
        self.calls = []

    def invoke(self, call):
        with self._lock:
            self.calls.append({"model_id": call.model_id, "max_output_tokens": call.max_output_tokens,
                               "text": call.text})
            index = len(self.calls)
        if "fixture:slow" in call.text:
            time.sleep(1)
        if "fixture:unknown" in call.text:
            return ProviderResult(outcome="unknown", reason_code="provider_timeout")
        if "fixture:output-block" in call.text:
            text = "UNSAFE_ANSWER_MARKER \u202e hidden direction"
        elif "fixture:echo" in call.text:
            text = call.text
        else:
            text = "A checked answer from the fixture model."
        return ProviderResult(text=text, input_tokens=12, output_tokens=min(8, call.max_output_tokens),
                              provider_request_id=f"fixture-call-{index}",
                              response_id=f"fixture-response-{index}", observed_model=call.model_id,
                              outcome="completed", reason_code="completed")


temporary = tempfile.TemporaryDirectory(prefix="guardoath-invocation-browser-")
atexit.register(temporary.cleanup)
checkers = default_checkers()
records = tuple(TenantRecord(tenant_id=tenant_id, display_name=tenant_id.removeprefix("usr-").title(),
                            demo=False, subscription_status="active", monthly_allowance=100000,
                            overage="block", mode="enforce", input_check_text_security="block",
                            output_check_text_security="block") for tenant_id in TOKENS.values())
tenant_store = MemoryTenantStore(records)
tenant_service = TenantService(tenant_store, checkers)
sessions = VerifiedFixtureSessions()
providers = {tenant_id: RecordingProvider(tenant_id, "connection-" + tenant_id.rsplit("-", 1)[-1])
             for tenant_id in tuple(TOKENS.values())[:2]}
connections = [ConnectionBinding(application_id=tenant_id,
    info=ConnectionInfo(id=provider.connection_id, version=provider.connection_version,
        label="Approved " + tenant_id.rsplit("-", 1)[-1].title() + " connection",
        provider="aws_bedrock" if tenant_id.endswith("alpha") else "openai",
        region="us-east-1" if tenant_id.endswith("alpha") else None,
        models=(ModelInfo(id="fixture-model", label="Approved fixture model",
                          provider_model_id="fixture.pinned-model-v1", max_output_tokens=512),
                ModelInfo(id="fixture-small", label="Approved small model",
                          provider_model_id="fixture.pinned-small-v1", max_output_tokens=64)),
        monthly_call_limit=1000, monthly_output_token_limit=500000), adapter=provider)
    for tenant_id, provider in providers.items()]
receipt_store = SQLiteInvocationStore(Path(temporary.name) / "receipts.sqlite3", SIGNING_KEY)
gateway = InvocationGateway(store=receipt_store, connections=connections,
                            provider_timeout=4, request_timeout=10)
atexit.register(gateway.workers.close)
limits = ResourceLimits(checks_per_minute=60000, burst=1000)
app = create_app(engine=demo_engine(checkers=checkers), credentials={}, limits=limits,
                 tenants=tenant_service, sessions=sessions, invocation_gateway=gateway)
unconfigured_app = create_app(engine=demo_engine(checkers=checkers), credentials={}, limits=limits,
                              tenants=tenant_service, sessions=sessions)
observations = []


@app.middleware("http")
async def record_exact_invocation_transport(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/v1/interactions":
        body = await request.body()
        observations.append({"application_id": sessions.tenant_for(request.headers.get("x-guardoath-session")),
            "body": json.loads(body), "hash_matches": request.headers.get("x-amz-content-sha256") == hashlib.sha256(body).hexdigest(),
            "session_header": bool(request.headers.get("x-guardoath-session")),
            "api_key_header": bool(request.headers.get("x-guardoath-key"))})
    return await call_next(request)


@app.get("/__test/observations")
async def recorded_calls(x_guardoath_session: str | None = Header(default=None)):
    tenant_id = sessions.tenant_for(x_guardoath_session)
    if tenant_id is None:
        raise HTTPException(401)
    provider = providers.get(tenant_id)
    return {"calls": list(provider.calls) if provider else [],
            "requests": [item for item in observations if item["application_id"] == tenant_id]}


@app.get("/__test/reopen-receipt/{interaction_id}")
async def reopen_persisted_receipt(interaction_id: str, x_guardoath_session: str | None = Header(default=None)):
    tenant_id = sessions.tenant_for(x_guardoath_session)
    if tenant_id is None:
        raise HTTPException(401)
    reopened = SQLiteInvocationStore(Path(temporary.name) / "receipts.sqlite3", SIGNING_KEY)
    receipt = reopened.get(tenant_id, interaction_id)
    if receipt is None:
        raise HTTPException(404)
    return receipt.model_dump(mode="json")
