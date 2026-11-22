"""One guarded model transaction, using the existing tenant compiler and Engine.

Durable admission is the execution authority. A duplicate returns only metadata;
no process cache, retry loop or provider idempotency assumption can authorize a
second dispatch. Provider worker slots outlive a timed-out HTTP waiter.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from threading import BoundedSemaphore, Lock
from time import monotonic
from uuid import uuid4

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Stage
from guardoath.tenant_record import TenantRecord, TENANT_POLICY_VERSION, usage_month
from guardoath.tenant_store import UsageLimitReached
from .adapters import ApprovedModelCall, ProviderResult
from .contracts import (
    CheckEvidence, CheckerEvidence, ConnectionCatalog, ConnectionInfo, InvocationReceipt,
    InvocationRequest, InvocationResponse, MAX_OUTPUT_BYTES, ReportedUsage,
    TargetSnapshot, TERMINAL_STATUSES,
)
from .store import DispatchQuota, InvocationConflict, InvocationQuotaExceeded, InvocationStoreUnavailable


CATALOG_VERSION = "guarded-models-1"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _changed(receipt, **fields):
    return InvocationReceipt.model_validate({**receipt.model_dump(), "updated_at": _now(), **fields})


def _evidence(result):
    def identifier(value):
        return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value) else "unidentified"
    findings = tuple(dict.fromkeys(identifier(f.rule_id) for c in result.checker_results for f in c.findings))
    return CheckEvidence(
        check_id=result.check_id, decision=result.effective_action,
        evaluation_status=result.evaluation_status, simulated=result.simulated,
        checker_ids=tuple(dict.fromkeys(identifier(c.checker_id) for c in result.checker_results))[:64],
        finding_ids=findings[:128], findings_truncated=len(findings) > 128,
        checker_results=tuple(CheckerEvidence(checker_id=identifier(c.checker_id),
            checker_version=identifier(c.checker_version), status=c.status) for c in result.checker_results[:64]),
    )


def _approved(result, original):
    if result.evaluation_status != "complete" or result.simulated:
        return None, "incomplete"
    if result.effective_action == Action.BLOCK:
        return None, "blocked"
    if result.effective_action == Action.REDACT:
        if result.redaction.status != "ready" or result.redaction.content is None:
            return None, "incomplete"
        return result.redaction.content, None
    return original, None


@dataclass(frozen=True)
class ConnectionBinding:
    application_id: str
    info: ConnectionInfo
    adapter: object

    def __post_init__(self):
        if (not isinstance(self.application_id, str) or not 1 <= len(self.application_id) <= 100
                or not isinstance(self.info, ConnectionInfo)
                or not callable(getattr(self.adapter, "invoke", None))):
            raise ValueError("Invalid trusted connection binding")
        if len({model.id for model in self.info.models}) != len(self.info.models):
            raise ValueError("Approved model aliases must be distinct")
        # Adapters are trusted injected boundaries, but a reusable adapter may
        # never be accidentally rebound to a different tenant or credential version.
        for name, expected in (("tenant_id", self.application_id), ("connection_id", self.info.id),
                               ("connection_version", self.info.version)):
            if getattr(self.adapter, name, None) != expected:
                raise ValueError("Provider adapter binding mismatch")


class _WorkerPermit:
    def __init__(self, owner):
        self.owner, self.submitted, self.closed = owner, False, False

    async def invoke(self, adapter, call, timeout):
        if self.submitted or self.closed:
            raise RuntimeError("Provider worker permit already consumed")
        self.submitted = True
        try:
            deadline = monotonic() + timeout
            def dispatch():
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return ProviderResult(outcome="rejected", reason_code="configuration_unavailable")
                return adapter.invoke(replace(call, timeout_seconds=min(call.timeout_seconds, remaining)))
            future = self.owner._pool.submit(dispatch)
        except BaseException:
            self.owner._slots.release()
            self.closed = True
            raise
        # Cancellation of an asyncio waiter cannot free a still-running sync
        # call. No retry or queued unbounded orphan work is created.
        future.add_done_callback(lambda unused: self.owner._slots.release())
        wrapped = asyncio.wrap_future(future)
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.wait_for(asyncio.shield(wrapped), timeout)

    def close(self):
        if not self.submitted and not self.closed:
            self.closed = True
            self.owner._slots.release()


class BoundedProviderWorkers:
    def __init__(self, max_workers=2):
        if type(max_workers) is not int or not 1 <= max_workers <= 8:
            raise ValueError("Invalid provider worker bound")
        self._slots = BoundedSemaphore(max_workers)
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="guardoath-provider")
        self._closed, self._lock = False, Lock()

    def reserve(self):
        with self._lock:
            if self._closed or not self._slots.acquire(blocking=False):
                return None
            return _WorkerPermit(self)

    def close(self):
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)


class InvocationGateway:
    def __init__(self, *, store, connections, provider_timeout=4.0, request_timeout=10.0, max_workers=2):
        if (type(provider_timeout) not in (int, float) or type(request_timeout) not in (int, float)
                or not math.isfinite(provider_timeout) or not math.isfinite(request_timeout)
                or not 0 < provider_timeout <= request_timeout <= 10):
            raise ValueError("Invalid trusted invocation deadline")
        bindings = tuple(connections)
        keys = {(b.application_id, b.info.id) for b in bindings}
        if len(keys) != len(bindings) or len(bindings) > 200:
            raise ValueError("Invalid trusted connection registry")
        if any(sum(b.application_id == owner for b in bindings) > 20 for owner, _ in keys):
            raise ValueError("Too many tenant connections")
        self.store = store
        self._connections = {(b.application_id, b.info.id): b for b in bindings}
        self.provider_timeout, self.request_timeout = provider_timeout, request_timeout
        self.workers = BoundedProviderWorkers(max_workers)

    def catalog(self, record):
        reason = ("demo_not_enabled" if record.demo else
                  "subscription_required" if record.subscription_status != "active" else
                  "enforcement_required" if record.mode != "enforce" else "ready")
        connections = tuple(b.info for (owner, _), b in self._connections.items() if owner == record.tenant_id)
        if reason == "ready" and not connections:
            reason = "no_connections"
        return ConnectionCatalog(application_id=record.tenant_id, available=reason == "ready",
                                 reason_code=reason, connections=connections if reason == "ready" else ())

    async def receipt(self, application_id, interaction_id):
        return await run_in_threadpool(self.store.get, application_id, interaction_id)

    async def recent(self, application_id, limit):
        return await run_in_threadpool(self.store.recent, application_id, limit)

    async def invoke(self, request: InvocationRequest, record: TenantRecord, *, tenants, budgets):
        # Rate/concurrency admission precedes every ledger write, including a
        # rejected or duplicate logical request. No unbounded storage side path.
        with budgets.check(record.tenant_id):
            return await self._invoke(request, record, tenants=tenants)

    async def _invoke(self, request: InvocationRequest, record: TenantRecord, *, tenants):
        deadline = monotonic() + self.request_timeout
        if request.application_id != record.tenant_id:
            raise HTTPException(403, "Credential does not authorize this application")
        snapshot = TenantRecord.model_validate(record.model_dump())
        catalog = self.catalog(snapshot)
        if not catalog.available:
            status = 402 if catalog.reason_code == "subscription_required" else 403
            raise HTTPException(status, "Model invocation is unavailable for this tenant")
        binding = self._connections.get((snapshot.tenant_id, request.target.connection_id))
        model = next((m for m in binding.info.models if m.id == request.target.model_id), None) if binding else None
        if model is None:
            raise HTTPException(404, "Approved connection or model not found")
        if request.max_output_tokens > model.max_output_tokens:
            raise HTTPException(422, "Output token limit exceeds the approved model limit")
        policy = snapshot.to_policy()
        engine = Engine([policy], tenants.checkers)
        fingerprint = self.store.make_fingerprint(snapshot.tenant_id, json.dumps(
            request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8"))
        initial = InvocationReceipt(
            application_id=snapshot.tenant_id, interaction_id=uuid4().hex, request_id=request.request_id,
            created_at=_now(), updated_at=_now(), status="admitted",
            target=TargetSnapshot(connection_id=binding.info.id, connection_version=binding.info.version,
                                  provider=binding.info.provider, region=binding.info.region,
                                  model_id=model.id, provider_model_id=model.provider_model_id),
            policy_revision=snapshot.revision,
            policy_digest=hashlib.sha256(policy.model_dump_json().encode("utf-8")).hexdigest(),
            catalog_version=CATALOG_VERSION,
        )
        admitted = await run_in_threadpool(self.store.admit, initial, fingerprint)
        if not admitted.created:
            return InvocationResponse(receipt=admitted.receipt, replayed=True)
        receipt, worker = admitted.receipt, None

        async def advance(**changes):
            nonlocal receipt
            receipt = await run_in_threadpool(self.store.advance, receipt, _changed(receipt, **changes))
            return receipt

        async def current_authority():
            current = await run_in_threadpool(tenants.store.get, snapshot.tenant_id)
            return (current is not None and current.tenant_id == snapshot.tenant_id and
                    current.status == "active" and not current.demo and
                    current.subscription_status == "active" and current.mode == "enforce" and
                    current.revision == snapshot.revision)

        async def check(text, stage):
            # Keep the existing monthly validation counter authoritative. A
            # completed check is counted; an exception is refunded as before.
            # Output capacity can be exhausted while the provider runs: in that
            # case the answer is withheld, never released without a check.
            month = usage_month()
            await run_in_threadpool(tenants.store.count_check, snapshot.tenant_id, month)
            try:
                return await engine.check(CheckRequest(application_id=snapshot.tenant_id,
                    policy_version=TENANT_POLICY_VERSION, content=text), stage)
            except BaseException:
                try:
                    await run_in_threadpool(tenants.store.release_check, snapshot.tenant_id, month)
                except Exception:
                    pass  # Uncertain accounting stays conservative; never fabricate a refund.
                raise

        try:
            async with asyncio.timeout(max(0, deadline - monotonic())):
                if not await current_authority():
                    await advance(status="unavailable", reason_code="tenant_changed")
                    return InvocationResponse(receipt=receipt)
                checked_input = await check(request.message, Stage.INPUT)
                approved_input, refused = _approved(checked_input, request.message)
                await advance(status="input_checked", input_check=_evidence(checked_input))
                if refused:
                    await advance(status="input_" + refused, reason_code="input_" + refused)
                    return InvocationResponse(receipt=receipt)
                if not await current_authority():
                    await advance(status="unavailable", reason_code="tenant_changed")
                    return InvocationResponse(receipt=receipt)
                worker = self.workers.reserve()
                if worker is None:
                    await advance(status="unavailable", reason_code="provider_capacity")
                    return InvocationResponse(receipt=receipt)
                period = usage_month()
                receipt = await run_in_threadpool(self.store.reserve_dispatch, receipt,
                    _changed(receipt, status="dispatch_intent", dispatch_state="intent_recorded",
                             reserved_output_tokens=request.max_output_tokens, quota_period=period),
                    DispatchQuota(binding.info.monthly_call_limit, binding.info.monthly_output_token_limit, period))
                timeout = min(self.provider_timeout, deadline - monotonic())
                if timeout <= 0:
                    raise TimeoutError
                reply = await worker.invoke(binding.adapter, ApprovedModelCall(
                    text=approved_input, model_id=model.provider_model_id,
                    max_output_tokens=request.max_output_tokens, timeout_seconds=timeout), timeout)
                if not isinstance(reply, ProviderResult):
                    raise ValueError("Invalid normalized provider response")
                usage = (ReportedUsage(input_tokens=reply.input_tokens, output_tokens=reply.output_tokens)
                         if reply.input_tokens is not None or reply.output_tokens is not None else None)
                observations = dict(provider_request_id=reply.provider_request_id, provider_response_id=reply.response_id,
                                    observed_model=reply.observed_model, observed_service_tier=reply.observed_service_tier,
                                    usage=usage)
                if reply.outcome == "unknown":
                    await advance(status="outcome_unknown", dispatch_state="outcome_unknown",
                                  reason_code=reply.reason_code, **observations)
                    return InvocationResponse(receipt=receipt)
                if reply.outcome == "rejected" and reply.reason_code in {
                    "request_invalid", "model_not_allowed", "configuration_unavailable"
                }:
                    if any(value is not None for value in observations.values()):
                        raise ValueError("Incoherent local provider refusal")
                    await advance(status="provider_rejected", dispatch_state="not_dispatched", reason_code=reply.reason_code)
                    return InvocationResponse(receipt=receipt)
                await advance(status="provider_result", dispatch_state="response_observed",
                              **observations)
                if reply.outcome != "completed":
                    await advance(status="provider_" + reply.outcome, reason_code=reply.reason_code)
                    return InvocationResponse(receipt=receipt)
                if (not isinstance(reply.text, str) or not reply.text or
                        len(reply.text.encode("utf-8")) > MAX_OUTPUT_BYTES or
                        (reply.observed_model is not None and reply.observed_model != model.provider_model_id) or
                        reply.observed_service_tier not in (None, "default")):
                    await advance(status="provider_withheld", reason_code="provider_contract_mismatch")
                    return InvocationResponse(receipt=receipt)
                if not await current_authority():
                    await advance(status="unavailable", reason_code="tenant_changed")
                    return InvocationResponse(receipt=receipt)
                checked_output = await check(reply.text, Stage.OUTPUT)
                approved_output, refused = _approved(checked_output, reply.text)
                if not await current_authority():
                    await advance(status="output_incomplete", output_check=_evidence(checked_output),
                                  reason_code="tenant_changed")
                    return InvocationResponse(receipt=receipt)
                if approved_output is not None and len(approved_output.encode("utf-8")) > MAX_OUTPUT_BYTES:
                    approved_output, refused = None, "incomplete"
                await advance(status=("output_" + refused) if refused else "completed",
                              output_check=_evidence(checked_output),
                              reason_code=("output_" + refused) if refused else None)
                # The terminal receipt must be durable before any answer
                # leaves the process. It means prepared for HTTP release,
                # not proof that a remote client received the bytes.
                return InvocationResponse(receipt=receipt, response=approved_output)
        except InvocationQuotaExceeded:
            await advance(status="quota_exceeded", reason_code="provider_quota_exceeded")
            return InvocationResponse(receipt=receipt)
        except UsageLimitReached:
            await advance(status=("output_incomplete" if receipt.dispatch_state == "response_observed" else "quota_exceeded"),
                          reason_code="validation_quota_exceeded")
            return InvocationResponse(receipt=receipt)
        except (InvocationStoreUnavailable, InvocationConflict):
            # An ambiguous write cannot mint dispatch permission or authorize
            # release. The caller keeps its request ID and reads the receipt.
            raise
        except BaseException as error:
            if receipt.status not in TERMINAL_STATUSES:
                after_dispatch = receipt.dispatch_state != "not_dispatched"
                observed = receipt.dispatch_state == "response_observed"
                try:
                    await advance(status="output_incomplete" if observed else
                                  "outcome_unknown" if after_dispatch else "unavailable",
                                  dispatch_state="response_observed" if observed else
                                  "outcome_unknown" if after_dispatch else "not_dispatched",
                                  reason_code="deadline_exceeded" if isinstance(error, TimeoutError) else "execution_interrupted")
                except Exception:
                    raise InvocationStoreUnavailable("Invocation receipt could not be confirmed") from None
            if isinstance(error, asyncio.CancelledError):
                raise
            if isinstance(error, HTTPException) and receipt.dispatch_state == "not_dispatched":
                raise error
            return InvocationResponse(receipt=receipt)
        finally:
            if worker is not None:
                worker.close()
