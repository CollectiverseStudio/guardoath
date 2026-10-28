"""Local development API. Credentials bind requests to one application."""

import hmac
import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from valinator.checkers import default_checkers
from valinator.config import load_policies
from valinator.engine import Engine
from valinator.models import Action, CheckRequest, CheckResponse, Policy, Rule, Stage
from valinator.ingress import JsonIngressLimits
from valinator.groups import GROUPS
from valinator.budgets import ResourceLimits, RequestBudgets, RequestCapacity


def demo_engine(enable_pii: bool = False, enable_azure: bool = False) -> Engine:
    return Engine([Policy(
        application_id="demo", version="support-v1", timeout_ms=2000 if enable_azure else 100,
        rules=(Rule(checker_id="schema", required=False, on_finding=Action.BLOCK),
               Rule(checker_id="safety", on_finding=Action.BLOCK),
               Rule(checker_id="bias", on_finding=Action.FLAG)) + (
                   (Rule(checker_id="pii", on_finding=Action.FLAG),) if enable_pii else ()) + (
                   (Rule(checker_id="azure_prompt_shields", stages=(Stage.INPUT,), on_finding=Action.FLAG),) if enable_azure else ()),
        input_schema={"type": "object", "properties": {"message": {"type": "string"}},
                      "required": ["message"], "additionalProperties": False},
        output_schema={"type": "object", "properties": {"answer": {"type": "string"}},
                       "required": ["answer"], "additionalProperties": False},
    )], default_checkers(enable_pii=enable_pii, enable_azure=enable_azure))


def create_app(engine: Engine | None = None, credentials: dict[str, str] | None = None,
               limits: ResourceLimits | None = None) -> FastAPI:
    policy_file = os.getenv("VALINATOR_POLICY_FILE")
    if engine is not None:
        service = engine
    else:
        pii_setting = os.getenv("VALINATOR_ENABLE_PII", "0")
        if pii_setting not in {"0", "1"}:
            raise ValueError("VALINATOR_ENABLE_PII must be 0 or 1")
        enable_pii = pii_setting == "1"
        azure_setting = os.getenv("VALINATOR_ENABLE_AZURE_PROMPT_SHIELDS", "0")
        if azure_setting not in {"0", "1"}:
            raise ValueError("VALINATOR_ENABLE_AZURE_PROMPT_SHIELDS must be 0 or 1")
        enable_azure = azure_setting == "1"
        service = (Engine(load_policies(policy_file), default_checkers(enable_pii=enable_pii, enable_azure=enable_azure))
                   if policy_file else demo_engine(enable_pii=enable_pii, enable_azure=enable_azure))
    # No embedded credential. Without configuration, checks fail closed with 503.
    key = os.getenv("VALINATOR_API_KEY", "")
    bindings = dict(credentials) if credentials is not None else ({key: "demo"} if key else {})
    if any(not token or not app_id for token, app_id in bindings.items()):
        raise ValueError("Credentials and application identifiers must be nonempty")
    resource_limits = limits if limits is not None else ResourceLimits.from_environment()
    budgets = RequestBudgets(resource_limits, set(bindings.values()))
    api = FastAPI(title="Valinator", version="0.15.0")
    api.add_middleware(JsonIngressLimits)
    api.add_middleware(RequestCapacity, budgets=budgets)
    bearer = HTTPBearer(auto_error=False)
    static_dir = Path(__file__).parent / "static"
    api.mount("/assets", StaticFiles(directory=static_dir), name="assets")

    @api.get("/", include_in_schema=False)
    async def index():
        return RedirectResponse("/dashboard")

    @api.get("/dashboard", include_in_schema=False)
    async def dashboard():
        return FileResponse(static_dir / "dashboard.html", headers={
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        })

    @api.exception_handler(RequestValidationError)
    async def validation_error(_request, _exc):
        # FastAPI's default 422 echoes invalid submitted values.
        return JSONResponse(status_code=422, content={"detail": "Invalid check request"})

    async def authenticate(
        auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> str:
        if not bindings:
            raise HTTPException(503, "Application credentials are not configured")
        if auth is not None:
            for token, app_id in bindings.items():
                if hmac.compare_digest(auth.credentials.encode(), token.encode()):
                    return app_id
        raise HTTPException(401, "Invalid application credential", headers={"WWW-Authenticate": "Bearer"})

    async def assess(request: CheckRequest, application_id: str, stage: Stage):
        if request.application_id != application_id:
            raise HTTPException(403, "Credential does not authorize this application")
        if not service.has_policy(application_id, request.policy_version):
            raise HTTPException(404, "Policy version not found")
        with budgets.check(application_id):
            return await service.check(request, stage)

    @api.get("/healthz")
    async def health():
        return {"status": "ok", "profile": "local-prototype"}

    @api.get("/v1/applications/current")
    async def current_application(app_id: Annotated[str, Depends(authenticate)]):
        return {"application_id": app_id, "policies": service.describe_policies(app_id),
                "resource_limits": {"scope": "server_process", **resource_limits.model_dump()},
                "groups": [{"group_id": gid, "name": name, "checker_ids": ids, "limitations": limits}
                           for gid, name, ids, limits in GROUPS]}

    @api.post("/v1/checks/input", response_model=CheckResponse)
    async def check_input(request: CheckRequest, app_id: Annotated[str, Depends(authenticate)]):
        return await assess(request, app_id, Stage.INPUT)

    @api.post("/v1/checks/output", response_model=CheckResponse)
    async def check_output(request: CheckRequest, app_id: Annotated[str, Depends(authenticate)]):
        return await assess(request, app_id, Stage.OUTPUT)

    return api


app = create_app()
