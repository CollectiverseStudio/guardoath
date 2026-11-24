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
from guardoath.admin_actions import AdminActionConflict, AdminActionUnavailable, AdminTenantActions, AdminUnboundZeroCap
from guardoath.admin_audit import AdminAuditReader, AdminSensitiveReadAuditor, AdminAuditUnavailable, EVENT
from guardoath.admin_billing import AdminSubscriptionReporter, BillingUnavailable
from guardoath.admin_billing_reconcile import AdminBillingDowngrade, BillingDowngradeConflict, BillingDowngradeUnavailable
from guardoath.admin_subscription_reconcile import (
    AdminSubscriptionReconciliation, SubscriptionConflict, SubscriptionUnavailable)
from guardoath.admin_invoices import AdminInvoiceHistory
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from guardoath.admin_catalog import CatalogProvider, read_catalog
from guardoath.admin_catalog_history import verified_catalog_history, CatalogHistoryUnavailable
from guardoath.admin_catalog_drafts import AdminCatalogDrafts, DraftConflict, DraftUnavailable
from guardoath.admin_catalog_preview import (
    CatalogPreviewConflict, CatalogPreviewUnavailable, preview_rule_description)
from guardoath.admin_catalog_manifest import (
    CatalogManifestConflict, CatalogManifestUnavailable, reviewed_metadata_manifest)
from guardoath.admin_directory import DynamoAdminDirectory, DirectoryUnavailable, InvalidCursor, saved_utc_timestamp
from guardoath.admin_usage import tenant_usage_report
from guardoath.admin_rule_inspection import (
    inspect_tenant_rule_action, RuleSelectionInvalid, RuleInspectionUnavailable)
from guardoath.admin_user_actions import (AdminCognitoUserActions, UserActionConflict, UserActionUnavailable)
from guardoath.admin_users import (CognitoUserDirectory, InvalidUserCursor,
                                  InvalidUserLookup, CognitoDirectoryUnavailable, CognitoLookupRateLimited)


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


# The browser and signed audit receipt must represent BOTH current and next revision exactly.
MAX_MUTABLE_REVISION = (1 << 53) - 2
# The owner browser must also read tenant revisions and usage without rounding.
MAX_BROWSER_SAFE_INTEGER = (1 << 53) - 1


class TenantRuleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, le=MAX_MUTABLE_REVISION, strict=True)
    stage: str = Field(pattern=r"^(input|output)$")
    rule_id: str = Field(min_length=3,max_length=100)
    action: str = Field(pattern=r"^(off|allow|flag|block|same_as_check)$")
    confirmation: str = Field(min_length=2,max_length=40)
    reason: str = Field(min_length=10,max_length=240)
    idempotency_key: str = Field(min_length=16,max_length=96)


class AllowanceCap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, le=MAX_MUTABLE_REVISION, strict=True)
    new_limit: int = Field(ge=0,le=1_000_000_000,strict=True)
    confirmation: str = Field(min_length=2,max_length=40)
    reason: str = Field(min_length=10,max_length=240)
    idempotency_key: str = Field(min_length=16,max_length=96)


class RevokeTenantKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, le=MAX_MUTABLE_REVISION, strict=True)
    confirmation: str = Field(min_length=2, max_length=40)
    reason: str = Field(min_length=10, max_length=240)
    idempotency_key: str = Field(min_length=16, max_length=96)


class ReconcileUnpaidSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, le=MAX_MUTABLE_REVISION, strict=True)
    confirmation: str = Field(min_length=12, max_length=55)
    reason: str = Field(min_length=10, max_length=240)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,96}$", max_length=96)


class CatalogRuleDescriptionPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_registry_version: str = Field(pattern=r"^[1-9][0-9]{0,8}$", max_length=9)
    proposed_registry_version: str = Field(pattern=r"^[1-9][0-9]{0,8}$", max_length=9)
    category_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,59}$", max_length=60)
    check_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,59}$", max_length=60)
    rule_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{3,100}$", max_length=100)
    title: str = Field(min_length=3, max_length=120)
    use_case: str = Field(min_length=3, max_length=500)


class CatalogDraftRequest(CatalogRuleDescriptionPreview):
    reason: str = Field(min_length=10, max_length=240)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,96}$", max_length=96)


class CatalogDraftReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str = Field(min_length=15, max_length=80)
    reason: str = Field(min_length=10, max_length=240)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,96}$", max_length=96)


class ExactUserLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=5, max_length=254)


class CognitoStatusReceiptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,96}$", max_length=96)
    # The optional exact email stays solely in the signed-in POST body.
    email: str | None = Field(default=None, min_length=5, max_length=254)


class CognitoStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=5, max_length=254)
    action: str = Field(pattern=r"^(disable|enable)$")
    expected_enabled: bool = Field(strict=True)
    confirmation: str = Field(min_length=10, max_length=270)
    reason: str = Field(min_length=10, max_length=240)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,96}$", max_length=96)


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, le=MAX_MUTABLE_REVISION, strict=True)
    status: str = Field(pattern=r"^(active|disabled)$")
    confirmation: str = Field(min_length=2, max_length=40)
    reason: str = Field(min_length=10, max_length=240)
    idempotency_key: str = Field(min_length=16, max_length=96)


Verifier = Callable[[str], VerifiedAdmin | None]
ADMIN_ROLES = frozenset({"owner"})  # Other roles require scoped authorization before enablement.
TENANT_ID = re.compile(r"^[a-z][a-z0-9-]{1,39}$")  # Match the authoritative TenantRecord.
PAGE_LIMIT = 50
# CloudFront replaces Authorization with its AWS origin signature. Hosted clients
# use a dedicated session header; never treat API keys as administrator sessions.
ADMIN_SESSION_HEADER = "X-GuardOath-Admin-Session"

_LOCAL_ADMIN_ORIGIN = re.compile(r"http://(localhost|127\.0\.0\.1):([0-9]{1,5})")


def _require_admin_origin(request: Request) -> None:
    """Deny foreign admin POST origins, including loopback sent to production.

    A public host must not accept a claim that the request originated from
    localhost. In local integration, the actual ASGI server address must match
    the precise loopback hostname and port as well as the HTTP request URL;
    a client-supplied Host header alone cannot establish local authority.
    """
    origins = request.headers.getlist("origin")
    if len(origins) == 1:
        if origins[0] == "https://guardoath.com":
            return
        local = _LOCAL_ADMIN_ORIGIN.fullmatch(origins[0])
        if local is not None:
            host, raw_port = local.groups()
            port = int(raw_port)
            server = request.scope.get("server")
            if (1 <= port <= 65535 and request.url.scheme == "http"
                    and request.url.hostname == host and request.url.port == port
                    and isinstance(server, (tuple, list)) and len(server) == 2
                    and server[0] == host and server[1] == port):
                return
    raise HTTPException(status_code=403, detail="Admin request origin refused")



def _safe_record(record) -> dict:
    """Strict tenant detail projection; never stringify corrupt identity or billing data.

    The DynamoDB directory validates raw pages independently. This path covers
    authoritative single-tenant detail and the non-DynamoDB directory fallback.
    An ambiguous account must never look like a valid paid customer.
    """
    tenant_id = getattr(record, "tenant_id", None)
    name = getattr(record, "display_name", None)
    status_obj = getattr(record, "status", None)
    status = getattr(status_obj, "value", status_obj)
    demo = getattr(record, "demo", None)
    subscription_obj = getattr(record, "subscription_status", None)
    subscription = getattr(subscription_obj, "value", subscription_obj)
    revision = getattr(record, "revision", None)
    updated = getattr(record, "updated_at", None)
    if (not isinstance(tenant_id, str) or not TENANT_ID.fullmatch(tenant_id)
            or not isinstance(name, str) or not 1 <= len(name) <= 100
            or status not in ("active", "disabled")
            or type(demo) is not bool
            or subscription not in (None, "inactive", "active", "past_due", "canceled")
            or type(revision) is not int or not 0 <= revision <= MAX_BROWSER_SAFE_INTEGER
            or not saved_utc_timestamp(updated)):
        raise ValueError("Ambiguous saved tenant identity or entitlement")

    month = datetime.now(timezone.utc).strftime("%Y-%m")
    usage = getattr(record, "usage", None)
    usage_count = None
    if usage is not None:
        if not isinstance(usage, dict) or len(usage) > 120:
            raise ValueError("Malformed tenant usage")
        for period, count in usage.items():
            if (not isinstance(period, str)
                    or not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", period)
                    or type(count) is not int or not 0 <= count <= MAX_BROWSER_SAFE_INTEGER):
                raise ValueError("Malformed tenant usage")
        usage_count = usage.get(month)

    allowance = getattr(record, "monthly_allowance", None)
    if type(allowance) is not int or not 0 <= allowance <= 1_000_000_000:
        allowance = None  # Unknown legacy allowance must not become zero.

    overage = getattr(record, "overage", None)
    if type(overage) is not str or overage not in ("block", "allow_and_record"):
        overage = None
    hashes = getattr(record, "key_hashes", None)
    key_count = (len(hashes) if isinstance(hashes, (tuple, list))
                 and len(hashes) <= 2
                 and all(isinstance(item, str) for item in hashes) else None)
    return {
        "tenant_id": tenant_id, "display_name": name, "status": status,
        "demo": demo, "subscription_status": subscription,
        "monthly_allowance": allowance, "overage_policy": overage,
        "api_key_count": key_count,
        "subscription_source": "persisted tenant record; not live Stripe verification",
        "usage_month": month, "usage_month_count": usage_count,
        "revision": revision, "updated_at": updated,
    }

def create_admin_router(*, verify_cognito_admin: Verifier | None = None,
                        tenant_store: TenantStore | None = None,
                        admin_directory: DynamoAdminDirectory | None = None,
                        admin_user_directory: CognitoUserDirectory | None = None,
                        catalog_provider: CatalogProvider | None = None,
                        catalog_history_provider: Callable[[], dict] | None = None,
                        admin_mutator: AdminTenantActions | None = None,
                        audit_reader: AdminAuditReader | None = None,
                        read_auditor: AdminSensitiveReadAuditor | None = None,
                        require_persistent_read_audit: bool = False,
                        browser_entry_enabled: bool = False,
                        billing_reporter: AdminSubscriptionReporter | None = None,
                        billing_downgrade: AdminBillingDowngrade | None = None,
                        billing_reconciler: AdminSubscriptionReconciliation | None = None,
                        billing_invoices: AdminInvoiceHistory | None = None,
                        catalog_drafts: AdminCatalogDrafts | None = None,
                        record_validator=None, rule_exists=None) -> APIRouter:
    """Build guarded admin routes; absent verifier => no admin access.

    The verifier MUST perform real independent Cognito checks; test stubs only
    belong in tests. No privilege inferred from email, custom tenant field,
    browser flags or a JWT group string without verification.
    """
    if type(require_persistent_read_audit) is not bool:
        raise ValueError("Persistent audit enforcement must be an explicit boolean")
    from guardoath.state_contracts import new_subscription_state_enabled
    if not new_subscription_state_enabled():
        billing_reconciler = None
    router = APIRouter()
    user_actions = None
    if (isinstance(admin_user_directory, CognitoUserDirectory)
            and isinstance(read_auditor, AdminSensitiveReadAuditor)):
        try:
            user_actions = AdminCognitoUserActions(
                admin_user_directory.client, read_auditor.ddb,
                admin_user_directory.pool_id, read_auditor.audit_table,
                audit_key=read_auditor.signing_key,
                reserve_read=admin_user_directory._reserve_provider_read)
        except ValueError:
            pass  # Incomplete provider permissions/configuration never expose a write route.


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

    def audit_sensitive_read(principal: VerifiedAdmin, resource: str, tenant_id: str = "system"):
        # Optional persistent audit uses an existing explicitly provisioned
        # append-only table. An audit failure prevents the sensitive provider read.
        if read_auditor is None:
            if require_persistent_read_audit:
                raise HTTPException(status_code=503, detail="Protected read auditing unavailable")
            return None
        try:
            return read_auditor.log(actor_sub=principal.subject,
                                    resource=resource, tenant_id=tenant_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Protected read auditing unavailable") from exc

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
        read_event = audit_sensitive_read(principal, "admin.features")
        return admin_json({"tenant_status_changes": admin_mutator is not None,
                           "cognito_user_status_changes": user_actions is not None,
                           "cognito_action_receipts": user_actions is not None,
                           "billing_changes": False, "billing_verify": billing_reporter is not None,
                           "billing_downgrade": billing_downgrade is not None,
                           "billing_reconcile": billing_reconciler is not None,
                           "billing_invoices": billing_invoices is not None,
                           "tenant_rule_changes": admin_mutator is not None and callable(record_validator) and callable(rule_exists),
                           "monthly_cost_caps": admin_mutator is not None and callable(record_validator),
                           "tenant_key_revocation": admin_mutator is not None and callable(record_validator),
                           "rule_publishing": False, "catalog_drafts": catalog_drafts is not None,
                           "catalog_review_receipts": catalog_drafts is not None and catalog_provider is not None,
                           "catalog_preview": catalog_provider is not None,
                           "audit_required_for_writes": True,
                           "audit_receipt_lookup": audit_reader is not None,
                           "sensitive_read_audit": read_auditor is not None,
                           **({"read_audit_event_id": read_event} if read_event else {})})

    @router.get("/v1/admin/audit/{event_id}")
    def audit_receipt(event_id: str, principal: VerifiedAdmin = Depends(require_admin)):
        if not EVENT.fullmatch(event_id):
            raise HTTPException(status_code=422, detail="Invalid audit event ID")
        if audit_reader is None:
            raise HTTPException(status_code=503, detail="Administrative audit lookup unavailable")
        read_event = audit_sensitive_read(principal, "audit.receipt")
        try:
            event = audit_reader.read(event_id)
        except (ValueError, AdminAuditUnavailable) as exc:
            raise HTTPException(status_code=503, detail="Administrative audit lookup unavailable") from exc
        if event is None:
            raise HTTPException(status_code=404, detail="Audit event not found")
        return admin_json({"audit": event, **({"read_audit_event_id": read_event} if read_event else {})})

    @router.get("/v1/admin/overview")
    def overview(principal: VerifiedAdmin = Depends(require_admin)):
        read_event = audit_sensitive_read(principal, "admin.overview")
        # Data not sourced from actual billing/provider systems must be unknown,
        # not an invented 0 or fabricated live revenue/cost metric.
        return admin_json({"status": "read_only", "metrics": {
            "users": {"value": None, "source": "Cognito", "status": "unavailable"},
            "active_subscriptions": {"value": None, "source": "Stripe", "status": "unavailable"},
            "revenue_usd": {"value": None, "source": "Stripe", "status": "unavailable"},
            "aws_cost_usd": {"value": None, "source": "AWS billing", "status": "unavailable"},
        }, **({"read_audit_event_id": read_event} if read_event else {})})

    @router.get("/v1/admin/users")
    def cognito_users(query: str = Query(default="", max_length=80),
                      limit: int = Query(default=20, ge=1, le=50),
                      cursor: str | None = Query(default=None, max_length=4096),
                      principal: VerifiedAdmin = Depends(require_admin)):
        # Provider enumeration requires freshly verified owner+MFA, never a tenant token.
        if admin_user_directory is None:
            raise HTTPException(status_code=503, detail="Cognito user directory unavailable")
        # Persist an owner-authenticated, signed read attempt before Cognito
        # enumeration. Audit outages must never result in unaudited reads.
        read_event = audit_sensitive_read(principal, "cognito.user_directory")
        try:
            page = admin_user_directory.page(owner_sub=principal.subject, limit=limit, query=query, cursor=cursor)
        except (InvalidUserCursor, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid user directory request") from exc
        except CognitoLookupRateLimited as exc:
            raise HTTPException(status_code=429, detail="Cognito user directory temporarily limited") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Cognito user directory unavailable") from exc
        return admin_json({**page, **({"read_audit_event_id": read_event} if read_event else {})})

    @router.post("/v1/admin/users/lookup")
    def exact_cognito_user(payload: ExactUserLookup, request: Request,
                           principal: VerifiedAdmin = Depends(require_admin)):
        # Exact email stays in the POST body, not the URL, access logs or redirects.
        # Same-origin check and independently verified Cognito owner/MFA apply.
        _require_admin_origin(request)
        if admin_user_directory is None:
            raise HTTPException(status_code=503, detail="Cognito user directory unavailable")
        read_event = audit_sensitive_read(principal, "cognito.exact_user")
        try:
            result = admin_user_directory.lookup(owner_sub=principal.subject, email=payload.email)
        except InvalidUserLookup as exc:
            raise HTTPException(status_code=422, detail="Invalid exact email") from exc
        except CognitoLookupRateLimited as exc:
            raise HTTPException(status_code=429, detail="Cognito account lookup temporarily limited") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Cognito identity inspection unavailable") from exc
        return admin_json({**result, **({"read_audit_event_id": read_event} if read_event else {})})

    if user_actions is not None:
        @router.post("/v1/admin/users/status")
        def change_cognito_user_status(payload: CognitoStatusChange, request: Request,
                                       principal: VerifiedAdmin = Depends(require_admin)):
            _require_admin_origin(request)
            # Preflight Cognito account reads are signed before the provider call.
            audit_sensitive_read(principal, "cognito.mutation_preflight")
            try:
                outcome = user_actions.change(
                    actor=principal.subject, email=payload.email, action=payload.action,
                    expected_enabled=payload.expected_enabled,
                    confirmation=payload.confirmation, reason=payload.reason,
                    idempotency_key=payload.idempotency_key)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid Cognito status action") from exc
            except UserActionConflict as exc:
                raise HTTPException(status_code=409, detail="Account state or action key changed; inspect before retrying") from exc
            except UserActionUnavailable as exc:
                raise HTTPException(status_code=503, detail="Cognito action not verified; inspect account and audit before retrying") from exc
            return admin_json(outcome)

        @router.post("/v1/admin/users/status/receipt")
        def inspect_cognito_action_receipt(payload: CognitoStatusReceiptRequest, request: Request,
                                           principal: VerifiedAdmin = Depends(require_admin)):
            _require_admin_origin(request)
            read_event = audit_sensitive_read(principal, "audit.receipt")
            try:
                receipt = user_actions.inspect_receipt(
                    actor=principal.subject, idempotency_key=payload.idempotency_key,
                    email=payload.email)
            except UserActionConflict as exc:
                raise HTTPException(status_code=409, detail="Action reference does not match current account") from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid action reference") from exc
            except UserActionUnavailable as exc:
                raise HTTPException(status_code=503, detail="Signed account-action history unavailable") from exc
            return admin_json({**receipt,
                               **({"read_audit_event_id": read_event} if read_event else {})})

    @router.get("/v1/admin/tenants")
    def tenants(search: str = Query(default="", max_length=40),
                offset: int = Query(default=0, ge=0, le=1_000_000),
                limit: int = Query(default=20, ge=1, le=PAGE_LIMIT),
                cursor: str | None = Query(default=None, max_length=4096),
                principal: VerifiedAdmin = Depends(require_admin)):
        if admin_directory is not None:
            read_event = audit_sensitive_read(principal, "tenant.directory")
            try:
                result = admin_directory.page(owner_sub=principal.subject, limit=limit, search=search, cursor=cursor)
            except InvalidCursor as exc:
                raise HTTPException(status_code=422, detail="Invalid pagination cursor") from exc
            except DirectoryUnavailable as exc:
                raise HTTPException(status_code=503, detail="Tenant directory unavailable") from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid directory request") from exc
            except Exception as exc:
                raise HTTPException(status_code=503, detail="Tenant directory unavailable") from exc
            return admin_json({**result, **({"read_audit_event_id": read_event} if read_event else {})})
        if cursor is not None:
            raise HTTPException(status_code=422, detail="Cursor pagination unavailable")
        if tenant_store is None:
            return admin_json({"status": "unavailable", "tenants": [], "total": None,
                               "source": "tenant store"})
        if search and not re.fullmatch(r"[a-zA-Z0-9_-]+", search):
            raise HTTPException(status_code=422, detail="Invalid tenant filter")
        read_event = audit_sensitive_read(principal, "tenant.directory")
        try:
            source_ids = tenant_store.tenant_ids()
            if (not isinstance(source_ids, (tuple, list)) or len(source_ids) > 100_000
                    or len(set(source_ids)) != len(source_ids)
                    or any(not isinstance(tid, str) or not TENANT_ID.fullmatch(tid)
                           for tid in source_ids)):
                raise ValueError("Ambiguous tenant directory identifiers")
            ids = sorted(tid for tid in source_ids if not search or search.lower() in tid.lower())
            selected = []
            for tid in ids[offset:offset + limit]:
                record = tenant_store.get(tid)
                if record is None:
                    raise ValueError("Tenant directory changed during projection")
                item = _safe_record(record)
                if item["tenant_id"] != tid:
                    raise ValueError("Tenant directory identity mismatch")
                selected.append(item)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Tenant directory unavailable") from exc
        return admin_json({"status": "ok", "source": "tenant store", "tenants": selected,
                           "total": len(ids), "offset": offset, "limit": limit,
                           **({"read_audit_event_id": read_event} if read_event else {})})

    @router.get("/v1/admin/tenants/{tenant_id}")
    def tenant_detail(tenant_id: str, principal: VerifiedAdmin = Depends(require_admin)):
        if not TENANT_ID.fullmatch(tenant_id):
            raise HTTPException(status_code=404, detail="Tenant not found")
        if tenant_store is None:
            raise HTTPException(status_code=503, detail="Tenant directory unavailable")
        read_event = audit_sensitive_read(principal, "tenant.detail", tenant_id)
        try:
            record = tenant_store.get(tenant_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Tenant not found")
            projected = _safe_record(record)
            if projected["tenant_id"] != tenant_id:
                raise ValueError("Tenant detail identity mismatch")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Tenant directory unavailable") from exc
        return admin_json({"tenant": projected, "source": "tenant store",
                           **({"read_audit_event_id": read_event} if read_event else {})})

    @router.get("/v1/admin/tenants/{tenant_id}/rule-action")
    def read_tenant_rule_action(tenant_id: str,
                                stage: str = Query(pattern=r"^(input|output)$"),
                                rule_id: str = Query(min_length=3, max_length=100,
                                                     pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$"),
                                principal: VerifiedAdmin = Depends(require_admin)):
        if not TENANT_ID.fullmatch(tenant_id):
            raise HTTPException(status_code=404, detail="Tenant not found")
        if tenant_store is None:
            raise HTTPException(status_code=503, detail="Tenant rule inspection unavailable")
        # Existing signed tenant-detail audit precedes the actual record read.
        read_event = audit_sensitive_read(principal, "tenant.detail", tenant_id)
        try:
            record = tenant_store.get(tenant_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Tenant not found")
            result = inspect_tenant_rule_action(
                record, tenant_id=tenant_id, stage=stage, rule_id=rule_id)
        except HTTPException:
            raise
        except RuleSelectionInvalid as exc:
            raise HTTPException(status_code=404, detail="Published rule unavailable") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Tenant rule inspection unavailable") from exc
        return admin_json({**result,
                           **({"read_audit_event_id": read_event} if read_event else {})})

    @router.get("/v1/admin/tenants/{tenant_id}/usage")
    def usage_detail(tenant_id: str, principal: VerifiedAdmin = Depends(require_admin)):
        if not TENANT_ID.fullmatch(tenant_id):
            raise HTTPException(status_code=404, detail="Tenant not found")
        if tenant_store is None:
            raise HTTPException(status_code=503, detail="Usage reporting unavailable")
        read_event = audit_sensitive_read(principal, "tenant.usage", tenant_id)
        try:
            record = tenant_store.get(tenant_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Tenant not found")
            # The requested path is not an authoritative account identity. A
            # misindexed or stale store must never return another tenant's usage.
            if getattr(record, "tenant_id", None) != tenant_id:
                raise ValueError("Tenant usage source identity mismatch")
            report = tenant_usage_report(record)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Usage reporting unavailable") from exc
        return admin_json({"status": "ok", "usage": report,
                           **({"read_audit_event_id": read_event} if read_event else {})})

    @router.get("/v1/admin/subscriptions/{tenant_id}")
    def read_verified_subscription(tenant_id: str, principal: VerifiedAdmin = Depends(require_admin)):
        if not TENANT_ID.fullmatch(tenant_id):
            raise HTTPException(status_code=404, detail="Tenant not found")
        if tenant_store is None or billing_reporter is None:
            raise HTTPException(status_code=503, detail="Subscription verification unavailable")
        read_event = audit_sensitive_read(principal, "stripe.subscription", tenant_id)
        try:
            record = tenant_store.get(tenant_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Tenant not found")
            # Validate the saved key before any external Stripe read or
            # entitlement projection. Provider bindings alone are insufficient.
            if getattr(record, "tenant_id", None) != tenant_id:
                raise ValueError("Subscription source identity mismatch")
            result = billing_reporter.read(record)
        except HTTPException:
            raise
        except BillingUnavailable as exc:
            raise HTTPException(status_code=503, detail="Subscription verification unavailable") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Subscription verification unavailable") from exc
        return admin_json({"tenant_id":tenant_id, "subscription":result,
                           **({"read_audit_event_id": read_event} if read_event else {})})

    @router.get("/v1/admin/subscriptions/{tenant_id}/invoices")
    def verified_invoice_history(tenant_id: str, principal: VerifiedAdmin = Depends(require_admin)):
        if not TENANT_ID.fullmatch(tenant_id):
            raise HTTPException(status_code=404, detail="Tenant not found")
        if tenant_store is None or billing_invoices is None:
            raise HTTPException(status_code=503, detail="Invoice history unavailable")
        # Signed durable read intent must precede both tenant and provider reads.
        read_event = audit_sensitive_read(principal, "stripe.invoices", tenant_id)
        try:
            record = tenant_store.get(tenant_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Tenant not found")
            # Do not request invoice data under an alias for another account,
            # even if the provider would otherwise accept its saved binding.
            if getattr(record, "tenant_id", None) != tenant_id:
                raise ValueError("Invoice source identity mismatch")
            result = billing_invoices.read(record)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Invoice history unavailable") from exc
        return admin_json({"tenant_id": tenant_id, "history": result,
                           **({"read_audit_event_id": read_event} if read_event else {})})

    if billing_reconciler is not None:
        @router.post("/v1/admin/subscriptions/{tenant_id}/reconcile")
        def reconcile_verified_subscription(tenant_id: str, payload: ReconcileUnpaidSubscription,
                                            request: Request, principal: VerifiedAdmin = Depends(require_admin)):
            _require_admin_origin(request)
            if not TENANT_ID.fullmatch(tenant_id):
                raise HTTPException(status_code=404, detail="Tenant not found")
            read_event = audit_sensitive_read(principal, "stripe.subscription", tenant_id)
            try:
                result = billing_reconciler.apply(
                    tenant_id=tenant_id, expected_revision=payload.expected_revision,
                    confirmation=payload.confirmation, reason=payload.reason,
                    actor=principal.subject, idempotency_key=payload.idempotency_key)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid subscription reconciliation") from exc
            except SubscriptionConflict as exc:
                raise HTTPException(status_code=409, detail="Reconciliation conflicts with saved account or provider state; refresh and inspect") from exc
            except SubscriptionUnavailable as exc:
                raise HTTPException(status_code=503, detail="Subscription reconciliation not verified; retain action reference before retry") from exc
            return admin_json({**result, **({"read_audit_event_id": read_event} if read_event else {})})

    if billing_downgrade is not None:
        @router.post("/v1/admin/subscriptions/{tenant_id}/downgrade")
        def downgrade_subscription(tenant_id: str, payload: ReconcileUnpaidSubscription,
                                   request: Request, principal: VerifiedAdmin = Depends(require_admin)):
            _require_admin_origin(request)
            if not TENANT_ID.fullmatch(tenant_id):
                raise HTTPException(status_code=404, detail="Tenant not found")
            read_event = audit_sensitive_read(principal, "stripe.subscription", tenant_id)
            try:
                result = billing_downgrade.apply(
                    tenant_id=tenant_id, expected_revision=payload.expected_revision,
                    confirmation=payload.confirmation, reason=payload.reason,
                    actor=principal.subject, idempotency_key=payload.idempotency_key)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid billing reconciliation") from exc
            except BillingDowngradeConflict as exc:
                raise HTTPException(status_code=409, detail="No verified provider downgrade or revision changed") from exc
            except BillingDowngradeUnavailable as exc:
                raise HTTPException(status_code=503, detail="Audited billing reconciliation unavailable") from exc
            return admin_json({**result, **({"read_audit_event_id": read_event} if read_event else {})})

    @router.get("/v1/admin/catalog")
    def catalog(category_id: str | None = Query(default=None, min_length=1, max_length=60,
                                                pattern=r"^[a-z][a-z0-9_]*$"),
                principal: VerifiedAdmin = Depends(require_admin)):
        if catalog_provider is None:
            return admin_json({"status": "unavailable", "source": "published GuardOath control registry",
                               "categories": [], "total_categories": None})
        read_event = audit_sensitive_read(principal, "catalog.registry")
        try:
            result = read_catalog(catalog_provider, domain_id=category_id)
            if catalog_history_provider is None:
                result["metadata_releases"] = {"status": "unavailable", "release_count": None,
                                               "source": "Source history not configured"}
            elif category_id is None:
                result["metadata_releases"] = verified_catalog_history(
                    result, catalog_history_provider())
            else:
                # A filtered projection cannot verify whole-registry source fingerprints.
                whole = read_catalog(catalog_provider)
                result["metadata_releases"] = verified_catalog_history(
                    whole, catalog_history_provider())
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Published catalog unavailable") from exc
        return admin_json({"status": "ok", **result,
                           **({"read_audit_event_id": read_event} if read_event else {})})

    @router.post("/v1/admin/catalog/preview")
    def preview_catalog_rule(payload: CatalogRuleDescriptionPreview, request: Request,
                             principal: VerifiedAdmin = Depends(require_admin)):
        # Authenticated metadata-only preview. No draft is saved and no published
        # detector, tenant record, or customer rule action can be modified.
        _require_admin_origin(request)
        if catalog_provider is None:
            raise HTTPException(status_code=503, detail="Published catalog unavailable")
        read_event = audit_sensitive_read(principal, "catalog.preview")
        try:
            result = preview_rule_description(catalog_provider, **payload.model_dump())
        except CatalogPreviewConflict as exc:
            raise HTTPException(status_code=409, detail="Catalog changed; refresh before preview") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid catalog preview") from exc
        except CatalogPreviewUnavailable as exc:
            raise HTTPException(status_code=503, detail="Published catalog unavailable") from exc
        return admin_json({**result, **({"read_audit_event_id": read_event} if read_event else {})})


    if catalog_drafts is not None:
        @router.post("/v1/admin/catalog/drafts")
        def save_catalog_draft(payload: CatalogDraftRequest, request: Request,
                               principal: VerifiedAdmin = Depends(require_admin)):
            _require_admin_origin(request)
            if catalog_provider is None:
                raise HTTPException(status_code=503, detail="Published catalog unavailable")
            read_event = audit_sensitive_read(principal, "catalog.preview")
            try:
                values = payload.model_dump(exclude={"reason", "idempotency_key"})
                preview = preview_rule_description(catalog_provider, **values)
                record = catalog_drafts.save(actor=principal.subject,
                    nonce=payload.idempotency_key, reason=payload.reason, preview=preview,
                    title=payload.title, use_case=payload.use_case)
            except CatalogPreviewConflict as exc:
                raise HTTPException(status_code=409, detail="Catalog version or source changed") from exc
            except DraftConflict as exc:
                raise HTTPException(status_code=409, detail="Draft idempotency conflict") from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid catalog draft") from exc
            except (DraftUnavailable, CatalogPreviewUnavailable) as exc:
                raise HTTPException(status_code=503, detail="Draft persistence unavailable") from exc
            return admin_json({**record, "persisted": True, "reviewed": False,
                **({"read_audit_event_id": read_event} if read_event else {})})

        @router.get("/v1/admin/catalog/drafts/{draft_id}")
        def read_catalog_draft(draft_id: str, principal: VerifiedAdmin = Depends(require_admin)):
            if not re.fullmatch(r"[0-9a-f]{64}", draft_id):
                raise HTTPException(status_code=422, detail="Invalid draft ID")
            read_event = audit_sensitive_read(principal, "catalog.draft")
            try:
                record = catalog_drafts.read(principal.subject, draft_id)
                if record is None:
                    raise HTTPException(status_code=404, detail="Catalog draft not found")
                if catalog_provider is None:
                    raise CatalogPreviewUnavailable("Published catalog unavailable")
                refreshed = preview_rule_description(catalog_provider,
                    expected_registry_version=record["registry_version"],
                    proposed_registry_version=record["candidate_registry_version"],
                    category_id=record["category_id"], check_id=record["check_id"],
                    rule_id=record["rule_id"], title=record["title"], use_case=record["use_case"])
                stale = (refreshed["baseline_sha256"] != record["baseline_sha256"] or
                         refreshed["proposal_sha256"] != record["proposal_sha256"])
            except HTTPException:
                raise
            except (CatalogPreviewConflict, ValueError):
                stale = True
            except (DraftUnavailable, CatalogPreviewUnavailable, KeyError, TypeError) as exc:
                raise HTTPException(status_code=503, detail="Catalog draft unavailable") from exc
            return admin_json({"draft":record, "stale":stale, "published":False,
                "ready_to_publish":False,
                **({"read_audit_event_id":read_event} if read_event else {})})

        @router.post("/v1/admin/catalog/drafts/{draft_id}/review")
        def review_catalog_draft(draft_id: str, payload: CatalogDraftReviewRequest,
                                 request: Request,
                                 principal: VerifiedAdmin = Depends(require_admin)):
            # This signed review receipt never publishes a catalog definition.
            _require_admin_origin(request)
            if not re.fullmatch(r"[0-9a-f]{64}", draft_id):
                raise HTTPException(status_code=422, detail="Invalid draft ID")
            if catalog_provider is None:
                raise HTTPException(status_code=503, detail="Published catalog unavailable")
            read_event = audit_sensitive_read(principal, "catalog.draft")
            try:
                draft = catalog_drafts.read(principal.subject, draft_id)
                if draft is None:
                    raise HTTPException(status_code=404, detail="Catalog draft not found")
                refreshed = preview_rule_description(
                    catalog_provider,
                    expected_registry_version=draft["registry_version"],
                    proposed_registry_version=draft["candidate_registry_version"],
                    category_id=draft["category_id"], check_id=draft["check_id"],
                    rule_id=draft["rule_id"], title=draft["title"],
                    use_case=draft["use_case"])
                if (refreshed["baseline_sha256"] != draft["baseline_sha256"] or
                        refreshed["proposal_sha256"] != draft["proposal_sha256"]):
                    raise DraftConflict("Published catalog source changed")
                review = catalog_drafts.review(
                    actor=principal.subject, draft_id=draft_id,
                    nonce=payload.idempotency_key, reason=payload.reason,
                    confirmation=payload.confirmation,
                    baseline_sha256=refreshed["baseline_sha256"],
                    proposal_sha256=refreshed["proposal_sha256"])
            except HTTPException:
                raise
            except (CatalogPreviewConflict, DraftConflict, ValueError, KeyError) as exc:
                if isinstance(exc, ValueError) and not isinstance(
                        exc, (CatalogPreviewConflict, DraftConflict)):
                    raise HTTPException(status_code=422, detail="Invalid catalog review") from exc
                raise HTTPException(status_code=409, detail="Catalog draft/source changed; refresh") from exc
            except (DraftUnavailable, CatalogPreviewUnavailable, TypeError) as exc:
                raise HTTPException(status_code=503, detail="Catalog review unavailable") from exc
            return admin_json({**review, "persisted": True, "reviewed": True,
                "ready_to_publish": False,
                **({"read_audit_event_id": read_event} if read_event else {})})

        @router.get("/v1/admin/catalog/reviews/{review_id}")
        def read_catalog_review(review_id: str, principal: VerifiedAdmin = Depends(require_admin)):
            if not re.fullmatch(r"[0-9a-f]{64}", review_id):
                raise HTTPException(status_code=422, detail="Invalid review ID")
            read_event = audit_sensitive_read(principal, "catalog.draft")
            try:
                review = catalog_drafts.read_review(principal.subject, review_id)
                if review is None:
                    raise HTTPException(status_code=404, detail="Catalog review not found")
                # Historical receipt integrity is NOT a claim of current source.
                draft = catalog_drafts.read(principal.subject, review["draft_id"])
                if (draft is None or
                        draft["baseline_sha256"] != review["baseline_sha256"] or
                        draft["proposal_sha256"] != review["proposal_sha256"]):
                    raise DraftUnavailable("Catalog review/draft binding unavailable")
                if catalog_provider is None:
                    raise CatalogPreviewUnavailable("Published catalog unavailable")
                try:
                    refreshed = preview_rule_description(
                        catalog_provider,
                        expected_registry_version=draft["registry_version"],
                        proposed_registry_version=draft["candidate_registry_version"],
                        category_id=draft["category_id"], check_id=draft["check_id"],
                        rule_id=draft["rule_id"], title=draft["title"],
                        use_case=draft["use_case"])
                except (CatalogPreviewConflict, ValueError):
                    stale = True
                else:
                    stale = (refreshed["baseline_sha256"] != review["baseline_sha256"] or
                             refreshed["proposal_sha256"] != review["proposal_sha256"])
            except HTTPException:
                raise
            except (DraftUnavailable, CatalogPreviewUnavailable, KeyError, TypeError) as exc:
                raise HTTPException(status_code=503, detail="Catalog review verification unavailable") from exc
            return admin_json({"review": review, "stale": stale,
                "source_current": not stale, "published": False,
                "ready_to_publish": False,
                **({"read_audit_event_id": read_event} if read_event else {})})

        @router.get("/v1/admin/catalog/reviews/{review_id}/manifest")
        def review_metadata_manifest(review_id: str, principal: VerifiedAdmin = Depends(require_admin)):
            # Calculation only: never publish a catalog, enable a check or change a tenant.
            if not re.fullmatch(r"[0-9a-f]{64}", review_id):
                raise HTTPException(status_code=422, detail="Invalid review ID")
            read_event = audit_sensitive_read(principal, "catalog.draft")
            try:
                review = catalog_drafts.read_review(principal.subject, review_id)
                if review is None:
                    raise HTTPException(status_code=404, detail="Catalog review not found")
                draft = catalog_drafts.read(principal.subject, review["draft_id"])
                if draft is None:
                    raise CatalogManifestUnavailable("Reviewed draft unavailable")
                if catalog_provider is None:
                    raise CatalogManifestUnavailable("Published registry unavailable")
                manifest = reviewed_metadata_manifest(
                    catalog_provider, draft=draft, review=review)
            except HTTPException:
                raise
            except CatalogManifestConflict as exc:
                raise HTTPException(status_code=409, detail="Reviewed source changed; refresh") from exc
            except (DraftUnavailable, CatalogManifestUnavailable, KeyError, TypeError) as exc:
                raise HTTPException(status_code=503, detail="Catalog manifest unavailable") from exc
            return admin_json({**manifest,
                **({"read_audit_event_id": read_event} if read_event else {})})

    if admin_mutator is not None:
        @router.post("/v1/admin/tenants/{tenant_id}/status")
        def change_tenant_status(tenant_id: str, payload: StatusChange, request: Request,
                                 principal: VerifiedAdmin = Depends(require_admin)):
            # Browser admin tokens use a non-simple custom header. Enforce origin
            # on all writes as defense in depth against cross-site attempts.
            _require_admin_origin(request)
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
            _require_admin_origin(request)
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
            _require_admin_origin(request)
            try:
                result=admin_mutator.cap_monthly_allowance(
                    tenant_id=tenant_id,expected_revision=payload.expected_revision,
                    new_limit=payload.new_limit,confirmation=payload.confirmation,
                    reason=payload.reason,actor=principal.subject,
                    idempotency_key=payload.idempotency_key,
                    validate_record=record_validator)
            except ValueError as exc:
                raise HTTPException(status_code=422,detail="Invalid cost cap request") from exc
            except AdminUnboundZeroCap as exc:
                raise HTTPException(status_code=409,detail="A zero cap for an unbound account is unavailable in this release. Use Suspend to block access.") from exc
            except AdminActionConflict as exc:
                raise HTTPException(status_code=409,detail="Tenant state changed; refresh") from exc
            except AdminActionUnavailable as exc:
                raise HTTPException(status_code=503,detail="Audited cost control unavailable") from exc
            return admin_json(result)


    if admin_mutator is not None and callable(record_validator):
        @router.post("/v1/admin/tenants/{tenant_id}/revoke-keys")
        def revoke_tenant_keys(tenant_id: str, payload: RevokeTenantKeys, request: Request,
                               principal: VerifiedAdmin = Depends(require_admin)):
            _require_admin_origin(request)
            if not TENANT_ID.fullmatch(tenant_id):
                raise HTTPException(status_code=404, detail="Tenant not found")
            try:
                result = admin_mutator.revoke_all_keys(
                    tenant_id=tenant_id, expected_revision=payload.expected_revision,
                    confirmation=payload.confirmation, reason=payload.reason,
                    actor=principal.subject, idempotency_key=payload.idempotency_key,
                    validate_record=record_validator)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid key revocation request") from exc
            except AdminActionConflict as exc:
                raise HTTPException(status_code=409, detail="Tenant keys or revision changed; refresh") from exc
            except AdminActionUnavailable as exc:
                raise HTTPException(status_code=503, detail="Audited key revocation unavailable") from exc
            return admin_json(result)

    return router


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>GuardOath Admin — Read-only</title><style>
:root{font-family:system-ui,-apple-system,sans-serif;color:#182535;background:#f5f7fb}body{margin:0}header{background:#102337;color:white;padding:22px 6%}main{max-width:1100px;margin:36px auto;padding:0 24px}h1{margin:0 0 4px}p{line-height:1.5}.label{color:#b6cadb;font-size:14px}.panel{background:white;border:1px solid #dbe3ec;border-radius:12px;padding:24px;margin:18px 0}.status{font-weight:600;color:#8a4800}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.metric{border:1px solid #dbe3ec;border-radius:8px;padding:16px}.metric strong{display:block;font-size:23px;margin:10px 0}.metric small{color:#526376}a{color:#174c85}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:12px;border-bottom:1px solid #e5eaf0}@media(max-width:650px){table{display:block;overflow-x:auto}main{margin-top:18px}}
</style></head><body><header><h1>GuardOath Administration</h1><div class="label">Operator console · Phase A · Read-only</div></header><main>
<section class="panel"><h2>Operational overview</h2><p class="status">This first build does not claim real-time billing or cost data.</p><div class="grid"><div class="metric">Users<strong>Unavailable</strong><small>Cognito integration pending</small></div><div class="metric">Subscriptions<strong>Unavailable</strong><small>Stripe provider reconciliation pending</small></div><div class="metric">Revenue<strong>Unavailable</strong><small>Stripe reporting pending</small></div><div class="metric">AWS costs<strong>Unavailable</strong><small>AWS billing integration pending</small></div></div></section>
<section class="panel"><h2>Tenants</h2><p>Read-only tenant details are available through authenticated <code>/v1/admin/tenants</code>. No credentials or data are embedded in this HTML document.</p></section>
<section class="panel"><h2>Rules and categories</h2><p>Versioned catalog editing and publishing are deferred until server-side audit, role separation and rollback are implemented.</p></section>
</main></body></html>"""
