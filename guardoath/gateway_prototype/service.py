"""Mock-provider vertical slice using the real tenant compiler and policy engine.

Not registered in the hosted application. No example profiles, policy overrides,
provider URLs/credentials or tool authorization are accepted from customer input.
"""
import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import hmac
import json
import inspect
import math
import re
import secrets

from pydantic import Field
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Contract, Stage
from guardoath.tenant_record import TenantRecord
from .catalog import CATALOG_VERSION


class InteractionRequest(Contract):
    message: str = Field(min_length=1, max_length=32_000, strict=True)
    model: str = Field(default='tenant-default', pattern=r'^[A-Za-z0-9_.-]{1,80}$', strict=True)
    request_id: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,80}$', strict=True)


class ProviderReply(Contract):
    text: str = Field(min_length=1, max_length=32_000, strict=True)
    input_tokens: int = Field(ge=0, le=1_000_000, strict=True)
    output_tokens: int = Field(ge=0, le=1_000_000, strict=True)
    tool_calls: tuple[str, ...] = Field(default=(), max_length=20)


@dataclass(frozen=True)
class ProviderBinding:
    tenant_id: str
    provider: object  # Trusted adapter with async generate(message=..., model=...).
    allowed_models: tuple[str, ...] = ('tenant-default',)
    entitled: bool = True
    remaining_calls: int = 100

    def __post_init__(self):
        if type(self.entitled) is not bool or type(self.remaining_calls) is not int or self.remaining_calls < 0:
            raise ValueError('invalid trusted provider entitlement/budget')
        if (not isinstance(self.allowed_models, tuple) or not 1 <= len(self.allowed_models) <= 20
                or any(not isinstance(m, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', m) for m in self.allowed_models)
                or not inspect.iscoroutinefunction(getattr(self.provider, 'generate', None))):
            raise ValueError('bounded model allowlist and async provider adapter required')


class InteractionResponse(Contract):
    request_id: str
    status: str
    response: str | None = None
    tenant_policy_revision: int
    policy_digest: str
    catalog_version: str = CATALOG_VERSION
    model: str
    input_decision: str = 'not_run'
    output_decision: str = 'not_run'
    provider_called: bool = False
    replayed: bool = False
    usage: dict[str, int] | None = None
    checks: tuple[dict, ...] = ()


def _identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', value) else 'unidentified'


def _summary(result, policy):
    actions = {r.checker_id: ((r.on_input_finding if result.stage == Stage.INPUT else r.on_output_finding) or r.on_finding).value for r in policy.rules}
    return tuple({'stage': result.stage.value, 'checker_id': _identifier(c.checker_id),
        'checker_version': _identifier(c.checker_version), 'status': c.status,
        'action': actions[c.checker_id], 'rule_ids': tuple(_identifier(f.rule_id) for f in c.findings)}
        for c in result.checker_results)


def _approved(result, original):
    if result.evaluation_status != 'complete' or result.simulated:
        return None, 'validation_incomplete'
    if result.effective_action == Action.BLOCK:
        return None, 'blocked'
    if result.effective_action == Action.REDACT:
        if result.redaction.status != 'ready' or result.redaction.content is None:
            return None, 'validation_incomplete'
        return result.redaction.content, None
    return original, None


class PrototypeGateway:
    def __init__(self, checkers, bindings, *, provider_timeout=0.5, request_timeout=2.0):
        if (type(provider_timeout) not in (int, float) or type(request_timeout) not in (int, float)
                or not math.isfinite(provider_timeout) or not math.isfinite(request_timeout)
                or not 0 < provider_timeout <= request_timeout <= 5):
            raise ValueError('invalid trusted gateway deadlines')
        self.checkers = dict(checkers)
        self.bindings = dict(bindings)
        if any(owner != binding.tenant_id for owner, binding in self.bindings.items()):
            raise ValueError('provider binding owner mismatch')
        self._remaining_calls = {owner: binding.remaining_calls for owner, binding in self.bindings.items()}
        self.provider_timeout = provider_timeout
        self.request_timeout = request_timeout
        self._lock = asyncio.Lock()  # Prototype serialization, not a production concurrency service.
        self._cache = OrderedDict()
        self._digest_key = secrets.token_bytes(32)

    async def interact(self, trusted_record, request):
        deadline = asyncio.get_running_loop().time() + self.request_timeout
        def remaining():
            return max(0, deadline - asyncio.get_running_loop().time())
        snapshot = TenantRecord.model_validate(trusted_record.model_dump())
        policy = snapshot.to_policy()  # Sole executable selection/action authority.
        digest = hashlib.sha256(policy.model_dump_json().encode()).hexdigest()
        state = {'request_id': request.request_id, 'tenant_policy_revision': snapshot.revision,
                 'policy_digest': digest, 'model': request.model, 'usage': {'input_tokens': 0, 'output_tokens': 0}}
        def response(status, **fields):
            return InteractionResponse(status=status, **{**state, **fields})
        if snapshot.status != 'active':
            return response('unauthorized')
        # Observe tenants need an explicit Director migration. (Records naming a retired example profile
        # are already refused when read: TenantRecord.retired_profile_field.)
        if snapshot.mode != 'enforce':
            return response('configuration_error')
        binding = self.bindings.get(snapshot.tenant_id)
        if binding is None or binding.entitled is not True or request.model not in binding.allowed_models:
            return response('provider_unavailable')
        try:
            engine = Engine([policy], self.checkers)
        except Exception:
            return response('configuration_error')
        # 2026-11-05 (Tests #766, Windows 3.12): when setup above used up the request's deadline, waiting for a free lock
        # with timeout 0 raised TimeoutError and the request was reported 'gateway_busy' (HTTP 429, "retry later") though
        # nothing held the gateway. A deadline that passed before any wait is a timeout; 'gateway_busy' is reported only
        # when another request holds the gateway for the rest of this request's deadline.
        if remaining() <= 0:
            return response('gateway_timeout')
        if not self._lock.locked():
            await self._lock.acquire()  # Free: acquired at once, without waiting.
        else:
            try:
                await asyncio.wait_for(self._lock.acquire(), timeout=remaining())
            except TimeoutError:
                return response('gateway_busy')
        try:
            key = (snapshot.tenant_id, request.request_id)
            fingerprint = hmac.new(self._digest_key,
                json.dumps([request.model, request.message, snapshot.revision, digest, CATALOG_VERSION],
                           ensure_ascii=True).encode(), hashlib.sha256).digest()
            previous = self._cache.get(key)
            if previous is not None:
                if not hmac.compare_digest(previous[0], fingerprint):
                    return response('idempotency_conflict')
                return previous[1].model_copy(deep=True, update={'replayed': True})
            async def execute():
                incoming = await engine.check(CheckRequest(application_id=snapshot.tenant_id,
                    policy_version=policy.version, content=request.message), Stage.INPUT)
                state['input_decision'] = incoming.effective_action.value
                state['checks'] = _summary(incoming, policy)
                approved, failure = _approved(incoming, request.message)
                if failure:
                    return response('input_blocked' if failure == 'blocked' else failure)
                if self._remaining_calls[snapshot.tenant_id] <= 0:
                    return response('quota_exceeded')
                self._remaining_calls[snapshot.tenant_id] -= 1
                state['provider_called'] = True
                state['usage'] = None  # Unknown if provider fails after starting; never invent usage.
                try:
                    raw = await asyncio.wait_for(binding.provider.generate(message=approved, model=request.model), min(self.provider_timeout, remaining()))
                    reply = ProviderReply.model_validate(raw.model_dump() if isinstance(raw, ProviderReply) else raw)
                except TimeoutError:
                    return response('provider_timeout')
                except Exception:
                    return response('provider_error')
                state['usage'] = {'input_tokens': reply.input_tokens, 'output_tokens': reply.output_tokens}
                # No tool executor exists in this prototype. Content 'allow' never authorizes one.
                if reply.tool_calls:
                    return response('tool_withheld')
                outgoing = await engine.check(CheckRequest(application_id=snapshot.tenant_id,
                    policy_version=policy.version, content=reply.text), Stage.OUTPUT)
                state['output_decision'] = outgoing.effective_action.value
                state['checks'] += _summary(outgoing, policy)
                approved_output, failure = _approved(outgoing, reply.text)
                if failure:
                    return response('output_withheld' if failure == 'blocked' else failure)
                return response('completed', response=approved_output)
            cancelled = False
            try:
                result = await asyncio.wait_for(execute(), remaining())
            except asyncio.CancelledError:
                # Preserve cancellation while retaining idempotency after a
                # provider attempt whose external completion may be unknown.
                result = response('operation_cancelled')
                cancelled = True
            except TimeoutError:
                result = response('gateway_timeout')
            except Exception:
                result = response('validation_incomplete')
            # Only safe final envelopes; no raw prompts, rejected output or provider errors cached.
            self._cache[key] = (fingerprint, result.model_copy(deep=True))
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
            if cancelled:
                raise asyncio.CancelledError
            return result
        finally:
            self._lock.release()
