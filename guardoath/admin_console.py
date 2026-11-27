"""GuardOath admin Phase A: guarded, read-only tenant management slice.

Not enabled by simply importing. Production integration must inject a VERIFIED
Cognito identity validator; no JWT claim, user header, or application key is
accepted as proof of administrative authority by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
import re
from typing import Callable, Protocol, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from guardoath.admin_actions import AdminActionConflict, AdminActionUnavailable, AdminTenantActions, AdminUnboundZeroCap
from guardoath.admin_audit import AdminAuditReader, AdminSensitiveReadAuditor, AdminAuditUnavailable, EVENT
from guardoath.admin_billing import AdminSubscriptionReporter, BillingUnavailable
from guardoath.admin_billing_reconcile import AdminBillingDowngrade, BillingDowngradeConflict, BillingDowngradeUnavailable
from guardoath.admin_subscription_reconcile import (
    AdminSubscriptionReconciliation, SubscriptionConflict, SubscriptionUnavailable)
from guardoath.admin_invoices import AdminInvoiceHistory, InvalidInvoiceCursor, InvoiceReadRateLimited
from guardoath.admin_subscription_cancellation import (
    AdminSubscriptionCancellation, CancellationConflict, CancellationUnavailable, CancellationRateLimited)
from guardoath.admin_invoice_refund import (
    AdminInvoiceRefund, RefundConflict, RefundUnavailable, RefundRateLimited)
from guardoath.admin_invoice_credit import AdminInvoiceCredit, CreditConflict
from guardoath.admin_invoices import InvalidInvoiceReference
from guardoath.admin_cap_restore import (
    AdminRequestCapRestoration, CapRestoreConflict)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from guardoath.admin_catalog import CatalogProvider, read_catalog
from guardoath.admin_catalog_inspection import (
    CatalogInspectionConflict, CatalogInspectionNotFound, inspect_catalog)
from guardoath.admin_catalog_history import verified_catalog_history, CatalogHistoryUnavailable
from guardoath.admin_catalog_drafts import AdminCatalogDrafts, DraftConflict, DraftUnavailable
from guardoath.admin_catalog_preview import (
    CatalogPreviewConflict, CatalogPreviewUnavailable, preview_rule_description)
from guardoath.admin_catalog_manifest import (
    CatalogManifestConflict, CatalogManifestUnavailable, reviewed_metadata_manifest)
from guardoath.admin_catalog_publication import AdminCatalogPublications
from guardoath.admin_catalog_publication_state import PublicationConflict, PublicationUnavailable
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


class CancellationPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, le=MAX_BROWSER_SAFE_INTEGER, strict=True)


class CancellationRequest(CancellationPreviewRequest):
    preview_reference: str = Field(min_length=44, max_length=4096, pattern=r"^sc1\.[A-Za-z0-9_-]+$")
    confirmation: str = Field(min_length=23, max_length=62)
    reason: str = Field(min_length=10, max_length=240)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,96}$", max_length=96)


class CancellationReceiptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,96}$", max_length=96)


class InvoiceCreditPreviewRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    selection: str = Field(min_length=40, max_length=2049)
    amount_minor: int = Field(ge=1, le=99_999_999)
    reason: Literal["duplicate", "fraudulent", "order_change", "product_unsatisfactory"]


class InvoiceCreditReference(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    reference: str = Field(min_length=40, max_length=4054)


class InvoiceCreditSubmit(InvoiceCreditReference):
    confirmation: str = Field(min_length=10, max_length=100)


class RefundPreviewRequest(CancellationPreviewRequest):
    invoice_reference: str = Field(min_length=44, max_length=4096, pattern=r"^ir1\.[A-Za-z0-9_-]+$")


class RefundRequest(CancellationPreviewRequest):
    preview_reference: str = Field(min_length=44, max_length=4096, pattern=r"^rf1\.[A-Za-z0-9_-]+$")
    confirmation: str = Field(min_length=20, max_length=100)
    reason: str = Field(min_length=10, max_length=240)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,96}$", max_length=96)


class RefundReceiptRequest(CancellationReceiptRequest):
    pass


class CapRestorePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, le=MAX_MUTABLE_REVISION, strict=True)


class CapRestoreRequest(CapRestorePreviewRequest):
    preview_reference: str = Field(min_length=44, max_length=4096, pattern=r"^cr1\.[A-Za-z0-9_-]+$")
    confirmation: str = Field(min_length=30, max_length=120)
    reason: str = Field(min_length=10, max_length=240)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,96}$", max_length=96)


class CapRestoreReceiptRequest(CancellationReceiptRequest):
    pass


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


class CatalogPublicationPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    review_id: str = Field(pattern=r"^[a-f0-9]{64}$", max_length=64)
    operation: str = Field(pattern=r"^(publish_metadata|reverse_latest)$", max_length=20)


class CatalogPublicationRequest(CatalogPublicationPreviewRequest):
    expected_source_sha: str = Field(pattern=r"^[a-f0-9]{40}$", max_length=40)
    expected_control_hash: str = Field(pattern=r"^[a-f0-9]{64}$", max_length=64)
    reason: str = Field(min_length=10, max_length=240)
    action_reference: str = Field(pattern=r"^[A-Za-z0-9_-]{16,96}$", max_length=96)
    confirmation: str = Field(min_length=16, max_length=16)


class CatalogPublicationRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action_reference: str = Field(pattern=r"^[A-Za-z0-9_-]{16,96}$", max_length=96)


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
                        billing_cancellation: AdminSubscriptionCancellation | None = None,
                        billing_refunds: AdminInvoiceRefund | None = None,
                        billing_cap_restore: AdminRequestCapRestoration | None = None,
                        billing_credit: AdminInvoiceCredit | None = None,
                        billing_configuration: dict[str, bool] | None = None,
                        catalog_drafts: AdminCatalogDrafts | None = None,
                        catalog_publications: AdminCatalogPublications | None = None,
                        record_validator=None, rule_exists=None) -> APIRouter:
    """Build guarded admin routes; absent verifier => no admin access.

    The verifier MUST perform real independent Cognito checks; test stubs only
    belong in tests. No privilege inferred from email, custom tenant field,
    browser flags or a JWT group string without verification.
    """
    if type(require_persistent_read_audit) is not bool:
        raise ValueError("Persistent audit enforcement must be an explicit boolean")
    from guardoath.state_contracts import (
        new_subscription_state_enabled, new_cancellation_state_enabled, new_refund_state_enabled,
        new_cap_restoration_state_enabled)
    if billing_configuration is not None and (
            not isinstance(billing_configuration, dict)
            or set(billing_configuration) != {"signed_audit", "stripe_read", "plan_allowance", "cancellation_provider", "refund_provider"}
            or any(type(value) is not bool for value in billing_configuration.values())):
        raise ValueError("Billing configuration must contain only explicit prerequisite booleans")
    # This reports setup only. Actual mounted services remain the authority for
    # each capability; configuration is never proof of a verified subscription.
    billing_configuration = dict(billing_configuration or {})
    if not new_subscription_state_enabled():
        billing_reconciler = None
    if billing_reconciler is not None and (
            not isinstance(billing_reconciler, AdminSubscriptionReconciliation)
            or not isinstance(read_auditor, AdminSensitiveReadAuditor)
            or read_auditor.ddb is not billing_reconciler.ddb
            or read_auditor.audit_table != billing_reconciler.audit_table
            or not hmac.compare_digest(read_auditor.signing_key, billing_reconciler.key)):
        raise ValueError("Subscription reconciliation requires its bound persistent read auditor")
    if billing_cancellation is not None and (not isinstance(billing_cancellation, AdminSubscriptionCancellation)
            or not isinstance(read_auditor, AdminSensitiveReadAuditor)
            or read_auditor.ddb is not billing_cancellation.state.client
            or read_auditor.audit_table != billing_cancellation.state.table_name):
        raise ValueError("Cancellation requires its bound persistent read auditor")
    if billing_refunds is not None and (
            not isinstance(billing_refunds, AdminInvoiceRefund)
            or not isinstance(read_auditor, AdminSensitiveReadAuditor)
            or read_auditor.ddb is not billing_refunds.state.client
            or read_auditor.audit_table != billing_refunds.state.table_name
            or not hmac.compare_digest(read_auditor.signing_key, billing_refunds.state.audit_key)):
        raise ValueError("Refunds require their bound persistent read auditor")
    if billing_credit is not None and (
            not isinstance(billing_credit, AdminInvoiceCredit) or billing_credit.history is not billing_invoices
            or not isinstance(read_auditor, AdminSensitiveReadAuditor) or tenant_store is None
            or not hmac.compare_digest(billing_credit.audit_key, read_auditor.signing_key)):
        raise ValueError("Invoice credits require the bound invoice reader, tenant store and persistent auditor")
    if billing_cap_restore is not None and (
            not isinstance(billing_cap_restore, AdminRequestCapRestoration)
            or not isinstance(read_auditor, AdminSensitiveReadAuditor)
            or read_auditor.ddb is not billing_cap_restore.state.client
            or read_auditor.audit_table != billing_cap_restore.state.table_name
            or not hmac.compare_digest(read_auditor.signing_key, billing_cap_restore.state.audit_key)):
        raise ValueError("Cap restoration requires its bound persistent read auditor")
    if catalog_publications is not None and (
            not isinstance(catalog_publications, AdminCatalogPublications)
            or not isinstance(read_auditor, AdminSensitiveReadAuditor)
            or read_auditor.ddb is not catalog_publications.state.client
            or read_auditor.audit_table != catalog_publications.state.table_name
            or catalog_publications.drafts is not catalog_drafts):
        raise ValueError("Catalog publication requires its bound signed draft and read audit store")
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
                           "billing_cancellation_preview": billing_cancellation is not None and billing_cancellation.provider is not None,
                           "billing_cancellation_schedule": billing_cancellation is not None and billing_cancellation.provider is not None and new_cancellation_state_enabled(),
                           "billing_cancellation_receipts": billing_cancellation is not None,
                           "billing_credit_preview": billing_credit is not None,
                           "billing_credit_submit": billing_credit is not None and billing_credit.provider.can_issue,
                           "billing_refund_preview": billing_refunds is not None and billing_refunds.provider is not None,
                           "billing_refund_submit": billing_refunds is not None and billing_refunds.provider is not None and new_refund_state_enabled(),
                           "billing_refund_receipts": billing_refunds is not None,
                           "billing_refund_refresh": billing_refunds is not None and billing_refunds.provider is not None and new_refund_state_enabled(),
                           "billing_cap_restore_preview": billing_cap_restore is not None and billing_cap_restore.provider_configured,
                           "billing_cap_restore_submit": billing_cap_restore is not None and billing_cap_restore.provider_configured and new_cap_restoration_state_enabled(),
                           "billing_cap_restore_receipts": billing_cap_restore is not None,
                           "billing_configuration": {**billing_configuration,
                               "subscription_writer_enabled": new_subscription_state_enabled(),
                               "cancellation_writer_enabled": new_cancellation_state_enabled(),
                               "refund_writer_enabled": new_refund_state_enabled(),
                               "cap_restore_writer_enabled": new_cap_restoration_state_enabled()},
                           "tenant_rule_changes": admin_mutator is not None and callable(record_validator) and callable(rule_exists),
                           "monthly_cost_caps": admin_mutator is not None and callable(record_validator),
                           "tenant_key_revocation": admin_mutator is not None and callable(record_validator),
                           "rule_publishing": False, "catalog_drafts": catalog_drafts is not None,
                           "catalog_review_receipts": catalog_drafts is not None and catalog_provider is not None,
                           "catalog_preview": catalog_provider is not None,
                           "catalog_inspection": catalog_provider is not None,
                           "catalog_publication_preview": catalog_publications is not None,
                           "catalog_publication_requests": catalog_publications is not None and catalog_publications.writer_enabled(),
                           "catalog_publication_history": catalog_publications is not None,
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
    def verified_invoice_history(tenant_id: str,
                                 cursor: str | None = Query(default=None, max_length=2048),
                                 status: str | None = Query(default=None, max_length=13),
                                 principal: VerifiedAdmin = Depends(require_admin)):
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
            result = billing_invoices.read(record, owner_sub=principal.subject,
                                           cursor=cursor, status=status)
        except HTTPException:
            raise
        except InvalidInvoiceCursor as exc:
            raise HTTPException(status_code=400, detail="Invoice page expired or selection changed; restart invoice history") from exc
        except InvoiceReadRateLimited as exc:
            raise HTTPException(status_code=429, detail="Invoice reads temporarily limited") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Invoice history unavailable") from exc
        return admin_json({"tenant_id": tenant_id, "history": result,
                           **({"read_audit_event_id": read_event} if read_event else {})})

    if billing_credit is not None:
        def credit_call(tenant_id, principal, request, operation, payload):
            _require_admin_origin(request)
            if "owner" not in principal.roles:
                raise HTTPException(status_code=403, detail="Owner access required")
            if not TENANT_ID.fullmatch(tenant_id):
                raise HTTPException(status_code=404, detail="Tenant not found")
            audit = audit_sensitive_read(principal, "stripe.invoices", tenant_id)
            try:
                record = tenant_store.get(tenant_id)
                if record is None or record.tenant_id != tenant_id:
                    raise HTTPException(status_code=404, detail="Tenant not found")
                billing_invoices._reserve_read(principal.subject, billing_invoices._now())
                if operation == "preview":
                    result = billing_credit.preview(record, principal.subject, payload.selection,
                        payload.amount_minor, payload.reason, audit)
                elif operation == "issue":
                    result = billing_credit.apply(record, principal.subject, payload.reference,
                        payload.confirmation, tenant_store.get)
                else:
                    result = billing_credit.receipt(record, principal.subject, payload.reference)
            except HTTPException:
                raise
            except InvoiceReadRateLimited:
                raise HTTPException(status_code=429, detail="Invoice reads temporarily limited") from None
            except (CreditConflict, InvalidInvoiceReference):
                raise HTTPException(status_code=409, detail="Invoice credit selection changed or review expired") from None
            except Exception:
                raise HTTPException(status_code=503, detail="Credit unverified. Inspect the original review reference.") from None
            return admin_json({"tenant_id": tenant_id, "credit": result, "read_audit_event_id": audit})

        @router.post("/v1/admin/subscriptions/{tenant_id}/invoice-credit/preview")
        def preview_credit(tenant_id: str, payload: InvoiceCreditPreviewRequest, request: Request,
                           principal: VerifiedAdmin = Depends(require_admin)):
            return credit_call(tenant_id, principal, request, "preview", payload)

        @router.post("/v1/admin/subscriptions/{tenant_id}/invoice-credit/receipt")
        def credit_receipt(tenant_id: str, payload: InvoiceCreditReference, request: Request,
                           principal: VerifiedAdmin = Depends(require_admin)):
            return credit_call(tenant_id, principal, request, "receipt", payload)

        if billing_credit.provider.can_issue:
            @router.post("/v1/admin/subscriptions/{tenant_id}/invoice-credit")
            def issue_credit(tenant_id: str, payload: InvoiceCreditSubmit, request: Request,
                             principal: VerifiedAdmin = Depends(require_admin)):
                return credit_call(tenant_id, principal, request, "issue", payload)

    if billing_cancellation is not None:
        def cancellation_call(tenant_id, principal, request, operation, **payload):
            _require_admin_origin(request)
            if not TENANT_ID.fullmatch(tenant_id):
                raise HTTPException(status_code=404, detail="Tenant not found")
            # Reuse the existing read-audit vocabulary so the support-only
            # release creates no shared audit schema its predecessor rejects.
            resource = "audit.receipt" if operation == billing_cancellation.receipt else "stripe.subscription"
            read_event = audit_sensitive_read(principal, resource, tenant_id)
            try:
                if operation == billing_cancellation.apply:
                    # Mangum exposes the trusted Lambda context in ASGI scope.
                    # Reserve one second beyond the service's own outcome
                    # allowance; browsers cannot supply this execution budget.
                    context = request.scope.get("aws.context")
                    budget = 8.0
                    if context is not None:
                        remaining = context.get_remaining_time_in_millis()
                        if type(remaining) is not int or remaining <= 0:
                            raise CancellationUnavailable()
                        budget = min(8.0, remaining / 1000 - 1)
                    payload["execution_budget_seconds"] = budget
                result = operation(actor=principal.subject, tenant_id=tenant_id, **payload)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid cancellation request") from exc
            except CancellationConflict as exc:
                raise HTTPException(status_code=409, detail="Cancellation review or target changed; inspect the retained action reference") from exc
            except CancellationRateLimited as exc:
                raise HTTPException(status_code=429, detail="Cancellation inspections temporarily limited") from exc
            except Exception as exc:
                raise HTTPException(status_code=503, detail="Cancellation not verified; retain the action reference and inspect signed history") from exc
            return admin_json({"cancellation": result, "read_audit_event_id": read_event})

        @router.post("/v1/admin/subscriptions/{tenant_id}/cancellation/preview")
        def preview_cancellation(tenant_id: str, payload: CancellationPreviewRequest, request: Request,
                                 principal: VerifiedAdmin = Depends(require_admin)):
            return cancellation_call(tenant_id, principal, request, billing_cancellation.preview,
                                     **payload.model_dump())

        @router.post("/v1/admin/subscriptions/{tenant_id}/cancellation/receipt")
        def cancellation_receipt(tenant_id: str, payload: CancellationReceiptRequest, request: Request,
                                 principal: VerifiedAdmin = Depends(require_admin)):
            return cancellation_call(tenant_id, principal, request, billing_cancellation.receipt,
                                     **payload.model_dump())

        if new_cancellation_state_enabled() and billing_cancellation.provider is not None:
            @router.post("/v1/admin/subscriptions/{tenant_id}/cancel-at-period-end")
            def schedule_cancellation(tenant_id: str, payload: CancellationRequest, request: Request,
                                      principal: VerifiedAdmin = Depends(require_admin)):
                return cancellation_call(tenant_id, principal, request, billing_cancellation.apply,
                                         **payload.model_dump())

    if billing_refunds is not None:
        def refund_call(tenant_id, principal, request, operation, **payload):
            _require_admin_origin(request)
            if not TENANT_ID.fullmatch(tenant_id):
                raise HTTPException(status_code=404, detail="Tenant not found")
            resource = "audit.receipt" if operation == billing_refunds.receipt else "stripe.invoices"
            read_event = audit_sensitive_read(principal, resource, tenant_id)
            try:
                if operation in (billing_refunds.apply, billing_refunds.refresh):
                    context = request.scope.get("aws.context")
                    budget = 8.0
                    if context is not None:
                        remaining = context.get_remaining_time_in_millis()
                        if type(remaining) is not int or remaining <= 0:
                            raise RefundUnavailable()
                        budget = min(8.0, remaining / 1000 - 1)
                    payload["execution_budget_seconds"] = budget
                result = operation(actor=principal.subject, tenant_id=tenant_id, **payload)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid refund request") from exc
            except RefundConflict as exc:
                raise HTTPException(status_code=409, detail="Refund review or target changed; inspect the retained action reference") from exc
            except RefundRateLimited as exc:
                raise HTTPException(status_code=429, detail="Refund inspections temporarily limited") from exc
            except Exception as exc:
                raise HTTPException(status_code=503, detail="Refund not verified; retain the action reference and inspect signed history") from exc
            return admin_json({"refund": result, "read_audit_event_id": read_event})

        @router.post("/v1/admin/subscriptions/{tenant_id}/refund/preview")
        def preview_refund(tenant_id: str, payload: RefundPreviewRequest, request: Request,
                           principal: VerifiedAdmin = Depends(require_admin)):
            return refund_call(tenant_id, principal, request, billing_refunds.preview,
                               **payload.model_dump())

        @router.post("/v1/admin/subscriptions/{tenant_id}/refund/receipt")
        def refund_receipt(tenant_id: str, payload: RefundReceiptRequest, request: Request,
                           principal: VerifiedAdmin = Depends(require_admin)):
            return refund_call(tenant_id, principal, request, billing_refunds.receipt,
                               **payload.model_dump())

        if new_refund_state_enabled() and billing_refunds.provider is not None:
            @router.post("/v1/admin/subscriptions/{tenant_id}/refund")
            def submit_refund(tenant_id: str, payload: RefundRequest, request: Request,
                              principal: VerifiedAdmin = Depends(require_admin)):
                return refund_call(tenant_id, principal, request, billing_refunds.apply,
                                   **payload.model_dump())

            @router.post("/v1/admin/subscriptions/{tenant_id}/refund/refresh")
            def refresh_refund(tenant_id: str, payload: RefundReceiptRequest, request: Request,
                               principal: VerifiedAdmin = Depends(require_admin)):
                return refund_call(tenant_id, principal, request, billing_refunds.refresh,
                                   **payload.model_dump())

    if billing_cap_restore is not None:
        def cap_restore_call(tenant_id, principal, request, operation, **payload):
            _require_admin_origin(request)
            if not TENANT_ID.fullmatch(tenant_id):
                raise HTTPException(status_code=404, detail="Tenant not found")
            resource = "audit.receipt" if operation == billing_cap_restore.receipt else "stripe.subscription"
            read_event = audit_sensitive_read(principal, resource, tenant_id)
            try:
                result = operation(actor=principal.subject, tenant_id=tenant_id, **payload)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid cap restoration request") from exc
            except CapRestoreConflict as exc:
                raise HTTPException(status_code=409, detail="Cap restoration review or saved account changed; inspect the retained action reference") from exc
            except Exception as exc:
                raise HTTPException(status_code=503, detail="Cap restoration not verified; retain the action reference and inspect signed history") from exc
            return admin_json({"restoration": result, "read_audit_event_id": read_event})

        @router.post("/v1/admin/tenants/{tenant_id}/allowance-cap/restore/preview")
        def preview_cap_restoration(tenant_id: str, payload: CapRestorePreviewRequest, request: Request,
                                    principal: VerifiedAdmin = Depends(require_admin)):
            return cap_restore_call(tenant_id, principal, request, billing_cap_restore.preview,
                                    **payload.model_dump())

        @router.post("/v1/admin/tenants/{tenant_id}/allowance-cap/restore/receipt")
        def cap_restoration_receipt(tenant_id: str, payload: CapRestoreReceiptRequest, request: Request,
                                    principal: VerifiedAdmin = Depends(require_admin)):
            return cap_restore_call(tenant_id, principal, request, billing_cap_restore.receipt,
                                    **payload.model_dump())

        if new_cap_restoration_state_enabled() and billing_cap_restore.provider_configured:
            @router.post("/v1/admin/tenants/{tenant_id}/allowance-cap/restore")
            def restore_request_cap(tenant_id: str, payload: CapRestoreRequest, request: Request,
                                    principal: VerifiedAdmin = Depends(require_admin)):
                return cap_restore_call(tenant_id, principal, request, billing_cap_restore.apply,
                                        **payload.model_dump())

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

    def catalog_inspection(category_id, check_id, rule_id, expected_registry_version, principal):
        if catalog_provider is None:
            raise HTTPException(status_code=503, detail="Published catalog unavailable")
        read_event = audit_sensitive_read(principal, "catalog.registry")
        try:
            result = inspect_catalog(catalog_provider, category_id=category_id,
                check_id=check_id, rule_id=rule_id,
                expected_registry_version=expected_registry_version)
        except CatalogInspectionNotFound as exc:
            raise HTTPException(status_code=404, detail="Published catalog target not found") from exc
        except CatalogInspectionConflict as exc:
            raise HTTPException(status_code=409, detail="Published catalog changed; refresh before inspection") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Published catalog inspection unavailable") from exc
        return admin_json({**result, **({"read_audit_event_id": read_event} if read_event else {})})

    @router.get("/v1/admin/catalog/categories/{category_id}/checks/{check_id}")
    def inspect_catalog_check(category_id: str, check_id: str,
            expected_registry_version: str = Query(pattern=r"^[1-9][0-9]{0,8}$", max_length=9),
            principal: VerifiedAdmin = Depends(require_admin)):
        return catalog_inspection(category_id, check_id, None, expected_registry_version, principal)

    @router.get("/v1/admin/catalog/categories/{category_id}/checks/{check_id}/rules/{rule_id}")
    def inspect_catalog_rule(category_id: str, check_id: str, rule_id: str,
            expected_registry_version: str = Query(pattern=r"^[1-9][0-9]{0,8}$", max_length=9),
            principal: VerifiedAdmin = Depends(require_admin)):
        return catalog_inspection(category_id, check_id, rule_id, expected_registry_version, principal)

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

    if catalog_publications is not None:
        def publication_result(principal, operation, *, receipt=False):
            read_event = audit_sensitive_read(principal, "audit.receipt" if receipt else "catalog.draft")
            try:
                result = operation()
            except PublicationConflict as exc:
                raise HTTPException(status_code=409, detail="Catalog publication source or request conflicts; inspect the retained action reference") from exc
            except PublicationUnavailable as exc:
                raise HTTPException(status_code=503, detail="Catalog publication evidence unavailable; recover the same action reference") from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid catalog publication confirmation or reference") from exc
            if result is None:
                raise HTTPException(status_code=404, detail="Catalog publication request not found")
            return admin_json({**result, **({"read_audit_event_id": read_event} if read_event else {})})

        @router.post("/v1/admin/catalog/publications/preview")
        def preview_publication(payload: CatalogPublicationPreviewRequest, request: Request,
                                principal: VerifiedAdmin = Depends(require_admin)):
            _require_admin_origin(request)
            return publication_result(principal, lambda: catalog_publications.preview(
                actor=principal.subject, **payload.model_dump()))

        @router.post("/v1/admin/catalog/publications/recover")
        def recover_publication(payload: CatalogPublicationRecoveryRequest, request: Request,
                                principal: VerifiedAdmin = Depends(require_admin)):
            _require_admin_origin(request)
            return publication_result(principal, lambda: catalog_publications.recover(
                actor=principal.subject, **payload.model_dump()), receipt=True)

        @router.get("/v1/admin/catalog/publications/{request_id}")
        def inspect_publication(request_id: str, principal: VerifiedAdmin = Depends(require_admin)):
            if not re.fullmatch(r"[a-f0-9]{64}", request_id):
                raise HTTPException(status_code=422, detail="Invalid catalog publication reference")
            return publication_result(principal, lambda: catalog_publications.inspect(
                actor=principal.subject, request_id=request_id), receipt=True)

        if catalog_publications.writer_enabled():
            @router.post("/v1/admin/catalog/publications")
            def request_publication(payload: CatalogPublicationRequest, request: Request,
                                    principal: VerifiedAdmin = Depends(require_admin)):
                _require_admin_origin(request)
                return publication_result(principal, lambda: catalog_publications.request(
                    actor=principal.subject, **payload.model_dump()))

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
                raise HTTPException(status_code=409,
                    detail="A zero cap for an unbound account is unavailable in this release. Use Suspend to block access.",
                    headers={"X-GuardOath-Admin-Error":"unbound-zero-cap"}) from exc
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
