"""Bounded hosted-model acceptance: read-only by default, at most one opt-in POST.

The API key is read only from GUARDOATH_API_KEY. Production traffic uses one
fixed HTTPS origin and never follows redirects. Reports exclude tenant/request
identifiers, credentials, prompts, answers and provider error bodies.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import http.client
import json
import os
import re

from guardoath.invocations.contracts import (
    ConnectionCatalog, ID_PATTERN, InvocationReceipt, InvocationRequest,
    InvocationResponse, TargetSnapshot,
)
from guardoath.json_data import MAX_JSON_BYTES, parse_json
from guardoath.models import Action
from guardoath.tenant_record import TENANT_ID_PATTERN, tenant_id_from_key


HOST = "guardoath.com"
SYNTHETIC_MESSAGE = "For this synthetic connectivity test, reply with a short greeting."


@dataclass(frozen=True)
class WireResponse:
    status: int
    body: bytes


class HTTPSExchange:
    """One HTTPS request, with bounded reads and no redirect/retry machinery."""

    def __call__(self, method, path, body, headers):
        connection = http.client.HTTPSConnection(HOST, timeout=15)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            try:
                # Error/redirect bodies are neither needed nor retained.
                data = response.read(MAX_JSON_BYTES + 1) if response.status == 200 else b""
                if len(data) > MAX_JSON_BYTES:
                    raise ValueError("Response exceeds verification bound")
                return WireResponse(response.status, data)
            finally:
                response.close()
        finally:
            connection.close()


class ProbeFailure(Exception):
    """Only internally selected constant issue codes reach the report."""


def _exchange(transport, method, path, headers, *, body=None, allow_missing=False):
    try:
        reply = transport(method, path, body, headers)
    except Exception:
        raise ProbeFailure("transport_unavailable") from None
    if allow_missing and reply.status == 404:
        return None
    if reply.status != 200:
        raise ProbeFailure("http_not_successful")
    try:
        if not isinstance(reply.body, bytes) or len(reply.body) > MAX_JSON_BYTES:
            raise ValueError
        value = parse_json(reply.body.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except Exception:
        raise ProbeFailure("invalid_response") from None


def _bound_receipt(value, request, target):
    receipt = InvocationReceipt.model_validate(value)
    if (receipt.application_id != request.application_id or
            receipt.request_id != request.request_id or receipt.target != target or
            receipt.sequence < 1 or receipt.event_digest == "0" * 64 or
            (receipt.dispatch_state != "not_dispatched" and
             receipt.reserved_output_tokens != request.max_output_tokens) or
            (receipt.usage is not None and receipt.usage.output_tokens is not None and
             receipt.usage.output_tokens > request.max_output_tokens)):
        raise ValueError("Receipt does not match this request and approved target")
    return receipt


def _completed_checks(receipt):
    return (receipt.status == "completed" and receipt.dispatch_state == "response_observed"
            and all(check is not None and check.evaluation_status == "complete"
                    and not check.simulated and check.decision != Action.BLOCK
                    and all(result.status not in {"error", "unsupported"}
                            for result in check.checker_results)
                    for check in (receipt.input_check, receipt.output_check)))


def verify(*, application_id, api_key, invoke=False, connection_id=None,
           model_id=None, request_id=None, max_output_tokens=128,
           expected_sha=None, transport=None):
    """Inspect readiness or make one explicitly selected synthetic invocation.

    Injected transport is for offline tests. A timeout can mean remote execution
    occurred; only a receipt GET follows, never another generation attempt.
    """
    report = {
        "mode": "one_call" if invoke else "read_only",
        "passed": False,
        "health_ok": False,
        "source_sha_verified": False,
        "authenticated_catalog_ok": False,
        "invocation_configured": False,
        "catalog_reason_code": None,
        "approved_target_verified": False,
        "post_attempted": False,
        "existing_receipt_detected": False,
        "original_checked_response_verified": False,
        "receipt_recovered": False,
        "durable_receipt_correlated": False,
        "receipt_status": None,
        "keep_request_id": False,
        "full_production_acceptance": False,
        "issues": [],
    }
    try:
        if (not isinstance(application_id, str) or
                not re.fullmatch(TENANT_ID_PATTERN, application_id) or
                not isinstance(api_key, str) or tenant_id_from_key(api_key) != application_id or
                not api_key.isascii() or any(not 33 <= ord(c) <= 126 for c in api_key) or
                type(invoke) is not bool or type(max_output_tokens) is not int or
                not 1 <= max_output_tokens <= 256 or
                (expected_sha is not None and
                 (not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_sha)))):
            raise ValueError
        if invoke and any(not isinstance(value, str) or not re.fullmatch(ID_PATTERN, value)
                          for value in (connection_id, model_id, request_id)):
            raise ValueError
        # A read-only run never silently consumes invocation-only arguments.
        if not invoke and any(value is not None for value in (connection_id, model_id, request_id)):
            raise ValueError
    except Exception:
        report["issues"].append("invalid_arguments_or_credential")
        return report

    transport = transport or HTTPSExchange()
    public_headers = {"Accept": "application/json", "User-Agent": "GuardOath-Model-Acceptance"}
    headers = {**public_headers, "X-GuardOath-Key": api_key}
    try:
        health = _exchange(transport, "GET", "/healthz", public_headers)
        if health.get("status") != "ok":
            raise ValueError
        report["health_ok"] = True
        if expected_sha is not None:
            if health.get("source_sha") != expected_sha:
                report["issues"].append("source_sha_mismatch")
                return report
            report["source_sha_verified"] = True
    except Exception:
        report["issues"].append("health_unavailable")
        return report
    try:
        catalog = ConnectionCatalog.model_validate(
            _exchange(transport, "GET", "/v1/connections", headers))
        if catalog.application_id != application_id:
            raise ValueError
        ids = [connection.id for connection in catalog.connections]
        if len(ids) != len(set(ids)) or any(
            len({model.id for model in connection.models}) != len(connection.models)
            for connection in catalog.connections
        ):
            raise ValueError
        report["authenticated_catalog_ok"] = True
        report["catalog_reason_code"] = catalog.reason_code
        report["invocation_configured"] = (catalog.available and catalog.reason_code == "ready"
                                             and bool(catalog.connections))
    except Exception:
        report["issues"].append("catalog_unavailable_or_mismatched")
        return report
    if not report["invocation_configured"]:
        report["issues"].append("invocation_not_ready")
        return report
    if not invoke:
        report["passed"] = True
        return report

    try:
        connection = next(item for item in catalog.connections if item.id == connection_id)
        model = next(item for item in connection.models if item.id == model_id)
        if max_output_tokens > min(model.max_output_tokens, connection.monthly_output_token_limit):
            raise ValueError
        request = InvocationRequest(application_id=application_id, request_id=request_id,
            target={"kind": "model", "connection_id": connection_id, "model_id": model_id},
            message=SYNTHETIC_MESSAGE, max_output_tokens=max_output_tokens)
        target = TargetSnapshot(connection_id=connection.id, connection_version=connection.version,
            provider=connection.provider, region=connection.region, model_id=model.id,
            provider_model_id=model.provider_model_id)
        report["approved_target_verified"] = True
        recovery_path = "/v1/interactions/by-request/" + request_id
        existing = _exchange(transport, "GET", recovery_path, headers, allow_missing=True)
        if existing is not None:
            receipt = _bound_receipt(existing, request, target)
            report.update(existing_receipt_detected=True, receipt_recovered=True,
                          receipt_status=receipt.status, keep_request_id=True)
            report["issues"].append("request_id_already_used")
            return report
    except Exception:
        report["keep_request_id"] = True
        report["issues"].append("target_or_request_preflight_failed")
        return report

    body = json.dumps(request.model_dump(mode="json"), separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    post_headers = {**headers, "Content-Type": "application/json",
                    "x-amz-content-sha256": hashlib.sha256(body).hexdigest()}
    original = None
    report.update(post_attempted=True, keep_request_id=True)
    try:
        result = InvocationResponse.model_validate(
            _exchange(transport, "POST", "/v1/interactions", post_headers, body=body))
        original = _bound_receipt(result.receipt.model_dump(mode="json"), request, target)
        report["receipt_status"] = original.status
        report["original_checked_response_verified"] = (
            not result.replayed and isinstance(result.response, str) and bool(result.response.strip())
            and _completed_checks(original))
        if not report["original_checked_response_verified"]:
            report["issues"].append("original_checked_response_unconfirmed")
    except Exception:
        report["issues"].append("original_result_unconfirmed")
    # The original POST may have reached the service even after a timeout.
    # Inspect only the existing request mapping; never retry or change its ID.
    try:
        recovered = _bound_receipt(
            _exchange(transport, "GET", recovery_path, headers), request, target)
        report.update(receipt_recovered=True, receipt_status=recovered.status)
        report["durable_receipt_correlated"] = original is not None and recovered == original
        if original is not None and recovered != original:
            report["issues"].append("durable_receipt_mismatch")
    except Exception:
        report["issues"].append("receipt_recovery_unconfirmed")
    report["passed"] = (report["original_checked_response_verified"]
                        and report["durable_receipt_correlated"] and not report["issues"])
    return report


class _PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # Unknown options can contain a mistakenly supplied credential.
        super().error("Invalid command-line arguments; use --help. Do not pass credentials as arguments.")


def main(argv=None):
    parser = _PrivateArgumentParser(description=__doc__)
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--expected-sha")
    parser.add_argument("--invoke", action="store_true", help="Permit one potentially billable synthetic POST")
    parser.add_argument("--connection-id")
    parser.add_argument("--model-id")
    parser.add_argument("--request-id", help="Save this logical operation ID before opting into invocation")
    parser.add_argument("--max-output-tokens", type=int, default=128)
    args = parser.parse_args(argv)
    result = verify(application_id=args.application_id, api_key=os.getenv("GUARDOATH_API_KEY", ""),
        expected_sha=args.expected_sha, invoke=args.invoke, connection_id=args.connection_id,
        model_id=args.model_id, request_id=args.request_id, max_output_tokens=args.max_output_tokens)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
