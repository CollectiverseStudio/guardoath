"""GuardOath admin Phase A: guarded, read-only tenant management slice.

Not enabled by simply importing. Production integration must inject a VERIFIED
Cognito identity validator; no JWT claim, user header, or application key is
accepted as proof of administrative authority by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Callable, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from guardoath.admin_actions import AdminActionConflict, AdminActionUnavailable, AdminTenantActions
from guardoath.admin_billing import AdminSubscriptionReporter, BillingUnavailable
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from guardoath.admin_catalog import CatalogProvider, read_catalog
from guardoath.admin_directory import DynamoAdminDirectory, InvalidCursor
from guardoath.admin_usage import tenant_usage_report
from guardoath.admin_users import CognitoUserDirectory, InvalidUserCursor


@dataclass(frozen=True)
class VerifiedAdmin:
    """Produced exclusively by a server-side Cognito verification adapter.

    The adapter must verify signature, issuer, client, token use, expiration,
    revocation/active account, Cognito administrator group from a trusted
    provider source, and MFA; never copy these values from unverified claims.
    """
    subject: str
    roles: frozenset[str]
    mfa_verified: bool


class TenantStore(Protocol):
    def tenant_ids(self) -> tuple[str, ...]: ...
    def get(self, tenant_id: str): ...


class TenantRuleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1,strict=True)
    stage: str = Field(pattern=r"^(input|output)$")
    rule_id: str = Field(min_length=3,max_length=100)
    action: str = Field(pattern=r"^(off|allow|flag|block|same_as_check)$")
    confirmation: str = Field(min_length=2,max_length=40)
    reason: str = Field(min_length=10,max_length=240)
    idempotency_key: str = Field(min_length=16,max_length=96)


class AllowanceCap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1,strict=True)
    new_limit: int = Field(ge=0,le=1_000_000_000,strict=True)
    confirmation: str = Field(min_length=2,max_length=40)
    reason: str = Field(min_length=10,max_length=240)
    idempotency_key: str = Field(min_length=16,max_length=96)


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)
    status: str = Field(pattern=r"^(active|disabled)$")
    confirmation: str = Field(min_length=2, max_length=40)
    reason: str = Field(min_length=10, max_length=240)
    idempotency_key: str = Field(min_length=16, max_length=96)


Verifier = Callable[[str], VerifiedAdmin | None]
ADMIN_ROLES = frozenset({"owner"})  # Other roles require scoped authorization before enablement.
TENANT_ID = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")
PAGE_LIMIT = 50
# CloudFront replaces Authorization with its AWS origin signature. Hosted clients
# use a dedicated session header; never treat API keys as administrator sessions.
ADMIN_SESSION_HEADER = "X-GuardOath-Admin-Session"


def _safe_record(record) -> dict:
    """Strict field allowlist; never serializes API key hashes, raw data or secrets."""
    return {
        "tenant_id": str(record.tenant_id),
        "display_name": str(record.display_name),
        "status": str(getattr(record.status, "value", record.status)),
        "demo": bool(record.demo),
        "subscription_status": str(getattr(record, "subscription_status", "unknown")),
        "monthly_allowance": int(record.monthly_allowance),
        # Count only; plaintext keys, hashes and identifiers never leave the tenant store.
        "api_key_count": (len(record.key_hashes) if isinstance(getattr(record, "key_hashes", None), (tuple, list))
                          and len(record.key_hashes) <= 2 else None),
        "subscription_source": "persisted tenant record; not live Stripe verification",
        "usage_month": datetime.now(timezone.utc).strftime("%Y-%m"),
        "usage_month_count": getattr(record, "usage", {}).get(datetime.now(timezone.utc).strftime("%Y-%m")),
        "revision": int(record.revision),
        "updated_at": str(record.updated_at),
    }


def create_admin_router(*, verify_cognito_admin: Verifier | None = None,
                        tenant_store: TenantStore | None = None,
                        admin_directory: DynamoAdminDirectory | None = None,
                        admin_user_directory: CognitoUserDirectory | None = None,
                        catalog_provider: CatalogProvider | None = None,
                        admin_mutator: AdminTenantActions | None = None,
                        browser_entry_enabled: bool = False,
                        billing_reporter: AdminSubscriptionReporter | None = None,
                        record_validator=None, rule_exists=None) -> APIRouter:
    """Build guarded admin routes; absent verifier => no admin access.

    The verifier MUST perform real independent Cognito checks; test stubs only
    belong in tests. No privilege inferred from email, custom tenant field,
    browser flags or a JWT group string without verification.
    """
    router = APIRouter()

    def require_admin(request: Request) -> VerifiedAdmin:
        # The hosted CloudFront origin uses Authorization for AWS SigV4. A
        # distinct, forwarded header carries the verified Cognito access token;
        # never accept application API keys as admin identities.
        sessions = request.headers.getlist(ADMIN_SESSION_HEADER)
        bearer = request.headers.getlist("Authorization")
        # Behind CloudFront OAC, Authorization is *always* an AWS SigV4
        # origin signature. Accept that transport header alongside the admin
        # session, but reject a second client-supplied Bearer credential.
        if len(sessions) > 1 or len(bearer) > 1:
            raise HTTPException(status_code=401, detail="Administrator authentication required")
        if sessions and bearer and not bearer[0].startswith("AWS4-HMAC-SHA256 "):
            raise HTTPException(status_code=401, detail="Administrator authentication required")
        if sessions:
            token = sessions[0]
        elif bearer and bearer[0].startswith("Bearer "):
            # Local direct-to-app testing only; the cloud SigV4 Authorization
            # signature can never match this scheme.
            token = bearer[0][7:]
        else:
            raise HTTPException(status_code=401, detail="Administrator authentication required")
        if not token or len(token) > 8192 or any(ch.isspace() for ch in token):
            raise HTTPException(status_code=401, detail="Administrator authentication required")
        if verify_cognito_admin is None:
            raise HTTPException(status_code=503, detail="Administration unavailable")
        try:
            principal = verify_cognito_admin(token)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Administration identity verification unavailable") from exc
        if principal is None:
            raise HTTPException(status_code=401, detail="Administrator authentication required")
        if not isinstance(principal, VerifiedAdmin) or not principal.subject or not principal.mfa_verified:
            raise HTTPException(status_code=403, detail="Administrative authorization denied")
        if not principal.roles.intersection(ADMIN_ROLES):
            raise HTTPException(status_code=403, detail="Administrative authorization denied")
        return principal

    def admin_json(data: dict) -> JSONResponse:
        return JSONResponse(data, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    if browser_entry_enabled:
        @router.get("/admin", include_in_schema=False)
        def admin_entry():
            # This route discloses no administrator data. The login page is a
            # public shell; real API privileges remain server-side.
            return RedirectResponse("/admin/login", status_code=303,
                                    headers={"Cache-Control":"no-store"})
    else:
        @router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
        def admin_page(principal: VerifiedAdmin = Depends(require_admin)):
            return HTMLResponse(_PAGE, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
                                                "Referrer-Policy": "no-referrer"})

    @router.get("/v1/admin/features")
    def available_admin_features(principal: VerifiedAdmin = Depends(require_admin)):
        return admin_json({"tenant_status_changes": admin_mutator is not None,
                           "billing_changes": False, "billing_verify": billing_reporter is not None,
                           "tenant_rule_changes": admin_mutator is not None and callable(record_validator) and callable(rule_exists),
                           "monthly_cost_caps": admin_mutator is not None and callable(record_validator),
                           "rule_publishing": False,
                           "audit_required_for_writes": True})

    @router.get("/v1/admin/overview")
    def overview(principal: VerifiedAdmin = Depends(require_admin)):
        # Data not sourced from actual billing/provider systems must be unknown,
        # not an invented 0 or fabricated live revenue/cost metric.
        return admin_json({"status": "read_only", "metrics": {
            "users": {"value": None, "source": "Cognito", "status": "unavailable"},
            "active_subscriptions": {"value": None, "source": "Stripe", "status": "unavailable"},
            "revenue_usd": {"value": None, "source": "Stripe", "status": "unavailable"},
            "aws_cost_usd": {"value": None, "source": "AWS billing", "status": "unavailable"},
        }})

    @router.get("/v1/admin/users")
    def cognito_users(query: str = Query(default="", max_length=80),
                      limit: int = Query(default=20, ge=1, le=50),
                      cursor: str | None = Query(default=None, max_length=4096),
                      principal: VerifiedAdmin = Depends(require_admin)):
        # Provider enumeration requires freshly verified owner+MFA, never a tenant token.
        if admin_user_directory is None:
            raise HTTPException(status_code=503, detail="Cognito user directory unavailable")
        try:
            page = admin_user_directory.page(limit=limit, query=query, cursor=cursor)
        except (InvalidUserCursor, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid user directory request") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Cognito user directory unavailable") from exc
        return admin_json(page)

    @router.get("/v1/admin/tenants")
    def tenants(search: str = Query(default="", max_length=40),
                offset: int = Query(default=0, ge=0, le=1_000_000),
                limit: int = Query(default=20, ge=1, le=PAGE_LIMIT),
                cursor: str | None = Query(default=None, max_length=4096),
                principal: VerifiedAdmin = Depends(require_admin)):
        if admin_directory is not None:
            try:
                result = admin_directory.page(limit=limit, search=search, cursor=cursor)
            except InvalidCursor as exc:
                raise HTTPException(status_code=422, detail="Invalid pagination cursor") from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid directory request") from exc
            except Exception as exc:
                raise HTTPException(status_code=503, detail="Tenant directory unavailable") from exc
            return admin_json(result)
        if cursor is not None:
            raise HTTPException(status_code=422, detail="Cursor pagination unavailable")
        if tenant_store is None:
            return admin_json({"status": "unavailable", "tenants": [], "total": None,
                               "source": "tenant store"})
        if search and not re.fullmatch(r"[a-zA-Z0-9_-]+", search):
            raise HTTPException(status_code=422, detail="Invalid tenant filter")
        try:
            ids = sorted(tid for tid in tenant_store.tenant_ids() if not search or search.lower() in tid.lower())
            selected = [_safe_record(record) for tid in ids[offset:offset + limit]
                        if (record := tenant_store.get(tid)) is not None]
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Tenant directory unavailable") from exc
        return admin_json({"status": "ok", "source": "tenant store", "tenants": selected,
                           "total": len(ids), "offset": offset, "limit": limit})

    @router.get("/v1/admin/tenants/{tenant_id}")
    def tenant_detail(tenant_id: str, principal: VerifiedAdmin = Depends(require_admin)):
        if not TENANT_ID.fullmatch(tenant_id):
            raise HTTPException(status_code=404, detail="Tenant not found")
        if tenant_store is None:
            raise HTTPException(status_code=503, detail="Tenant directory unavailable")
        try:
            record = tenant_store.get(tenant_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Tenant directory unavailable") from exc
        if record is None:
            raise HTTPException(status_code=404, detail="Tenant not found")
        return admin_json({"tenant": _safe_record(record), "source": "tenant store"})

    @router.get("/v1/admin/tenants/{tenant_id}/usage")
    def usage_detail(tenant_id: str, principal: VerifiedAdmin = Depends(require_admin)):
        if not TENANT_ID.fullmatch(tenant_id):
            raise HTTPException(status_code=404, detail="Tenant not found")
        if tenant_store is None:
            raise HTTPException(status_code=503, detail="Usage reporting unavailable")
        try:
            record = tenant_store.get(tenant_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Tenant not found")
            report = tenant_usage_report(record)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Usage reporting unavailable") from exc
        return admin_json({"status": "ok", "usage": report})

    @router.get("/v1/admin/subscriptions/{tenant_id}")
    def read_verified_subscription(tenant_id: str, principal: VerifiedAdmin = Depends(require_admin)):
        if not TENANT_ID.fullmatch(tenant_id):
            raise HTTPException(status_code=404, detail="Tenant not found")
        if tenant_store is None or billing_reporter is None:
            raise HTTPException(status_code=503, detail="Subscription verification unavailable")
        try:
            record = tenant_store.get(tenant_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Tenant not found")
            result = billing_reporter.read(record)
        except HTTPException:
            raise
        except BillingUnavailable as exc:
            raise HTTPException(status_code=503, detail="Subscription verification unavailable") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Subscription verification unavailable") from exc
        return admin_json({"tenant_id":tenant_id, "subscription":result})

    @router.get("/v1/admin/catalog")
    def catalog(category_id: str | None = Query(default=None, min_length=1, max_length=60,
                                                pattern=r"^[a-z][a-z0-9_]*$"),
                principal: VerifiedAdmin = Depends(require_admin)):
        if catalog_provider is None:
            return admin_json({"status": "unavailable", "source": "published GuardOath control registry",
                               "categories": [], "total_categories": None})
        try:
            result = read_catalog(catalog_provider, domain_id=category_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Published catalog unavailable") from exc
        return admin_json({"status": "ok", **result})

    if admin_mutator is not None:
        @router.post("/v1/admin/tenants/{tenant_id}/status")
        def change_tenant_status(tenant_id: str, payload: StatusChange, request: Request,
                                 principal: VerifiedAdmin = Depends(require_admin)):
            # Browser admin tokens use a non-simple custom header. Enforce origin
            # on all writes as defense in depth against cross-site attempts.
            origin = request.headers.get("origin")
            if origin != "https://guardoath.com" and not re.fullmatch(
                    r"http://(?:localhost|127\.0\.1):\d+", origin or ""):
                raise HTTPException(status_code=403, detail="Admin request origin refused")
            if not TENANT_ID.fullmatch(tenant_id):
                raise HTTPException(status_code=404, detail="Tenant not found")
            try:
                result = admin_mutator.change_status(
                    tenant_id=tenant_id, expected_revision=payload.expected_revision,
                    status=payload.status, confirmation=payload.confirmation,
                    reason=payload.reason, actor=principal.subject,
                    idempotency_key=payload.idempotency_key)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid administrative action") from exc
            except AdminActionConflict as exc:
                raise HTTPException(status_code=409, detail="Tenant status changed; refresh first") from exc
            except AdminActionUnavailable as exc:
                raise HTTPException(status_code=503, detail="Audited administration unavailable") from exc
            return admin_json(result)

    if admin_mutator is not None and callable(record_validator) and callable(rule_exists):
        @router.post("/v1/admin/tenants/{tenant_id}/rule-action")
        def change_rule_action(tenant_id: str, payload: TenantRuleChange, request: Request,
                               principal: VerifiedAdmin = Depends(require_admin)):
            origin = request.headers.get("origin")
            if origin != "https://guardoath.com" and not re.fullmatch(
                    r"http://(?:localhost|127\.0\.1):\d+",origin or ""):
                raise HTTPException(status_code=403,detail="Admin request origin refused")
            try:
                result=admin_mutator.change_rule_action(
                    tenant_id=tenant_id,expected_revision=payload.expected_revision,
                    stage=payload.stage,rule_id=payload.rule_id,action=payload.action,
                    confirmation=payload.confirmation,reason=payload.reason,
                    actor=principal.subject,idempotency_key=payload.idempotency_key,
                    validate_record=record_validator,rule_exists=rule_exists)
            except ValueError as exc:
                raise HTTPException(status_code=422,detail="Invalid rule action request") from exc
            except AdminActionConflict as exc:
                raise HTTPException(status_code=409,detail="Tenant or rule state changed; refresh") from exc
            except AdminActionUnavailable as exc:
                raise HTTPException(status_code=503,detail="Audited rule change unavailable") from exc
            return admin_json(result)

    if admin_mutator is not None and callable(record_validator):
        @router.post("/v1/admin/tenants/{tenant_id}/allowance-cap")
        def cap_allowance(tenant_id:str, payload:AllowanceCap, request:Request,
                          principal:VerifiedAdmin=Depends(require_admin)):
            origin=request.headers.get("origin")
            if origin!="https://guardoath.com" and not re.fullmatch(
                    r"http://(?:localhost|127\.0\.1):\d+",origin or ""):
                raise HTTPException(status_code=403,detail="Admin request origin refused")
            try:
                result=admin_mutator.cap_monthly_allowance(
                    tenant_id=tenant_id,expected_revision=payload.expected_revision,
                    new_limit=payload.new_limit,confirmation=payload.confirmation,
                    reason=payload.reason,actor=principal.subject,
                    idempotency_key=payload.idempotency_key,
                    validate_record=record_validator)
            except ValueError as exc:
                raise HTTPException(status_code=422,detail="Invalid cost cap request") from exc
            except AdminActionConflict as exc:
                raise HTTPException(status_code=409,detail="Tenant state changed; refresh") from exc
            except AdminActionUnavailable as exc:
                raise HTTPException(status_code=503,detail="Audited cost control unavailable") from exc
            return admin_json(result)

    return router


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>GuardOath Admin — Read-only</title><style>
:root{font-family:system-ui,-apple-system,sans-serif;color:#182535;background:#f5f7fb}body{margin:0}header{background:#102337;color:white;padding:22px 6%}main{max-width:1100px;margin:36px auto;padding:0 24px}h1{margin:0 0 4px}p{line-height:1.5}.label{color:#b6cadb;font-size:14px}.panel{background:white;border:1px solid #dbe3ec;border-radius:12px;padding:24px;margin:18px 0}.status{font-weight:600;color:#8a4800}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.metric{border:1px solid #dbe3ec;border-radius:8px;padding:16px}.metric strong{display:block;font-size:23px;margin:10px 0}.metric small{color:#526376}a{color:#174c85}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:12px;border-bottom:1px solid #e5eaf0}@media(max-width:650px){table{display:block;overflow-x:auto}main{margin-top:18px}}
</style></head><body><header><h1>GuardOath Administration</h1><div class="label">Operator console · Phase A · Read-only</div></header><main>
<section class="panel"><h2>Operational overview</h2><p class="status">This first build does not claim real-time billing or cost data.</p><div class="grid"><div class="metric">Users<strong>Unavailable</strong><small>Cognito integration pending</small></div><div class="metric">Subscriptions<strong>Unavailable</strong><small>Stripe provider reconciliation pending</small></div><div class="metric">Revenue<strong>Unavailable</strong><small>Stripe reporting pending</small></div><div class="metric">AWS costs<strong>Unavailable</strong><small>AWS billing integration pending</small></div></div></section>
<section class="panel"><h2>Tenants</h2><p>Read-only tenant details are available through authenticated <code>/v1/admin/tenants</code>. No credentials or data are embedded in this HTML document.</p></section>
<section class="panel"><h2>Rules and categories</h2><p>Versioned catalog editing and publishing are deferred until server-side audit, role separation and rollback are implemented.</p></section>
</main></body></html>"""
