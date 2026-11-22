"""Public API documentation, without changing request authentication or execution.

The edge-specific transport and the customer's two authentication alternatives
cannot be inferred from FastAPI's optional header dependencies. Enrich only the
generated OpenAPI document. The response models below document JSONResponse
payloads; they are not installed as runtime response filters.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Literal

from fastapi import FastAPI
from pydantic import Field

from guardoath.models import CheckResponse, Contract


class ApiError(Contract):
    """Application errors use a short detail string, not validation input dumps."""

    detail: str


class EnrollmentCodeMismatchError(ApiError):
    code: Literal["code_mismatch"]


class AttachmentReceipt(Contract):
    """Metadata for actual received bytes; recognition is not a clean scan."""

    index: int = Field(ge=0, le=2)
    kind: Literal["csv", "tsv", "pdf", "zip_container", "ole_container", "image", "unknown"]
    status: Literal["recognized", "unsupported", "invalid"]
    byte_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(description="Fixed format/inspection reason code; never file contents.")
    evaluation_status: Literal["complete", "incomplete", "not_inspected_policy_off"]


class AttachmentCheckResponse(CheckResponse):
    attachments: tuple[AttachmentReceipt, ...] = Field(min_length=1, max_length=3)


class AgentActivityResponse(Contract):
    """Caller-reported comparison, never execution authority or a stored receipt."""

    status: Literal["no_observation", "matching_unverified", "mismatch"]
    matches: bool
    verified_execution: Literal[False]
    finding_ids: tuple[str, ...] = Field(max_length=1)
    authorization: Literal["not_granted"]
    tenant_id: str
    mode: Literal["diagnostic_unverified"]
    limitations: str


ATTACHMENT_RESPONSES = {
    200: {
        "model": AttachmentCheckResponse,
        "description": "Check completed with byte receipts. HTTP 200 can contain an incomplete/Block decision.",
    },
}
AGENT_ACTIVITY_RESPONSES = {
    200: {
        "model": AgentActivityResponse,
        "description": "Diagnostic comparison only: no action execution, authorization or durable event recording.",
    },
}

_SESSION_ONLY_PATHS = frozenset({
    "/v1/tenants/current/keys", "/v1/tenants/current/keys/revoke",
    "/v1/account/status", "/v1/register",
    "/v1/billing/checkout", "/v1/billing/portal", "/v1/billing/reconcile",
})
_OWNER_OR_DEMO_PATHS = frozenset({
    "/v1/tenants/current/statistical-drift", "/v1/tenants/current/checks",
    "/v1/tenants/current/references", "/v1/tenants/current/reset",
})
_CHECK_PATHS = frozenset({
    "/v1/checks/input", "/v1/checks/output",
    "/v1/checks/input/attachments", "/v1/checks/output/attachments",
})
_ENTITLED_PATHS = _CHECK_PATHS | {
    "/v1/sigma/demo", "/v1/agent-activity/compare",
}
_BODY_HASH = {
    "name": "x-amz-content-sha256", "in": "header", "required": True,
    "description": (
        "Required by https://guardoath.com for POST requests: lowercase SHA-256 of the exact UTF-8 "
        "body bytes. Serialize once, hash those bytes, and send them unchanged. For an empty body "
        "hash the empty byte string. The CloudFront origin-signing boundary can return a non-JSON "
        "403 when it is absent or mismatched. This transport header is not an API credential and "
        "is not required by a direct local ASGI server."
    ),
    "schema": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
}
_TAGS = [
    {"name": "Validation", "description": "Start here: check input or a supplied answer with the authenticated tenant's saved controls. No AI/model invocation."},
    {"name": "Customer policy", "description": "Inspect bound tenant policy; customer settings changes require a signed-in owner session."},
    {"name": "Customer access", "description": "Public demo/sign-in entry, customer registration, API-key management and subscription operations."},
    {"name": "Diagnostics", "description": "Synthetic event tests and unverified action-report comparison; no agent execution."},
    {"name": "Public catalog", "description": "Published capabilities, not a request-time policy override."},
    {"name": "System", "description": "Public service health and build identity."},
    {"name": "Administrator sign-in", "description": "Public owner sign-in/bootstrap endpoints. They do not grant protected data access without independent owner/MFA verification."},
    {"name": "Owner administration", "description": "Separate protected owner operations: signed recent MFA and live owner membership are mandatory; customer credentials are insufficient."},
]
_ERRORS = {
    "400": "Malformed/ambiguous JSON request, duplicate JSON keys, or parser limits exceeded.",
    "401": "Missing, invalid, expired or revoked credential/session for this operation; customer and administrator authority are separate.",
    "402": "A verified active subscription is required for this customer operation; registration is insufficient.",
    "403": "Credential lacks this tenant/operation permission, or the production edge rejected the request body hash.",
    "404": "Requested tenant policy/resource or configured capability is unavailable.",
    "408": "Request-body read deadline exceeded.",
    "409": "Saved state/revision conflicts with this operation; reload before deciding whether to retry.",
    "413": "Request body exceeds this operation's byte limit.",
    "415": "UTF-8 JSON content type is required; compressed request bodies are unsupported.",
    "422": "Invalid request or unsupported tenant validation-pack selection. Submitted values are not echoed by schema errors.",
    "429": "Request capacity, rate or monthly check allowance reached. A completed check is not idempotent.",
    "503": "Required configuration, authentication or service is unavailable; do not infer an allowed check.",
}


def _has_admin_dependency(dependant) -> bool:
    return any(
        getattr(child.call, "__name__", "") == "require_admin" or _has_admin_dependency(child)
        for child in getattr(dependant, "dependencies", ())
    )


def _protected_admin_operations(routes, prefix="") -> set[tuple[str, str]]:
    operations = set()
    for route in routes:
        # FastAPI >=0.162 keeps included routers deferred. Earlier versions
        # expand them into APIRoutes. Inspect either representation only for
        # schema metadata; never replace or execute an authentication dependency.
        original = getattr(route, "original_router", None)
        if original is not None:
            context = getattr(route, "include_context", None)
            operations.update(_protected_admin_operations(
                original.routes, prefix + getattr(context, "prefix", "")))
        elif _has_admin_dependency(getattr(route, "dependant", None)):
            operations.update((prefix + route.path, method.lower())
                              for method in getattr(route, "methods", ()))
    return operations


def _tag(path: str) -> str:
    if path in _CHECK_PATHS:
        return "Validation"
    if path.startswith("/v1/admin/auth/"):
        return "Administrator sign-in"
    if path.startswith("/v1/admin/"):
        return "Owner administration"
    if path in {"/v1/applications/current", "/v1/validation-packs"} or (
        path.startswith("/v1/tenants/current/") and "/keys" not in path
    ):
        return "Customer policy"
    if path.startswith(("/v1/sigma/", "/v1/agent-activity/")):
        return "Diagnostics"
    if path == "/v1/controls":
        return "Public catalog"
    if path == "/healthz":
        return "System"
    return "Customer access"


def _error_response(code: str) -> dict:
    response = {
        "description": _ERRORS[code],
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}},
    }
    if code == "403":
        for media in ("text/html", "text/plain"):
            response["content"][media] = {"schema": {"type": "string"}}
    if code in {"429", "503"}:
        response["headers"] = {"Retry-After": {
            "description": "When present, seconds before a new attempt; not an idempotency guarantee.",
            "schema": {"type": "string"},
        }}
    return response


def install_api_contract(api: FastAPI) -> None:
    """Document hosted behavior after all routes exist; leave their dependencies intact."""
    original_openapi = api.openapi

    def documented_openapi():
        if api.openapi_schema is not None:
            return api.openapi_schema
        schema = original_openapi()
        schema["info"]["description"] = (
            "Tenant-authoritative content validation. Call /v1/checks/input before your own AI/model "
            "invocation and /v1/checks/output on the supplied answer. These are independent requests: "
            "an output check does not require or prove an input check, and neither invokes a model "
            "or agent. Model/provider/agent selection, a hosted /v1/interactions route, trusted "
            "conversation history and a durable AI-call ledger are not implemented. HTTP 200 is a "
            "validation result, not permission by itself; inspect effective_action, evaluation_status, "
            "checker_results and redaction. Tenant settings and explicit Off remain authoritative."
        )
        schema["servers"] = [{
            "url": "/",
            "description": (
                "Current origin: interactive requests stay on the server serving this specification. "
                "Canonical production origin is https://guardoath.com; production POSTs require the exact-body hash."
            ),
        }]
        schema["externalDocs"] = {"description": "Integration guide and decision semantics", "url": "https://guardoath.com/guide"}
        schema["tags"] = deepcopy(_TAGS)
        components = schema.setdefault("components", {})
        components.setdefault("schemas", {})["ApiError"] = ApiError.model_json_schema()
        security = components.setdefault("securitySchemes", {})
        security["GuardOathApiKey"] = {
            "type": "apiKey", "in": "header", "name": "X-GuardOath-Key",
            "description": "Tenant-bound application API key; keep customer keys on the customer's server. Shared demo keys are public.",
        }
        security["GuardOathCustomerSession"] = {
            "type": "apiKey", "in": "header", "name": "X-GuardOath-Session",
            "description": "Verified Cognito customer access token used by the dashboard. Not an administrator session. Do not send both customer auth headers.",
        }
        # Derive protected owner operations from their actual existing dependency,
        # rather than making every public administrator bootstrap endpoint private.
        protected_admin = _protected_admin_operations(api.routes)
        if protected_admin:
            security["GuardOathAdminSession"] = {
                "type": "apiKey", "in": "header", "name": "X-GuardOath-Admin-Session",
                "description": (
                    "Separate Cognito administrator access token: requires signed current-login MFA, "
                    "recent auth_time, enabled identity and live owner membership. A customer key or "
                    "customer session never grants owner access."
                ),
            }
        if "HTTPBearer" in security:
            security["HTTPBearer"]["description"] = (
                "Legacy/direct-local-server authentication only. On guardoath.com CloudFront replaces "
                "Authorization; use X-GuardOath-Key or the supported customer-session header."
            )
        for path, item in schema["paths"].items():
            for method, operation in item.items():
                if method not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                    continue
                operation["tags"] = [_tag(path)]
                if not path.startswith("/v1/"):
                    continue
                parameters = operation.setdefault("parameters", [])
                names = {p.get("name", "").lower() for p in parameters if p.get("in") == "header"}
                key_auth = "x-guardoath-key" in names
                session_auth = "x-guardoath-session" in names
                descriptions = [operation.get("description", "")]
                if key_auth or session_auth:
                    operation["parameters"] = [p for p in parameters if not (
                        p.get("in") == "header" and p.get("name", "").lower() in {
                            "x-guardoath-key", "x-guardoath-session"})]
                    session_only = path in _SESSION_ONLY_PATHS or not key_auth
                    operation["security"] = ([{"GuardOathCustomerSession": []}] if session_only else
                                             [{"GuardOathApiKey": []}, {"GuardOathCustomerSession": []}])
                    if key_auth and not session_only:
                        operation["x-local-security"] = [{"HTTPBearer": []}]
                    descriptions.append(
                        "Requires a verified customer session; production API keys do not authorize this operation."
                        if session_only else
                        "Use X-GuardOath-Key for a server integration, or a verified X-GuardOath-Session for the dashboard. "
                        "Do not combine credentials. Authorization: Bearer is supported only when calling a direct local server."
                    )
                    if path in _OWNER_OR_DEMO_PATHS:
                        descriptions.append("Customer settings require a signed-in owner session. A public demo key can manage only its own shared demo record; reset is demo-only.")
                admin_auth = (path, method) in protected_admin
                if admin_auth:
                    operation["security"] = [{"GuardOathAdminSession": []}]
                    descriptions.append(
                        "Protected owner operation. Send X-GuardOath-Admin-Session with independently "
                        "verified signed recent MFA and live owner membership; customer keys/sessions do "
                        "not authorize it. Protected data reads require a persisted signed audit receipt."
                    )
                if path in _CHECK_PATHS:
                    descriptions.append(
                        "The authenticated tenant's saved policy runs; omit policy_version or use tenant. "
                        "A different application_id is refused. Each HTTP 200 result counts as one check, "
                        "including Block; no request-id idempotency or stored check-receipt lookup is provided. "
                        "No model/agent call is made. Output checks are independent of input checks; the "
                        "caller controls sequencing and content release. A tenant policy can change between requests."
                    )
                    operation["responses"]["200"]["description"] = (
                        "A validation result, including Allow/Flag/Redact/Block or incomplete. Inspect the decision, not HTTP success alone."
                    )
                    operation["requestBody"]["content"]["application/json"]["examples"] = {
                        "text": {"summary": "Synthetic tenant input or supplied answer", "value": {
                            "application_id": "demo1", "content": "What is the return policy?", "content_format": "text",
                            **({"attachments_b64": ["bmFtZSx2YWx1ZQpTeW50aGV0aWMsMjU="]} if path.endswith("/attachments") else {}),
                        }},
                    }
                if path == "/v1/agent-activity/compare":
                    descriptions.append("Caller-reported proposed/observed comparison only. It never authorizes or executes an action, authenticates telemetry or persists a receipt.")
                if method == "post":
                    operation["parameters"].append(deepcopy(_BODY_HASH))
                    if admin_auth or path.startswith(("/v1/admin/auth/native/", "/v1/admin/auth/enrollment/")):
                        operation["parameters"].append({
                            "name": "Origin", "in": "header", "required": True,
                            "description": "Production administrator POSTs require this exact origin in addition to their identity/challenge checks. An origin is not authentication.",
                            "schema": {"type": "string", "enum": ["https://guardoath.com"]},
                        })
                codes = {"429", "503"}
                if method == "post":
                    codes |= {"400", "403", "408", "413", "415", "422"}
                if key_auth or session_auth or admin_auth:
                    codes |= {"401", "403", "404", "422"}
                if path in _ENTITLED_PATHS or (path == "/v1/tenants/current/keys" and method == "post"):
                    codes.add("402")
                if method == "post" and (path in _SESSION_ONLY_PATHS or path in _OWNER_OR_DEMO_PATHS):
                    codes.add("409")
                if path.startswith("/v1/admin/auth/") and method == "post":
                    codes.add("401")
                for code in sorted(codes, key=int):
                    # Replace FastAPI's autogenerated input-echoing 422 shape.
                    operation.setdefault("responses", {})[code] = _error_response(code)
                if method == "post":
                    operation["responses"]["413"]["description"] = (
                        "Authenticator setup request body exceeds 24,000 bytes."
                        if path.startswith("/v1/admin/auth/enrollment/") else
                        "JSON request body exceeds 262,144 bytes."
                    )
                if path == "/v1/admin/auth/enrollment/finish" and method == "post":
                    components["schemas"]["EnrollmentCodeMismatchError"] = EnrollmentCodeMismatchError.model_json_schema()
                    operation["responses"]["400"]["content"]["application/json"]["schema"] = {"oneOf": [
                        {"$ref": "#/components/schemas/ApiError"},
                        {"$ref": "#/components/schemas/EnrollmentCodeMismatchError"},
                    ]}
                    operation["responses"]["400"]["description"] = (
                        "Invalid setup request, or a confirmed code mismatch with code=code_mismatch. "
                        "Only that explicit mismatch permits the same bounded pending flow to retry."
                    )
                if descriptions:
                    operation["description"] = "\n\n".join(part for part in descriptions if part)
        api.openapi_schema = schema
        return schema

    api.openapi = documented_openapi
