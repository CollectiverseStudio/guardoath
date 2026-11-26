"""Explicit, opt-in GuardOath admin mount using the existing hosted services.

No implicit admin privileges or fallback data source. The production caller must
supply its already-configured CognitoSessions, a tightly scoped Cognito client,
and the existing DynamoDB-backed TenantService. Nothing here creates IAM roles,
accounts, tables, sessions, users, or payment entitlements.
"""
from __future__ import annotations

from fastapi import FastAPI

from guardoath.admin_catalog import published_catalog
from guardoath.catalog_metadata import load_release_history
from guardoath.admin_browser import BrowserAuthConfig, BoundExchange, create_browser_router
from guardoath.admin_native_mfa import CognitoNativeMfa
from guardoath.admin_cognito import make_verifier
from guardoath.admin_console import create_admin_router
from guardoath.admin_directory import DynamoAdminDirectory
from guardoath.admin_users import CognitoUserDirectory


class AdminConfigurationError(ValueError):
    """Refuse to mount admin endpoints with incomplete production safeguards."""


def _all_route_paths(routes):
    """Handle FastAPI/Starlette versions that expose included router wrappers.

    Routing may be represented as APIRoute objects or nested _IncludedRouter
    objects. Inspect both so duplicate registration cannot be hidden by an
    implementation-specific route wrapper.
    """
    for route in routes:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            yield path
        children = getattr(route, "routes", None)
        if children is None:
            children = getattr(getattr(route, "router", None), "routes", ())
        if children:
            yield from _all_route_paths(children)


def mount_admin_console(app: FastAPI, *, sessions, tenants, cognito,
                        cursor_signing_key: bytes,
                        browser_config: BrowserAuthConfig | None = None,
                        browser_exchange: BoundExchange | None = None,
                        native_auth: CognitoNativeMfa | None = None,
                        native_enrollment=None,
                        admin_mutator=None, audit_reader=None, read_auditor=None, catalog_drafts=None,
                        catalog_publications=None,
                        require_persistent_read_audit: bool = False,
                        billing_reporter=None, billing_downgrade=None, billing_reconciler=None, billing_invoices=None,
                        billing_cancellation=None,
                        billing_configuration=None,
                        record_validator=None, rule_exists=None) -> None:
    """Register the guarded read-only admin routes on an existing GuardOath app.

    Must be called only after production configuration is validated. A caller
    cannot substitute memory-store enumeration for production DynamoDB pages.
    Admin MFA assurance depends on verifiable per-login signed Cognito evidence;
    absent evidence is rejected by the verifier (no bypass).
    """
    if not isinstance(app, FastAPI):
        raise AdminConfigurationError("A FastAPI application is required")
    if sessions is None or not callable(getattr(sessions, "verified_claims", None)) or not getattr(sessions, "pool_id", None):
        raise AdminConfigurationError("Verified Cognito sessions are required")
    if cognito is None or not all(callable(getattr(cognito, method, None)) for method in
                                   ("get_user", "admin_get_user", "admin_list_groups_for_user")):
        raise AdminConfigurationError("Cognito account and owner-group verification are required")
    store = getattr(tenants, "store", None)
    if store is None or not getattr(store, "table_name", None) or not getattr(store, "_client", None) or not callable(getattr(store, "get", None)):
        raise AdminConfigurationError("An existing DynamoDB tenant store is required")
    if not isinstance(cursor_signing_key, bytes) or len(cursor_signing_key) < 32:
        raise AdminConfigurationError("A separate server-side 32-byte cursor secret is required")
    # FastAPI resolves routes in declaration order. A pre-existing route at
    # any reserved admin path can shadow our authenticated endpoint, so check
    # the entire namespace before mounting (including nested router paths).
    existing_paths = set(_all_route_paths(app.routes))
    if (getattr(app.state, "guardoath_admin_mounted", False) or
            any(path == "/admin" or path.startswith("/admin/") or
                path == "/v1/admin" or path.startswith("/v1/admin/")
                for path in existing_paths)):
        raise AdminConfigurationError("An administration route is already registered")

    # The browser uses the SAME signed-token identity authority as /v1/admin.
    # Explicit opt-in: a missing callback or app-client match cannot downgrade
    # to unverified access, even when the base read-only router is present.
    if (browser_config is None) != (browser_exchange is None):
        raise AdminConfigurationError("Admin browser callback and exchange are both required")
    if native_auth is not None and browser_config is None:
        raise AdminConfigurationError("Native admin MFA requires the bound browser verifier")
    if (native_auth is not None and browser_config is not None and
            getattr(native_auth, "client_id", None) != browser_config.client_id):
        raise AdminConfigurationError("Native admin challenge client must match the signed token verifier")
    if native_enrollment is not None:
        from guardoath.admin_enrollment import CognitoNativeEnrollment
        if (not isinstance(native_enrollment, CognitoNativeEnrollment)
                or native_auth is None or browser_config is None
                or getattr(native_enrollment, "client_id", None) != browser_config.client_id
                or getattr(native_enrollment, "pool_id", None) != sessions.pool_id
                or native_enrollment._sessions is not sessions
                or native_enrollment._state._client is not store._client
                or getattr(getattr(native_enrollment, "_state", None), "table_name", None) == store.table_name):
            raise AdminConfigurationError("Owner enrollment requires native sign-in and independent bound state")
    if browser_config is not None:
        if (getattr(sessions, "client_id", None) != browser_config.client_id or
                getattr(sessions, "domain", None) != browser_config.domain or
                browser_exchange.config != browser_config):
            raise AdminConfigurationError("Admin browser app client must match token verifier")
        if set(_all_route_paths(app.routes)).intersection({"/admin/login", "/admin/client.js", "/v1/admin/auth/exchange"}):
            raise AdminConfigurationError("Admin browser login has already been mounted")

    directory = DynamoAdminDirectory(store._client, store.table_name, cursor_signing_key)
    if billing_reconciler is not None and (
            getattr(billing_reconciler, "tenant_table", None) != store.table_name
            or getattr(billing_reconciler, "ddb", None) is not store._client):
        raise AdminConfigurationError("Subscription reconciliation must use the authoritative tenant store")
    if billing_cancellation is not None and (
            getattr(billing_cancellation, "tenant_table", None) != store.table_name
            or getattr(getattr(billing_cancellation, "state", None), "client", None) is not store._client):
        raise AdminConfigurationError("Cancellation must use the authoritative tenant store")
    if catalog_publications is not None and (
            getattr(getattr(catalog_publications, "state", None), "client", None) is not store._client
            or getattr(getattr(catalog_publications, "state", None), "table_name", None) == store.table_name
            or getattr(catalog_publications, "drafts", None) is not catalog_drafts):
        raise AdminConfigurationError("Catalog publication must use the independent signed catalog audit store")
    users = CognitoUserDirectory(cognito, sessions.pool_id, cursor_signing_key) if callable(getattr(cognito, 'list_users', None)) else None
    verifier = make_verifier(sessions, cognito)
    app.include_router(create_admin_router(
        verify_cognito_admin=verifier,
        tenant_store=store,
        admin_directory=directory,
        admin_user_directory=users,
        catalog_provider=published_catalog,
        catalog_history_provider=load_release_history,
        admin_mutator=admin_mutator,
        audit_reader=audit_reader,
        read_auditor=read_auditor,
        catalog_drafts=catalog_drafts,
        catalog_publications=catalog_publications,
        require_persistent_read_audit=require_persistent_read_audit,
        billing_reporter=billing_reporter,
        billing_downgrade=billing_downgrade,
        billing_reconciler=billing_reconciler,
        billing_invoices=billing_invoices,
        billing_cancellation=billing_cancellation,
        billing_configuration=billing_configuration,
        record_validator=record_validator,
        rule_exists=rule_exists,
        browser_entry_enabled=browser_config is not None,
    ))
    if browser_config is not None and browser_exchange is not None:
        app.include_router(create_browser_router(browser_config, browser_exchange, verifier,
                                                 native_auth=native_auth,
                                                 native_enrollment=native_enrollment))

    app.state.guardoath_admin_mounted = True
