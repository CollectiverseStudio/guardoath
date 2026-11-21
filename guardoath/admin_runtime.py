"""Explicit production wiring for the GuardOath operator console.

This is intentionally inert unless GUARDOATH_ADMIN_ENABLED=1. Admin access
requires a distinct Cognito public app client, the hosted DynamoDB tenant
store, and a separate HMAC secret. Never reuse customer API keys or grant
entitlements from this module.
"""
from __future__ import annotations

import os
import re
import hmac
import hashlib

from guardoath.admin_browser import BrowserAuthConfig, BoundExchange
from guardoath.admin_integration import AdminConfigurationError, mount_admin_console
from guardoath.admin_actions import AdminTenantActions
from guardoath.admin_audit import AdminAuditReader, AdminSensitiveReadAuditor
from guardoath.admin_billing import AdminSubscriptionReporter
from guardoath.admin_billing_reconcile import AdminBillingDowngrade
from guardoath.admin_catalog_drafts import AdminCatalogDrafts

_HEX_KEY = re.compile(r"^[a-fA-F0-9]{64,128}$")


def configure_admin_from_environment(app, *, tenants, customer_sessions=None,
                                     cognito_client=None, admin_sessions=None) -> bool:
    """Mount the owner console only after strict configuration validation.

    The existing tenant Cognito pool can be reused as the *identity provider*,
    but an independent admin OAuth app client is mandatory. The administrator
    must be in GuardOathOwners; normal customer access grants no rights.

    All network/IAM operations are lazy; mounting does not send AWS requests.
    """
    enabled = os.getenv("GUARDOATH_ADMIN_ENABLED", "0")
    if enabled == "0":
        return False
    if enabled != "1":
        raise AdminConfigurationError("GUARDOATH_ADMIN_ENABLED must be 0 or 1")
    pool = os.getenv("GUARDOATH_COGNITO_POOL_ID", "")
    domain = os.getenv("GUARDOATH_ADMIN_COGNITO_DOMAIN", "")
    client_id = os.getenv("GUARDOATH_ADMIN_CLIENT_ID", "")
    key_hex = os.getenv("GUARDOATH_ADMIN_CURSOR_HMAC_KEY", "")
    if not _HEX_KEY.fullmatch(key_hex):
        raise AdminConfigurationError("Independent admin cursor key must be 32–64 random bytes encoded as hex")
    if not pool or not domain or not client_id:
        raise AdminConfigurationError("Admin Cognito pool, domain and public client are required")
    if customer_sessions is not None and getattr(customer_sessions, "client_id", None) == client_id:
        raise AdminConfigurationError("A distinct admin OAuth client is required")
    config = BrowserAuthConfig(domain=domain, client_id=client_id,
                               redirect_uri="https://guardoath.com/admin/login")
    if admin_sessions is None:
        from guardoath.tenant_sessions import CognitoSessions
        # Only the signed access-token verification methods are used here; the
        # admin callback and OAuth code exchange use BrowserAuthConfig above.
        # The tenant session constructor currently validates /dashboard only.
        # Do not use its .exchange() for the admin callback.
        admin_sessions = CognitoSessions(pool_id=pool, client_id=client_id,
                                         domain=domain, redirect_uri="https://guardoath.com/dashboard")
    if (getattr(admin_sessions, "pool_id", None) != pool or
            getattr(admin_sessions, "client_id", None) != client_id or
            getattr(admin_sessions, "domain", None) != domain):
        raise AdminConfigurationError("Admin token verifier must match the configured pool and client")
    if cognito_client is None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise AdminConfigurationError("AWS SDK is required for admin authentication") from exc
        # Bounded provider calls; permissions are administered through the
        # Lambda execution role, not tokens or credentials in the browser.
        cognito_client = boto3.client("cognito-idp", region_name=pool.rsplit("_", 1)[0],
                                     config=Config(connect_timeout=1, read_timeout=2,
                                                   retries={"total_max_attempts": 2}))
    # Production owner data reads require the separate signed audit table.
    # Without one, /admin/login remains usable but protected reads return 503;
    # the customer app stays available and cannot expose unaudited owner data.
    # Mutations additionally require the same audit table.
    audit_table = os.getenv("GUARDOATH_ADMIN_AUDIT_TABLE", "")
    mutator = None
    audit_reader = None
    read_auditor = None
    catalog_drafts = None
    if audit_table:
        if audit_table == getattr(tenants.store, "table_name", None):
            raise AdminConfigurationError("Admin audit table must be independent")
        audit_signing_key = hmac.new(bytes.fromhex(key_hex), b"guardoath-admin-audit-v1", hashlib.sha256).digest()
        audit_reader = AdminAuditReader(tenants.store._client, audit_table, verify_key=audit_signing_key)
        read_auditor = AdminSensitiveReadAuditor(tenants.store._client, audit_table,
                                                 signing_key=audit_signing_key)
        mutator = AdminTenantActions(tenants.store._client, tenants.store.table_name,
                                     audit_table, audit_key=audit_signing_key)
        catalog_drafts = AdminCatalogDrafts(tenants.store._client, audit_table, audit_signing_key)
    billing_reporter = None
    billing_invoices = None
    if os.getenv("GUARDOATH_STRIPE_SECRET_KEY", ""):
        secret = os.environ["GUARDOATH_STRIPE_SECRET_KEY"]
        price = os.getenv("GUARDOATH_STRIPE_PRICE_ID", "")
        if (not re.fullmatch(r"price_[A-Za-z0-9_]{1,96}", price) or
                not re.fullmatch(r"sk_(?:live|test)_[A-Za-z0-9_]{11,}", secret)):
            raise AdminConfigurationError("Admin Stripe verification requires server-side secret and valid configured price")
        def verified_stripe(tenant_id, customer_id, subscription_id):
            from guardoath.stripe_provider import fetch_subscription_snapshot
            from guardoath.subscription_reconciliation import StripeBinding
            binding = StripeBinding(tenant_id, customer_id, subscription_id)
            return fetch_subscription_snapshot(binding, secret)
        billing_reporter = AdminSubscriptionReporter(
            verified_stripe, expected_price_id=price, expected_livemode=secret.startswith("sk_live_"))
        from guardoath.admin_invoices import AdminInvoiceHistory, fetch_invoice_list
        from guardoath.subscription_reconciliation import StripeBinding
        def verified_invoices(tenant_id, customer_id, subscription_id):
            return fetch_invoice_list(StripeBinding(tenant_id, customer_id, subscription_id), secret)
        billing_invoices = AdminInvoiceHistory(verified_invoices, billing_reporter)
    record_validator = rule_exists = None
    if mutator is not None:
        from guardoath.tenant_record import TenantRecord
        from guardoath.rule_catalog import RULES
        record_validator = TenantRecord.model_validate
        rule_exists = lambda rule_id: rule_id in RULES
    billing_downgrade = None
    if mutator is not None and billing_reporter is not None:
        billing_downgrade = AdminBillingDowngrade(
            tenants.store._client, tenants.store.table_name, audit_table,
            signing_key=audit_signing_key, reporter=billing_reporter,
            validate_record=record_validator)
    mount_admin_console(app, sessions=admin_sessions, tenants=tenants, cognito=cognito_client,
                        cursor_signing_key=bytes.fromhex(key_hex), browser_config=config,
                        browser_exchange=BoundExchange(config), admin_mutator=mutator,
                        audit_reader=audit_reader, read_auditor=read_auditor,
                        catalog_drafts=catalog_drafts,
                        require_persistent_read_audit=True,
                        billing_reporter=billing_reporter, billing_downgrade=billing_downgrade,
                        billing_invoices=billing_invoices,
                        record_validator=record_validator,rule_exists=rule_exists)
    return True
