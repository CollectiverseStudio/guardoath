"""Local development API. Credentials bind requests to one application."""

import hmac
import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import Action, ApiCheckRequest, CheckRequest, CheckResponse, Policy, Rule, Stage
from guardoath.ingress import JsonIngressLimits
from guardoath.groups import GROUPS
from guardoath.budgets import ResourceLimits, RequestBudgets, RequestCapacity
from guardoath.validation_packs import PackSelectionError, load_pack_registry
from guardoath.tenants import TenantRegistry
from guardoath.hosted import ChangeRejected, CheckChanges, NotADemoUser, TenantService
from guardoath.tenant_record import TENANT_ID_PATTERN, TenantRecord, usage_month
from guardoath.tenant_store import TenantConflict, TenantNotFound, UsageLimitReached


def demo_engine(enable_pii: bool = False, enable_azure: bool = False, content_safety_provider: str = "none") -> Engine:
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
    )], default_checkers(enable_pii=enable_pii, enable_azure=enable_azure, content_safety_provider=content_safety_provider))


def create_app(engine: Engine | None = None, credentials: dict[str, str] | None = None,
               limits: ResourceLimits | None = None,
               tenant_registry: TenantRegistry | None = None,
               tenants: TenantService | None = None) -> FastAPI:
    policy_file = os.getenv("GUARDOATH_POLICY_FILE")
    pack_file = os.getenv("GUARDOATH_PACK_FILE")
    if engine is not None:
        if pack_file:
            raise ValueError("Pack registry must be configured on the supplied engine")
        service = engine
    else:
        pii_setting = os.getenv("GUARDOATH_ENABLE_PII", "0")
        if pii_setting not in {"0", "1"}:
            raise ValueError("GUARDOATH_ENABLE_PII must be 0 or 1")
        enable_pii = pii_setting == "1"
        azure_setting = os.getenv("GUARDOATH_ENABLE_AZURE_PROMPT_SHIELDS", "0")
        if azure_setting not in {"0", "1"}:
            raise ValueError("GUARDOATH_ENABLE_AZURE_PROMPT_SHIELDS must be 0 or 1")
        enable_azure = azure_setting == "1"
        content_safety_provider = os.getenv("GUARDOATH_CONTENT_SAFETY_PROVIDER", "none")
        checkers = default_checkers(enable_pii=enable_pii, enable_azure=enable_azure, content_safety_provider=content_safety_provider)
        service = (Engine(load_policies(policy_file), checkers, pack_registry=load_pack_registry(pack_file) if pack_file else None)
                   if policy_file else demo_engine(enable_pii=enable_pii, enable_azure=enable_azure, content_safety_provider=content_safety_provider))
        if tenants is None:
            tenants = TenantService.from_environment(checkers)
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
    budget_apps = set(bindings.values()) if tenant_registry is None else set(tenant_registry.application_ids())
    budgets = RequestBudgets(resource_limits, budget_apps)
    api = FastAPI(title="GuardOath", version="0.25.0")
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
    ) -> str | TenantRecord:
        if not bindings and tenant_registry is None and tenants is None:
            raise HTTPException(503, "Application credentials are not configured")
        token = auth.credentials if auth else ""
        if tenants is not None and token.startswith("gok_"):
            record = tenants.authenticate(token)
            if record is None:
                raise HTTPException(401, "Invalid application credential", headers={"WWW-Authenticate": "Bearer"})
            budgets.register(record.tenant_id)
            return record
        if tenant_registry is not None:
            application = tenant_registry.authenticate(auth.credentials if auth else "")
            if application is not None:
                return application.application_id
            raise HTTPException(401, "Invalid application credential", headers={"WWW-Authenticate": "Bearer"})
        if auth is not None:
            for token, app_id in bindings.items():
                if hmac.compare_digest(auth.credentials.encode(), token.encode()):
                    return app_id
        raise HTTPException(401, "Invalid application credential", headers={"WWW-Authenticate": "Bearer"})

    async def assess(request: ApiCheckRequest, principal: str | TenantRecord, stage: Stage):
        if isinstance(principal, TenantRecord):
            return await assess_tenant(request, principal, stage)
        # Only a tenant record has a default profile; every other key must name one.
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

    async def assess_tenant(request: ApiCheckRequest, record: TenantRecord, stage: Stage):
        if request.application_id != record.tenant_id:
            raise HTTPException(403, "Credential does not authorize this application")
        # No profile named: the record's default profile.
        version = request.policy_version or record.default_profile
        if version not in tenants.profile_versions(record):
            raise HTTPException(404, "Policy version not found")
        request = CheckRequest.model_validate({**request.model_dump(), "policy_version": version})
        engine = tenants.engine_for(record)
        with budgets.check(record.tenant_id):
            month = usage_month()
            try:
                tenants.store.count_check(record.tenant_id, month)
            except UsageLimitReached:
                raise HTTPException(429, "Monthly check limit reached", headers={"Cache-Control": "no-store"}) from None
            try:
                return await engine.check(request, stage)
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
                "default_profile": current.default_profile,
                "checks": {check_id[6:]: getattr(current, check_id) for check_id in type(current).model_fields
                           if check_id.startswith("check_")}}

    def tenant_only(principal: str | TenantRecord) -> TenantRecord:
        if not isinstance(principal, TenantRecord):
            raise HTTPException(403, "This endpoint needs a tenant API key")
        return principal

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

    @api.post("/v1/tenants/current/checks")
    async def change_checks(body: CheckChanges, principal: Annotated[str | TenantRecord, Depends(authenticate)]):
        record = tenant_only(principal)
        return saved(lambda: tenants.change_checks(record.tenant_id, body.changes, body.revision))

    @api.post("/v1/tenants/current/reset")
    async def reset_checks(principal: Annotated[str | TenantRecord, Depends(authenticate)]):
        record = tenant_only(principal)
        return saved(lambda: tenants.reset_demo(record.tenant_id))

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

    @api.get("/healthz")
    async def health():
        return {"status": "ok", "profile": "local-prototype"}

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

    return api


app = create_app()
