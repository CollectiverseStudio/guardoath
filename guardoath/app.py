"""Local development API. Credentials bind requests to one application."""

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from time import perf_counter
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from guardoath.checkers import default_checkers
from guardoath.attachment_ingress import AttachmentCheckRequest, inspect_attachments
from guardoath.control_registry import registry_payload
from guardoath.config import load_policies
from guardoath.engine import Engine, PRIORITY
from pydantic import Field

from guardoath.models import Action, ApiCheckRequest, CheckRequest, CheckResponse, Contract, Policy, Rule, Stage
from guardoath.ingress import JsonIngressLimits
from guardoath.groups import GROUPS
from guardoath.budgets import ResourceLimits, RequestBudgets, RequestCapacity
from guardoath.validation_packs import PackSelectionError, load_pack_registry
from guardoath.tenants import TenantRegistry
from guardoath.hosted import ChangeRejected, CheckChanges, NotADemoUser, TenantService
from guardoath.tenant_record import (CHECK_IDS, TENANT_ID_PATTERN, TENANT_POLICY_VERSION,
                                     TenantRecord, usage_month)
from guardoath.tenant_store import TenantConflict, TenantNotFound, UsageLimitReached
from guardoath.tenant_record import new_key
from guardoath.record_rules import record_rules
from guardoath.trusted_reference import reference_is_trusted
from guardoath.sigma_site import catalog_payload as sigma_catalog_payload, evaluate_fixture as sigma_evaluate_fixture
from guardoath.agent_activity import compare_actions, InvalidAction
from guardoath.tenant_sessions import SESSION_HEADER, CognitoSessions, SignInBusy


def release_source_sha(path: Path | None = None) -> str | None:
    """Public Git SHA embedded in Lambda package during a clean-source build."""
    path = Path(__file__).with_name("_release_source.json") if path is None else path
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("source_sha")
    except (OSError, UnicodeError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) else None


# Videos are sent in parts of at most this size. The hosted service runs on AWS Lambda, whose buffered
# responses are limited to 6 MB after base64 encoding: 4 MiB is 5,592,408 base64 characters, under the
# 5,800,000 that guardoath.lambda_handler allows. The demo video (3,580,895 B since 2026-10-31, 96 kbit/s
# audio) fits in one part, so every request for it is answered with the whole file and none is cut short.
MEDIA_PART_BYTES = 4 * 1024 * 1024
MEDIA_STEM = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
SINGLE_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
# The API key header. Behind CloudFront with origin access control, CloudFront replaces the Authorization
# header with its own signature, so hosted clients send the key here; Authorization: Bearer still works.
KEY_HEADER = "X-GuardOath-Key"


def media_part(path: Path, range_header: str | None, head: bool) -> Response:
    """One response for a video: the whole file when it fits in one part, otherwise the requested byte
    range, cut to MEDIA_PART_BYTES. Video players request ranges and ask again for the rest."""
    size = path.stat().st_size
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=86400"}
    if range_header is None:
        if size > MEDIA_PART_BYTES:
            return Response(f"This video is sent in parts of up to {MEDIA_PART_BYTES} bytes. Request it with a Range header (bytes=start-end).",
                            status_code=416, media_type="text/plain", headers={**headers, "Content-Range": f"bytes */{size}"})
        return Response(b"" if head else path.read_bytes(), media_type="video/mp4",
                        headers={**headers, "Content-Length": str(size)})
    match = SINGLE_RANGE.fullmatch(range_header.strip())
    if match is None or match.group(1) == match.group(2) == "":
        # Several ranges, another unit or a malformed range: refused rather than guessed.
        return Response("Request one byte range (bytes=start-end).", status_code=416, media_type="text/plain",
                        headers={**headers, "Content-Range": f"bytes */{size}"})
    first, last = match.group(1), match.group(2)
    if first == "":
        start, end = max(0, size - int(last)), size - 1
    else:
        start, end = int(first), size - 1 if last == "" else min(int(last), size - 1)
    if size == 0 or start >= size or end < start:
        return Response("Range not satisfiable.", status_code=416, media_type="text/plain",
                        headers={**headers, "Content-Range": f"bytes */{size}"})
    end = min(end, start + MEDIA_PART_BYTES - 1)
    length = end - start + 1
    body = b""
    if not head:
        with path.open("rb") as stream:
            stream.seek(start)
            body = stream.read(length)
    return Response(body, status_code=206, media_type="video/mp4",
                    headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(length)})


def demo_engine(enable_pii: bool = False, checkers: dict | None = None) -> Engine:
    """The built-in demo policy. checkers: an already built default_checkers() result with the same
    settings, so an app builds its checkers (the sensitive-data checker takes most of the start-up
    time) once instead of twice."""
    return Engine([Policy(
        application_id="demo", version="support-v1", timeout_ms=100,
        rules=(Rule(checker_id="schema", required=False, on_finding=Action.BLOCK),
               Rule(checker_id="safety", on_finding=Action.BLOCK),
               Rule(checker_id="bias", on_finding=Action.FLAG)) + (
                   (Rule(checker_id="pii", on_finding=Action.FLAG),) if enable_pii else ()),
        input_schema={"type": "object", "properties": {"message": {"type": "string"}},
                      "required": ["message"], "additionalProperties": False},
        output_schema={"type": "object", "properties": {"answer": {"type": "string"}},
                       "required": ["answer"], "additionalProperties": False},
    )], checkers if checkers is not None else default_checkers(enable_pii=enable_pii))


class KeyRevocation(Contract):
    key_id: str = Field(pattern=r"^[0-9a-f]{12}$")


class AgentActivityRequest(Contract):
    proposed: dict
    observed: dict | None = None


class SigmaDemoRequest(Contract):
    rule_id: str = Field(min_length=1, max_length=128)
    sample: Literal["positive", "lookalike"]


class SignInCode(Contract):
    code: str = Field(min_length=1, max_length=256)
    code_verifier: str = Field(min_length=43, max_length=128)


def create_app(engine: Engine | None = None, credentials: dict[str, str] | None = None,
               limits: ResourceLimits | None = None,
               tenant_registry: TenantRegistry | None = None,
               tenants: TenantService | None = None,
               sessions: CognitoSessions | None = None) -> FastAPI:
    policy_file = os.getenv("GUARDOATH_POLICY_FILE")
    pack_file = os.getenv("GUARDOATH_PACK_FILE")
    if engine is not None:
        if pack_file:
            raise ValueError("Pack registry must be configured on the supplied engine")
        service = engine
        # Only for the control registry's detector versions; the supplied engine has its own checkers.
        checkers = tenants.checkers if tenants is not None else None
    else:
        pii_setting = os.getenv("GUARDOATH_ENABLE_PII", "0")
        if pii_setting not in {"0", "1"}:
            raise ValueError("GUARDOATH_ENABLE_PII must be 0 or 1")
        enable_pii = pii_setting == "1"
        checkers = default_checkers(enable_pii=enable_pii)
        service = (Engine(load_policies(policy_file), checkers, pack_registry=load_pack_registry(pack_file) if pack_file else None)
                   if policy_file else demo_engine(enable_pii=enable_pii, checkers=checkers))
        if tenants is None:
            tenants = TenantService.from_environment(checkers)
        if sessions is None:
            sessions = CognitoSessions.from_environment()
    if pack_file and not policy_file and engine is None:
        raise ValueError("Pack configuration requires a policy file")
    # No embedded credential. Without configuration, checks fail closed with 503.
    key = os.getenv("GUARDOATH_API_KEY", "")
    bindings = dict(credentials) if credentials is not None else ({key: "demo"} if key else {})
    if tenant_registry is not None and bindings:
        raise ValueError("Use tenant registry or legacy credentials, not both")
    if any(not token or not app_id for token, app_id in bindings.items()):
        raise ValueError("Credentials and application identifiers must be nonempty")
    resource_limits = limits if limits is not None else ResourceLimits.from_environment()
    if tenants is not None and tenant_registry is not None:
        raise ValueError("Use tenant records or a tenant registry, not both")
    if sessions is not None and tenants is None:
        raise ValueError("Tenant sign-in needs tenant records (GUARDOATH_TENANT_STORE)")
    budget_apps = set(bindings.values()) if tenant_registry is None else set(tenant_registry.application_ids())
    budgets = RequestBudgets(resource_limits, budget_apps)
    api = FastAPI(title="GuardOath", version="0.25.0")
    api.add_middleware(JsonIngressLimits)
    api.add_middleware(RequestCapacity, budgets=budgets)
    bearer = HTTPBearer(auto_error=False)
    static_dir = Path(__file__).parent / "static"

    # Scripts and stylesheets (2026-11-04). The deploy zip stamps every file 2026-01-01 and the /assets mount sent
    # no Cache-Control, so browsers cached them by heuristic (a share of the file's age: weeks) and kept running
    # an old dashboard.js under a new dashboard.html. Now the pages name each script and stylesheet with a
    # version from its content (?v=...), so a changed file is a new address, and the files themselves are
    # cached for five minutes at most.
    def asset_versions() -> dict[str, str]:
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()[:12]
                for pattern in ("*.js", "*.css") for path in static_dir.glob(pattern)}

    versions = asset_versions()
    asset_link = re.compile(r'(?P<attr>(?:src|href)=")/assets/(?P<name>[a-z0-9][a-z0-9_-]{0,63}\.(?:js|css))"')

    def versioned_page(name: str) -> str:
        page = (static_dir / name).read_text(encoding="utf-8")
        return asset_link.sub(lambda m: f'{m["attr"]}/assets/{m["name"]}?v={versions[m["name"]]}"'
                              if m["name"] in versions else m.group(0), page)

    def script_or_style(stem: str, suffix: str):
        path = static_dir / f"{stem}.{suffix}"
        if not MEDIA_STEM.fullmatch(stem) or not path.is_file():
            raise HTTPException(404, "Not Found")
        return FileResponse(path, headers={"Cache-Control": "public, max-age=300"})

    @api.api_route("/assets/{stem}.js", methods=["GET", "HEAD"], include_in_schema=False)
    async def script(stem: str):
        return script_or_style(stem, "js")

    @api.api_route("/assets/{stem}.css", methods=["GET", "HEAD"], include_in_schema=False)
    async def style(stem: str):
        return script_or_style(stem, "css")

    # Registered before the /assets mount, which would otherwise serve the whole file in one response.
    @api.api_route("/assets/{stem}.mp4", methods=["GET", "HEAD"], include_in_schema=False)
    async def video(stem: str, request: Request):
        path = static_dir / (stem + ".mp4")
        if not MEDIA_STEM.fullmatch(stem) or not path.is_file():
            raise HTTPException(404, "Not Found")
        return media_part(path, request.headers.get("range"), request.method == "HEAD")

    api.mount("/assets", StaticFiles(directory=static_dir), name="assets")

    @api.get("/", include_in_schema=False)
    async def index():
        return RedirectResponse("/dashboard")

    @api.get("/dashboard", include_in_schema=False)
    async def dashboard():
        return HTMLResponse(versioned_page("dashboard.html"), headers={
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        })

    @api.get("/sigma", include_in_schema=False)
    async def sigma_preview():
        return HTMLResponse(versioned_page("sigma.html"), headers={
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        })

    @api.get("/guide", include_in_schema=False)
    async def guide():
        # The developer guide is static text: no scripts at all.
        return HTMLResponse(versioned_page("guide.html"), headers={
            "Content-Security-Policy": "default-src 'self'; script-src 'none'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        })

    @api.exception_handler(RequestValidationError)
    async def validation_error(_request, _exc):
        # FastAPI's default 422 echoes invalid submitted values.
        return JSONResponse(status_code=422, content={"detail": "Invalid check request"})

    async def authenticate(
        request: Request,
        auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
        key_header: Annotated[str | None, Header(alias=KEY_HEADER)] = None,
        session_header: Annotated[str | None, Header(alias=SESSION_HEADER)] = None,
    ) -> str | TenantRecord:
        request.state.signed_in = False
        if session_header is not None:
            # A signed-in tenant user (Cognito). Only customer records have accounts; demo users do not.
            # The signature is checked locally; Cognito is asked over the network, so off the event loop.
            try:
                tenant_id = await run_in_threadpool(sessions.tenant_for, session_header) if sessions is not None else None
            except SignInBusy:
                raise HTTPException(503, "Sign-in checks are busy; try again shortly", headers={"Retry-After": "5"}) from None
            record = tenants.store.get(tenant_id) if tenant_id is not None else None
            if record is None or record.status != "active" or record.demo:
                raise HTTPException(401, "Sign-in expired or not valid; sign in again")
            budgets.register(record.tenant_id)
            request.state.signed_in = True
            return record
        if not bindings and tenant_registry is None and tenants is None:
            raise HTTPException(503, "Application credentials are not configured")
        # The key header wins; otherwise a bearer token. Any other Authorization scheme (such as the
        # CloudFront signature) is not a key.
        token = key_header if key_header is not None else (auth.credentials if auth else "")
        if tenants is not None and token.startswith("gok_"):
            record = tenants.authenticate(token)
            if record is None:
                raise HTTPException(401, "Invalid application credential", headers={"WWW-Authenticate": "Bearer"})
            budgets.register(record.tenant_id)
            return record
        if tenant_registry is not None:
            application = tenant_registry.authenticate(token)
            if application is not None:
                return application.application_id
            raise HTTPException(401, "Invalid application credential", headers={"WWW-Authenticate": "Bearer"})
        if token:
            for credential, app_id in bindings.items():
                if hmac.compare_digest(token.encode(), credential.encode()):
                    return app_id
        raise HTTPException(401, "Invalid application credential", headers={"WWW-Authenticate": "Bearer"})

    def sigma_preview_principal(principal: str | TenantRecord) -> str:
        if isinstance(principal, TenantRecord):
            return principal.tenant_id
        if principal != "demo":
            raise HTTPException(403, "Sigma preview needs a tenant or demo credential")
        return principal

    @api.get("/v1/sigma/catalog")
    async def sigma_catalog(principal: Annotated[str | TenantRecord, Depends(authenticate)]):
        app_id = sigma_preview_principal(principal)
        with budgets.check(app_id):
            try:
                result = await run_in_threadpool(sigma_catalog_payload)
            except Exception:
                raise HTTPException(503, "Sigma source catalog unavailable") from None
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @api.post("/v1/sigma/demo")
    async def sigma_demo(body: SigmaDemoRequest,
                         principal: Annotated[str | TenantRecord, Depends(authenticate)]):
        app_id = sigma_preview_principal(principal)
        # A tenant record is authoritative; missing rule action means OFF.
        # Legacy local developer keys may view the source catalog but cannot evaluate
        # tenant-selected event rules on behalf of another tenant.
        if not isinstance(principal, TenantRecord):
            raise HTTPException(403, "A tenant API key is required for Sigma event rules")
        current = tenants.store.get(principal.tenant_id)
        if current is None or current.status != "active":
            raise HTTPException(403, "Tenant record is not active")
        if sessions is not None and not current.demo and current.subscription_status != "active":
            raise HTTPException(402, "An active subscription is required to run checks",
                                headers={"Cache-Control": "no-store"})
        selected_action = current.sigma_rule_actions.get(body.rule_id)
        if selected_action is None:
            raise HTTPException(403, "Sigma event rule is off for this tenant")
        with budgets.check(app_id):
            counted = False
            if isinstance(principal, TenantRecord):
                try:
                    tenants.store.count_check(principal.tenant_id, usage_month())
                except UsageLimitReached:
                    raise HTTPException(429, "Monthly check limit reached") from None
                counted = True
            try:
                result = await run_in_threadpool(
                    sigma_evaluate_fixture, body.rule_id, body.sample,
                    tuple(current.sigma_rule_actions),
                )
                # Return EVERY matching configured event rule; the strongest tenant
                # action wins, as for multiple AI validation findings.
                priority = {"allow": 0, "flag": 1, "block": 2}
                actions = [current.sigma_rule_actions[match["rule_id"]]
                           for match in result["findings"]]
                recommendation = max(actions, key=priority.__getitem__, default="allow")
                result["recommended_action"] = recommendation
                result["effective_action"] = ("allow" if current.mode == "observe" else recommendation)
                result["tenant_policy_revision"] = current.revision
                result["tenant_id"] = current.tenant_id
                result["rule_configuration_source"] = "tenant_record"
            except KeyError:
                if counted:
                    tenants.store.release_check(principal.tenant_id, usage_month())
                raise HTTPException(404, "Sigma rule not found") from None
            except Exception:
                if counted:
                    tenants.store.release_check(principal.tenant_id, usage_month())
                raise HTTPException(503, "Sigma synthetic evaluation could not complete") from None
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @api.post("/v1/agent-activity/compare")
    async def compare_agent_activity(
        request: AgentActivityRequest,
        principal: Annotated[str | TenantRecord, Depends(authenticate)],
    ):
        # Diagnostic ONLY. The authenticated tenant is never permitted to claim
        # that its submitted observed payload is signed/verified host telemetry.
        if not isinstance(principal, TenantRecord):
            raise HTTPException(403, "Agent activity comparison requires a tenant credential")
        if sessions is not None and not principal.demo and principal.subscription_status != "active":
            raise HTTPException(402, "An active subscription is required to run checks",
                                headers={"Cache-Control": "no-store"})
        with budgets.check(principal.tenant_id):
            try:
                result = compare_actions(request.proposed, request.observed)
            except InvalidAction:
                raise HTTPException(422, "Invalid agent activity evidence") from None
        return JSONResponse({
            **result, "tenant_id": principal.tenant_id,
            "mode": "diagnostic_unverified",
            "limitations": "Caller-reported evidence only; no tool authorization or execution proof."
        }, headers={"Cache-Control": "no-store"})

    async def assess(request: ApiCheckRequest, principal: str | TenantRecord, stage: Stage):
        if isinstance(principal, TenantRecord):
            return await assess_tenant(request, principal, stage)
        # A tenant key may leave the profile out (its record's checks run); every other key must name one.
        if request.policy_version is None:
            raise HTTPException(422, "Invalid check request")
        request = CheckRequest.model_validate(request.model_dump())
        application_id = principal
        if request.application_id != application_id:
            raise HTTPException(403, "Credential does not authorize this application")
        if not service.has_policy(application_id, request.policy_version):
            raise HTTPException(404, "Policy version not found")
        with budgets.check(application_id):
            try:
                return await service.check(request, stage)
            except PackSelectionError:
                raise HTTPException(422, "Invalid or unavailable validation pack selection") from None

    async def assess_tenant(request: ApiCheckRequest, record: TenantRecord, stage: Stage,
                            attachments: tuple[bytes, ...] = ()):
        if request.application_id != record.tenant_id:
            raise HTTPException(403, "Credential does not authorize this application")
        # Account registration alone does not authorize paid validations.
        # When customer sign-in is configured, legacy/unverified records
        # fail closed until their paid entitlement is explicitly migrated.
        # Older API-only deployments without customer sessions retain their
        # existing legacy behavior during migration; they must not be treated
        # as paid production onboarding.
        if not record.demo and sessions is not None and record.subscription_status != "active":
            raise HTTPException(402, "An active subscription is required to run checks",
                                headers={"Cache-Control": "no-store"})
        # A tenant key runs its record's own checks only. Naming any other profile is refused (not counted).
        version = request.policy_version or TENANT_POLICY_VERSION
        if version not in tenants.policy_versions(record):
            raise HTTPException(404, "Policy version not found")
        request = CheckRequest.model_validate({**request.model_dump(exclude={"attachments_b64"}),
                                               "policy_version": version})
        engine = tenants.engine_for(record)
        with budgets.check(record.tenant_id):
            month = usage_month()
            try:
                tenants.store.count_check(record.tenant_id, month)
            except UsageLimitReached:
                raise HTTPException(429, "Monthly check limit reached", headers={"Cache-Control": "no-store"}) from None
            try:
                started = perf_counter()
                response = await engine.check(request, stage)
                if not attachments:
                    return response
                # File scanning is inside the existing authenticated, metered and capacity-limited
                # tenant call. Every file runs the same Engine/checker and receives the tenant's
                # stage and per-rule action; no separate policy database or outside service.
                batch = await inspect_attachments(record, stage, attachments, tenants.checkers)
                recommended = max((response.recommended_action, batch.recommended_action),
                                  key=PRIORITY.__getitem__)
                # Preserve the shared group taxonomy while reflecting file findings:
                # a CSV finding is not a disabled Structure group, and an unsupported
                # file must not be reported as a successful inspection.
                csv_results = tuple(result for result in batch.checker_results if result.checker_id == "csv_formula")
                prompt_results = tuple(result for result in batch.checker_results if result.checker_id == "prompt_attack")
                secret_results = tuple(result for result in batch.checker_results if result.checker_id == "secrets")
                pii_results = tuple(result for result in batch.checker_results if result.checker_id == "pii")
                file_status = ("incomplete" if batch.incomplete else
                               "findings" if any(result.status == "flagged" for result in csv_results) else
                               "passed" if csv_results and all(result.status == "passed" for result in csv_results)
                               else None)
                groups = []
                for group in response.groups:
                    if group.group_id == "structure" and file_status is not None:
                        status = (group.status if group.status == "incomplete" or
                                  (group.status == "findings" and file_status == "passed")
                                  else file_status)
                        group = group.model_copy(update={
                            "status": status,
                            "checker_ids": tuple(dict.fromkeys((*group.checker_ids, "csv_formula"))),
                        })
                    elif group.group_id == "prompt_security" and prompt_results:
                        # A finding in untrusted uploaded CSV must not leave the prompt
                        # security group showing Passed from the ordinary message alone.
                        status = ("incomplete" if group.status == "incomplete" or
                                  any(result.status not in {"passed", "flagged"}
                                      for result in prompt_results) else
                                  "findings" if group.status == "findings" or
                                  any(result.status == "flagged" for result in prompt_results) else
                                  group.status)
                        group = group.model_copy(update={
                            "status": status,
                            "checker_ids": tuple(dict.fromkeys((*group.checker_ids, "prompt_attack"))),
                        })
                    elif group.group_id == "sensitive_data" and pii_results:
                        # Recognized CSV PII uses the original sensitive-data group.
                        # File redaction cannot be represented as a sanitized file;
                        # report incomplete/Block instead of implying a safe preview.
                        redaction_unavailable = (record.action("pii", stage) == Action.REDACT and
                                                 any(result.status == "flagged" for result in pii_results))
                        status = ("incomplete" if group.status == "incomplete" or redaction_unavailable or
                                  any(result.status not in {"passed", "flagged"} for result in pii_results) else
                                  "findings" if group.status == "findings" or
                                  any(result.status == "flagged" for result in pii_results) else
                                  group.status)
                        group = group.model_copy(update={
                            "status": status,
                            "checker_ids": tuple(dict.fromkeys((*group.checker_ids, "pii"))),
                        })
                    elif group.group_id == "secrets" and secret_results:
                        # CSV credential findings belong to the same Secrets group
                        # as normal message findings, not the CSV Structure group.
                        status = ("incomplete" if group.status == "incomplete" or
                                  any(result.status not in {"passed", "flagged"}
                                      for result in secret_results) else
                                  "findings" if group.status == "findings" or
                                  any(result.status == "flagged" for result in secret_results) else
                                  group.status)
                        group = group.model_copy(update={
                            "status": status,
                            "checker_ids": tuple(dict.fromkeys((*group.checker_ids, "secrets"))),
                        })
                    groups.append(group)
                updated = response.model_copy(update={
                    "findings": response.findings + batch.findings,
                    "checker_results": response.checker_results + batch.checker_results,
                    "recommended_action": recommended,
                    "effective_action": (Action.BLOCK if batch.incomplete else
                                         Action.ALLOW if record.mode == "observe" else recommended),
                    "evaluation_status": "incomplete" if batch.incomplete or
                                         response.evaluation_status == "incomplete" else "complete",
                    "groups": tuple(groups),
                    "total_latency_ms": round((perf_counter() - started) * 1000, 3),
                })
                return JSONResponse({
                    **updated.model_dump(mode="json"),
                    # Receipts use server-computed byte metadata and fixed format reason codes only.
                    "attachments": list(batch.receipts),
                }, headers={"Cache-Control": "no-store"})
            except PackSelectionError:
                # Validation packs are not offered on tenant records: refuse the request (not counted).
                tenants.store.release_check(record.tenant_id, month)
                raise HTTPException(422, "Invalid or unavailable validation pack selection") from None
            except BaseException:
                # Only checks that return a result are counted.
                tenants.store.release_check(record.tenant_id, month)
                raise

    def tenant_summary(record: TenantRecord) -> dict:
        current = tenants.store.get(record.tenant_id) or record
        month = usage_month()
        return {"tenant_id": current.tenant_id, "display_name": current.display_name, "demo": current.demo,
                "revision": current.revision, "month": month, "used": current.used(month),
                "monthly_allowance": current.monthly_allowance, "overage": current.overage,
                "subscription_status": current.subscription_status or "unverified_legacy",
                "subscription_active": current.subscription_status == "active",
                # Each check's action on each stage (2026-11-04); no value = it does not run on that stage.
                "input_checks": {check_id: current.action(check_id, Stage.INPUT) for check_id in CHECK_IDS},
                "output_checks": {check_id: current.action(check_id, Stage.OUTPUT) for check_id in CHECK_IDS},
                "attachment_csv_mode": {"input": current.input_attachment_csv_mode,
                                        "output": current.output_attachment_csv_mode},
                "input_rule_actions": dict(sorted(current.input_rule_actions.items())),
                "output_rule_actions": dict(sorted(current.output_rule_actions.items())),
                # This record's own rules (its field comparisons and required sections), for this tenant only.
                "record_rules": list(record_rules(current).values()),
                # Only authenticated tenant-owned basic comparison definitions. No signed,
                # statistical, or host-held reference values are exposed here.
                "comparisons": [item.model_dump(mode="json") for item in current.comparisons],
                # Exact tenant-owned arithmetic pointers only: no host-signed reference values.
                "advanced_arithmetic": [item.model_dump(mode="json") for item in current.advanced_arithmetic],
                # Non-reference identity definitions can be edited by this tenant.
                # Do not expose trusted lot/scope sets in the general application summary.
                "advanced_identity": [item.model_dump(mode="json") for item in current.advanced_identity
                                      if item.kind in ("order_id_match", "serial_unique")],
                "advanced_identity_reference_status": [
                    {"rule_id": item.rule_id, "kind": item.kind,
                     "stages": [stage.value for stage in item.stages],
                     "reference_configured": bool(item.trusted_values)}
                    for item in current.advanced_identity
                    if item.kind in ("quarantine_exclusion", "authorized_scope_subset")
                ],
                # Expose editable non-FX conversion/time rules only; private FX rate,
                # timestamp and versioned tenant reference remain opaque.
                "advanced_conversion_time": [
                    item.model_dump(mode="json") for item in current.advanced_conversion_time
                    if item.kind != "currency_conversion_cap"
                ],
                "advanced_conversion_reference_status": [
                    {"rule_id": item.rule_id, "kind": item.kind,
                     "stages": [stage.value for stage in item.stages],
                     "reference_configured": all(value is not None for value in (
                         item.source_currency, item.destination_currency,
                         item.trusted_rate_numerator, item.trusted_rate_denominator,
                         item.rate_reference, item.rate_asof))}
                    for item in current.advanced_conversion_time
                    if item.kind == "currency_conversion_cap"
                ],
                # Read-only host-attested FC metadata for tenant dashboard discovery.
                # Reference values, source, signature and timestamps remain private.
                # Report the SAME runtime integrity gate used by the shared Engine.
                # Re-evaluate on each authenticated summary read: missing host key,
                # bad MAC, invalid values or expired references mean mandatory Block
                # when active. This is NOT an attestation of external source truth.
                # Never expose signature, source, timestamps or reference values.
                "advanced_trusted_reference_status": [
                    {"rule_id": item.rule_id, "kind": item.kind,
                     "stages": [stage.value for stage in item.stages],
                     "snapshot_present": True,
                     "runtime_integrity": ("accepted" if reference_is_trusted(
                         item, current.tenant_id) else "blocked")}
                    for item in current.advanced_trusted_reference
                ],
                 "sigma_rule_actions": dict(sorted(current.sigma_rule_actions.items()))}

    def tenant_only(principal: str | TenantRecord) -> TenantRecord:
        if not isinstance(principal, TenantRecord):
            raise HTTPException(403, "This endpoint needs a tenant API key")
        return principal

    def settings_owner(request: Request, principal: str | TenantRecord) -> TenantRecord:
        """Who may change a tenant's settings: a signed-in user of that tenant. A demo user's own demo key
        may change its demo record (demo users have no accounts). A customer's API key only runs checks,
        so a leaked production key cannot change settings or keys."""
        record = tenant_only(principal)
        if not record.demo and not getattr(request.state, "signed_in", False):
            raise HTTPException(403, "Sign in to change this tenant's settings; an API key only runs checks")
        return record

    def signed_in_owner(request: Request, principal: str | TenantRecord) -> TenantRecord:
        record = tenant_only(principal)
        if not getattr(request.state, "signed_in", False):
            raise HTTPException(403, "Sign in to manage API keys")
        return record

    def key_list(record: TenantRecord) -> dict:
        # A key is shown by the start of its hash only; the key itself is never stored.
        return {"keys": [{"key_id": digest[:12]} for digest in record.key_hashes], "max_keys": 2}

    def saved(update):
        try:
            record = update()
        except TenantConflict:
            raise HTTPException(409, "The tenant record changed; reload it and try again") from None
        except TenantNotFound:
            raise HTTPException(404, "Tenant not found") from None
        except NotADemoUser as error:
            raise HTTPException(403, str(error)) from None
        except ChangeRejected as error:
            raise HTTPException(422, str(error)) from None
        return JSONResponse(tenant_summary(record), headers={"Cache-Control": "no-store"})

    @api.get("/v1/tenants/current/statistical-drift")
    async def current_statistical_drift(
        request: Request,
        principal: Annotated[str | TenantRecord, Depends(authenticate)],
    ):
        # Private reference histograms are NOT included in the application
        # summary that a production evaluation key may read. Only the signed-in
        # settings owner (or a demo owner using its own demo key) can edit them.
        owner = settings_owner(request, principal)
        current = tenants.store.get(owner.tenant_id) or owner
        return JSONResponse({
            "revision": current.revision,
            "rules": [rule.model_dump(mode="json") for rule in current.advanced_statistical_drift],
        }, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @api.post("/v1/tenants/current/checks")
    async def change_checks(request: Request, body: CheckChanges,
                            principal: Annotated[str | TenantRecord, Depends(authenticate)]):
        record = settings_owner(request, principal)
        return saved(lambda: tenants.change_checks(record.tenant_id, body.changes, body.revision))

    @api.post("/v1/tenants/current/references")
    async def manage_tenant_references(request: Request, body: CheckChanges,
                                       principal: Annotated[str | TenantRecord, Depends(authenticate)]):
        # Owner-only API; production evaluation keys cannot write or read
        # private tenant reference definitions. Demo owner keys remain synthetic.
        record = settings_owner(request, principal)
        return saved(lambda: tenants.change_references(
            record.tenant_id, body.changes, body.revision))

    @api.post("/v1/tenants/current/reset")
    async def reset_checks(request: Request, principal: Annotated[str | TenantRecord, Depends(authenticate)]):
        record = settings_owner(request, principal)
        return saved(lambda: tenants.reset_demo(record.tenant_id))

    @api.get("/v1/tenants/current/keys")
    async def list_keys(request: Request, principal: Annotated[str | TenantRecord, Depends(authenticate)]):
        current = tenants.store.get(signed_in_owner(request, principal).tenant_id)
        return JSONResponse(key_list(current), headers={"Cache-Control": "no-store"})

    @api.post("/v1/tenants/current/keys")
    async def create_key(request: Request, principal: Annotated[str | TenantRecord, Depends(authenticate)]):
        current = tenants.store.get(signed_in_owner(request, principal).tenant_id)
        # Registration verifies identity only. Do not issue production
        # validation credentials until the trusted billing layer has activated
        # the tenant; an unverified legacy state is not an entitlement.
        if current.subscription_status != "active":
            raise HTTPException(402, "An active subscription is required to create an API key",
                                headers={"Cache-Control": "no-store"})
        if len(current.key_hashes) >= 2:
            raise HTTPException(409, "A tenant has at most 2 API keys; revoke one first")
        token, digest = new_key(current.tenant_id)
        try:
            updated = tenants.store.set_key_hashes(current.tenant_id, (*current.key_hashes, digest), current.revision)
        except TenantConflict:
            raise HTTPException(409, "The tenant record changed; reload it and try again") from None
        # The key is shown once, here; only its hash is stored.
        return JSONResponse({"api_key": token, "key_id": digest[:12], **key_list(updated)}, headers={"Cache-Control": "no-store"})

    @api.post("/v1/tenants/current/keys/revoke")
    async def revoke_key(request: Request, body: KeyRevocation,
                         principal: Annotated[str | TenantRecord, Depends(authenticate)]):
        current = tenants.store.get(signed_in_owner(request, principal).tenant_id)
        kept = tuple(digest for digest in current.key_hashes if digest[:12] != body.key_id)
        if len(kept) == len(current.key_hashes):
            raise HTTPException(404, "Key not found")
        try:
            updated = tenants.store.set_key_hashes(current.tenant_id, kept, current.revision)
        except TenantConflict:
            raise HTTPException(409, "The tenant record changed; reload it and try again") from None
        return JSONResponse(key_list(updated), headers={"Cache-Control": "no-store"})

    @api.post("/v1/billing/checkout")
    async def billing_checkout(
        session: Annotated[str | None, Header(alias=SESSION_HEADER)] = None,
    ):
        """Authenticated Checkout for an already provisioned Stripe billing account.

        Registration alone never establishes a Stripe customer binding, and
        a checkout redirect never activates validation entitlement.
        """
        from guardoath.billing import BillingMode
        from guardoath.stripe_checkout import StripeCheckoutError, create_checkout_session
        import secrets
        if sessions is None or tenants is None:
            raise HTTPException(404, "Customer billing is not available")
        if not session:
            raise HTTPException(401, "Sign in to subscribe")
        try:
            tenant_id = await run_in_threadpool(sessions.tenant_for, session)
        except SignInBusy:
            raise HTTPException(503, "Sign-in checks are busy; try again shortly") from None
        if tenant_id is None:
            raise HTTPException(401, "Invalid or expired sign-in")
        current = tenants.store.get(tenant_id)
        if current is None or current.demo or current.status != "active":
            raise HTTPException(403, "Register an account before subscribing")
        if current.subscription_status == "active":
            raise HTTPException(409, "Subscription already active; use billing management")
        if current.billing_mode != BillingMode.STRIPE or not current.payer_id:
            raise HTTPException(409, "Stripe customer provisioning is required before checkout")
        api_key = os.getenv("GUARDOATH_STRIPE_SECRET_KEY", "")
        price_id = os.getenv("GUARDOATH_STRIPE_PRICE_ID", "")
        if not api_key or not price_id:
            raise HTTPException(503, "Subscription checkout is not configured")
        try:
            url = await run_in_threadpool(create_checkout_session,
                customer_id=current.payer_id, price_id=price_id,
                tenant_id=current.tenant_id, api_key=api_key,
                success_url="https://guardoath.com/dashboard?checkout=success",
                cancel_url="https://guardoath.com/dashboard?checkout=cancel",
                idempotency_key="checkout-" + secrets.token_hex(24))
        except StripeCheckoutError:
            raise HTTPException(503, "Unable to start subscription checkout") from None
        return JSONResponse({"checkout_url": url}, headers={
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @api.post("/v1/billing/portal")
    async def billing_portal(
        session: Annotated[str | None, Header(alias=SESSION_HEADER)] = None,
    ):
        """Only the verified account owner may open their Stripe billing portal."""
        from guardoath.billing import BillingMode
        from guardoath.stripe_portal import StripePortalError, create_portal_session
        if sessions is None or tenants is None:
            raise HTTPException(404, "Customer billing is not available")
        if not session:
            raise HTTPException(401, "Sign in to manage billing")
        try:
            tenant_id = await run_in_threadpool(sessions.tenant_for, session)
        except SignInBusy:
            raise HTTPException(503, "Sign-in checks are busy; try again shortly") from None
        if tenant_id is None:
            raise HTTPException(401, "Invalid or expired sign-in")
        current = tenants.store.get(tenant_id)
        if current is None or current.demo or current.status != "active":
            raise HTTPException(403, "Register an account before managing billing")
        if current.billing_mode != BillingMode.STRIPE or not current.payer_id:
            raise HTTPException(409, "No Stripe billing account is associated with this tenant")
        api_key = os.getenv("GUARDOATH_STRIPE_SECRET_KEY", "")
        if not api_key:
            raise HTTPException(503, "Billing management is not configured")
        try:
            url = await run_in_threadpool(create_portal_session,
                customer_id=current.payer_id, api_key=api_key)
        except StripePortalError:
            raise HTTPException(503, "Unable to open billing management") from None
        return JSONResponse({"portal_url": url}, headers={
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @api.get("/v1/account/status")
    async def account_status(
        session: Annotated[str | None, Header(alias=SESSION_HEADER)] = None,
    ):
        """Account identity and entitlement state, never a payment assertion.

        Available before tenant registration, but only for a verified Cognito
        session. The browser must not infer subscription access from OAuth.
        """
        if sessions is None or tenants is None:
            raise HTTPException(404, "Customer sign-in is not available")
        if not session:
            raise HTTPException(401, "Sign in to view account status")
        try:
            tenant_id = await run_in_threadpool(sessions.tenant_for, session)
        except SignInBusy:
            raise HTTPException(503, "Sign-in checks are busy; try again shortly",
                                headers={"Retry-After": "5"}) from None
        if tenant_id is None:
            raise HTTPException(401, "Invalid or expired sign-in")
        record = tenants.store.get(tenant_id)
        registered = record is not None and record.status == "active" and not record.demo
        return JSONResponse({
            "registered": registered,
            "registration_enabled": bool(sessions.registration_enabled),
            "tenant_id": tenant_id if registered else None,
            "display_name": record.display_name if registered else None,
            "subscription_status": (record.subscription_status or "unverified_legacy")
                                   if registered else "not_registered",
            "can_validate": bool(registered and record.subscription_status == "active"),
        }, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @api.post("/v1/register")
    async def register_account(
        session: Annotated[str | None, Header(alias=SESSION_HEADER)] = None,
    ):
        """Explicit registration creates an account, never a paid entitlement."""
        if sessions is None or tenants is None or not sessions.registration_enabled:
            raise HTTPException(404, "Registration is not available")
        if not session:
            raise HTTPException(401, "Sign in before registering")
        try:
            tenant_id = await run_in_threadpool(sessions.tenant_for, session)
        except SignInBusy:
            raise HTTPException(503, "Registration is busy; try again shortly",
                                headers={"Retry-After": "5"}) from None
        if tenant_id is None:
            raise HTTPException(401, "A verified identity-provider sign-in is required")
        if tenants.store.get(tenant_id) is not None:
            raise HTTPException(409, "An account is already registered")
        # Never accept browser-supplied subscription status, tenant ID,
        # billing claims, validation allowance or API keys.
        registered = TenantRecord(
            tenant_id=tenant_id, display_name="My GuardOath account",
            demo=False, status="active", key_hashes=(),
            monthly_allowance=0, overage="block",
            subscription_status="inactive",
            # Tenant records require a concrete check at creation. This safe,
            # local-only baseline preserves that invariant for unpaid accounts
            # without granting checks or a paid entitlement.
            input_check_text_security="block",
            output_check_text_security="block",
        )
        try:
            registered = await run_in_threadpool(tenants.store.create, registered)
        except TenantConflict:
            raise HTTPException(409, "An account is already registered") from None
        return JSONResponse({"tenant_id": registered.tenant_id,
                             "subscription_status": "inactive",
                             "can_validate": False,
                             "next_step": "Choose a subscription to activate checks"},
                            status_code=201, headers={"Cache-Control": "no-store"})

    @api.get("/v1/sign-in")
    async def sign_in_config():
        # Public: where the dashboard sends a user to sign in. The app client has no secret.
        if sessions is None:
            raise HTTPException(404, "Tenant sign-in is not available")
        return JSONResponse(sessions.public_config(), headers={"Cache-Control": "no-store"})

    @api.post("/v1/sign-in/token")
    async def sign_in_token(body: SignInCode):
        if sessions is None:
            raise HTTPException(404, "Tenant sign-in is not available")
        try:
            result = await run_in_threadpool(sessions.exchange, body.code, body.code_verifier)
        except SignInBusy:
            raise HTTPException(503, "Sign-in checks are busy; try again shortly", headers={"Retry-After": "5"}) from None
        if result is None:
            raise HTTPException(401, "Sign-in failed; try again")
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @api.get("/v1/demo-users")
    async def demo_users():
        users = tenants.demo_users() if tenants is not None else []
        if not users:
            raise HTTPException(404, "Demo users are not available")
        return JSONResponse({"demo_users": users}, headers={"Cache-Control": "no-store"})

    @api.post("/v1/demo-users/{tenant_id}/key")
    async def demo_user_key(tenant_id: Annotated[str, PathParam(pattern=TENANT_ID_PATTERN)]):
        token = tenants.demo_key(tenant_id) if tenants is not None else None
        if token is None:
            raise HTTPException(404, "Demo user not found")
        return JSONResponse({"tenant_id": tenant_id, "api_key": token}, headers={"Cache-Control": "no-store"})

    # Built once: the registry is description only, and the same for every caller.
    controls_body = registry_payload(checkers)

    @api.get("/v1/controls")
    async def controls():
        # Public: it describes the controls a tenant can turn on. It holds no tenant data and decides
        # nothing; each tenant's record alone decides which controls run and with what action.
        return JSONResponse(controls_body, headers={"Cache-Control": "public, max-age=300"})

    @api.get("/healthz")
    async def health():
        # Public immutable build identity, not tenant data or deployment secrets.
        # A missing/invalid identity is explicitly unknown, never guessed.
        return JSONResponse(
            {"status": "ok", "profile": "local-prototype",
             "source_sha": release_source_sha()},
            headers={"Cache-Control": "no-store"})

    @api.get("/v1/applications/current")
    async def current_application(principal: Annotated[str | TenantRecord, Depends(authenticate)]):
        if isinstance(principal, TenantRecord):
            current = tenants.store.get(principal.tenant_id) or principal
            return {"application_id": current.tenant_id, "policies": tenants.engine_for(current).describe_policies(current.tenant_id),
                    "resource_limits": {"scope": "server_process", **resource_limits.model_dump()},
                    "groups": [{"group_id": gid, "name": name, "checker_ids": ids, "limitations": limits}
                               for gid, name, ids, limits in GROUPS],
                    "tenant": tenant_summary(current)}
        app_id = principal
        return {"application_id": app_id, "policies": service.describe_policies(app_id),
                "resource_limits": {"scope": "server_process", **resource_limits.model_dump()},
                "groups": [{"group_id": gid, "name": name, "checker_ids": ids, "limitations": limits}
                           for gid, name, ids, limits in GROUPS]}

    @api.get("/v1/validation-packs")
    async def validation_packs(principal: Annotated[str | TenantRecord, Depends(authenticate)]):
        if isinstance(principal, TenantRecord):
            return {"application_id": principal.tenant_id, "mandatory": "CORE", "available_packs": []}
        app_id = principal
        return {"application_id": app_id, "mandatory": "CORE",
                "available_packs": service.describe_validation_packs(app_id)}

    @api.post("/v1/checks/input", response_model=CheckResponse)
    async def check_input(request: ApiCheckRequest, principal: Annotated[str | TenantRecord, Depends(authenticate)]):
        return await assess(request, principal, Stage.INPUT)

    @api.post("/v1/checks/output", response_model=CheckResponse)
    async def check_output(request: ApiCheckRequest, principal: Annotated[str | TenantRecord, Depends(authenticate)]):
        return await assess(request, principal, Stage.OUTPUT)

    @api.post("/v1/checks/input/attachments")
    async def check_input_attachments(
        request: AttachmentCheckRequest,
        principal: Annotated[str | TenantRecord, Depends(authenticate)],
    ):
        if not isinstance(principal, TenantRecord):
            raise HTTPException(403, "Attachments require an authenticated tenant")
        return await assess_tenant(request, principal, Stage.INPUT, request.attachment_bytes())

    @api.post("/v1/checks/output/attachments")
    async def check_output_attachments(
        request: AttachmentCheckRequest,
        principal: Annotated[str | TenantRecord, Depends(authenticate)],
    ):
        if not isinstance(principal, TenantRecord):
            raise HTTPException(403, "Attachments require an authenticated tenant")
        return await assess_tenant(request, principal, Stage.OUTPUT, request.attachment_bytes())

    return api


app = create_app()
