import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.ingress import JsonIngressLimits
from guardoath.json_data import MAX_JSON_DEPTH, parse_json
from guardoath.models import Action, CheckRequest, FieldComparison, Policy, Rule, Stage


def evaluate(policy, content, stage=Stage.INPUT):
    engine = Engine([policy], default_checkers())
    request = CheckRequest(application_id="demo", policy_version=policy.version,
                           content=content, content_format="json")
    return asyncio.run(engine.check(request, stage))


@pytest.mark.parametrize("fmt,bad,good", [
    ("date", "not-a-date", "2026-10-27"),
    ("date", "2026-02-30", "2026-02-28"),
    ("date-time", "2026-10-27", "2026-10-27T12:30:00Z"),
    ("time", "25:90:00Z", "12:30:00Z"),
    ("email", "a@@example.com", "jane@example.com"),
    ("ipv4", "999.2.3.4", "192.0.2.1"),
    ("ipv6", "not-ipv6", "2001:db8::1"),
    ("uuid", "not-a-uuid", "8a7d5f0a-7818-4ed3-96ba-627bfac5d964"),
])
def test_formats_are_enforced(fmt, bad, good):
    policy = Policy(application_id="demo", version="v1", rules=(Rule(checker_id="schema"),),
                    input_schema={"type": "string", "format": fmt})
    assert evaluate(policy, json.dumps(bad)).checker_results[0].status == "flagged"
    assert evaluate(policy, json.dumps(good)).checker_results[0].status == "passed"


def test_unknown_schema_format_cannot_silently_pass():
    policy = Policy(application_id="demo", version="v1", rules=(), input_schema={"format": "custom-unknown"})
    with pytest.raises(ValueError, match="Unsupported schema format"):
        Engine([policy], default_checkers())


def test_format_word_in_constant_is_not_a_schema_keyword():
    policy = Policy(application_id="demo", version="v1", rules=(),
                    input_schema={"const": {"format": "my business format"}})
    Engine([policy], default_checkers())


def test_business_rules_and_asymmetric_actions():
    policy = load_policies("policies/business.json")[0]
    valid = {"start_date": "2026-10-01", "end_date": "2026-10-03", "purchase_amount": 100, "refund_amount": 25}
    assert evaluate(policy, json.dumps(valid)).recommended_action == "allow"
    bad = {**valid, "end_date": "2026-09-30", "refund_amount": 101}
    for stage, expected in [(Stage.INPUT, "flag"), (Stage.OUTPUT, "block")]:
        result = evaluate(policy, json.dumps(bad), stage)
        diagnostic = [(c.checker_id, c.status, c.error_code, c.latency_ms)
                      for c in result.checker_results]
        assert result.evaluation_status == "complete", diagnostic
        assert [(c.checker_id, c.status) for c in result.checker_results] == [
            ("schema", "passed"), ("cross_field", "flagged")], diagnostic
        assert result.recommended_action == expected, diagnostic
        assert {f.rule_id for f in result.findings} == {"business.date_order", "business.refund_limit"}


def test_bundled_schema_comparison_pair_preserves_both_stages_without_extra_tasks(monkeypatch):
    """Exact bundled CPU-only pair retains all verdicts and individual timed executes."""
    policy = load_policies("policies/business.json")[0]

    def unexpected_gather(*args, **kwargs):
        raise AssertionError("Bundled non-yielding checkers must not be task-scheduled")

    monkeypatch.setattr(asyncio, "gather", unexpected_gather)
    valid = {"start_date": "2026-10-01", "end_date": "2026-10-03",
             "purchase_amount": 100, "refund_amount": 25}
    bad = {**valid, "end_date": "2026-09-30", "refund_amount": 101}
    for stage, action in ((Stage.INPUT, Action.FLAG), (Stage.OUTPUT, Action.BLOCK)):
        clean = evaluate(policy, json.dumps(valid), stage)
        assert clean.evaluation_status == "complete" and clean.recommended_action == Action.ALLOW
        flagged = evaluate(policy, json.dumps(bad), stage)
        assert flagged.evaluation_status == "complete"
        assert flagged.recommended_action == action
        assert [r.checker_id for r in flagged.checker_results] == ["schema", "cross_field"]
        assert [r.status for r in flagged.checker_results] == ["passed", "flagged"]
        assert {f.rule_id for f in flagged.findings} == {
            "business.date_order", "business.refund_limit"}
    # The optimized scheduling path must not turn malformed input into Allow.
    malformed = evaluate(policy, '{"start_date":', Stage.INPUT)
    assert malformed.recommended_action == Action.BLOCK
    assert malformed.evaluation_status == "complete"
    assert "schema.invalid_json" in {f.rule_id for f in malformed.findings}



def test_exact_non_yielding_builtin_checkers_avoid_timers_but_reject_late_results(monkeypatch):
    """No timer scheduling for immutable CPU-only checkers; full deadline remains."""
    from guardoath.checkers import schema_check, cross_field_check

    # A future checker that yields must not silently inherit the CPU-only path.
    for builtin, policy, content in (
        (schema_check, load_policies("policies/business.json")[0],
         '{"start_date":"2026-10-01","end_date":"2026-10-03","purchase_amount":100,"refund_amount":25}'),
        (cross_field_check, load_policies("policies/business.json")[0],
         '{"start_date":"2026-10-01","end_date":"2026-10-03","purchase_amount":100,"refund_amount":25}'),
    ):
        request = CheckRequest(application_id=policy.application_id, policy_version=policy.version,
                               content=content, content_format="json")
        coroutine = builtin(request, policy, Stage.INPUT)
        try:
            coroutine.send(None)
        except StopIteration:
            pass
        else:
            coroutine.close()
            raise AssertionError("Built-in checker became yielding: restore cancellable timeout")

    def unexpected_timeout(*_args, **_kwargs):
        raise AssertionError("No event-loop timer for exact CPU-only checkers")

    monkeypatch.setattr(asyncio, "timeout", unexpected_timeout)
    original = load_policies("policies/business.json")[0]
    payload = {"start_date":"2026-10-01","end_date":"2026-10-03",
               "purchase_amount":100,"refund_amount":25}
    for stage in (Stage.INPUT, Stage.OUTPUT):
        result = evaluate(original, json.dumps(payload), stage)
        assert result.evaluation_status == "complete"
        assert [(r.checker_id, r.status) for r in result.checker_results] == [
            ("schema", "passed"), ("cross_field", "passed")]

    # Fake a monotonic 600-ms jump. Even a non-yielding built-in result is
    # rejected after the original 500-ms deadline, with fail-closed action.
    from guardoath import engine as engine_module
    counter = [0.]
    def advanced_clock():
        counter[0] += 0.6
        return counter[0]
    monkeypatch.setattr(engine_module, "perf_counter", advanced_clock)
    delayed = evaluate(original, json.dumps(payload), Stage.INPUT)
    assert delayed.evaluation_status == "incomplete"
    assert all(r.status == "error" and r.error_code == "checker_timeout"
               for r in delayed.checker_results)
    assert delayed.effective_action == Action.BLOCK

def test_custom_async_schema_still_uses_the_original_concurrent_scheduler(monkeypatch):
    """Only exact bundled checker identities may use the CPU-only fast path."""
    from guardoath.checkers import schema_check

    calls = []
    original_gather = asyncio.gather

    def recording_gather(*tasks, **kwargs):
        calls.append(len(tasks))
        return original_gather(*tasks, **kwargs)

    monkeypatch.setattr(asyncio, "gather", recording_gather)

    async def custom_schema(request, policy, stage):
        await asyncio.sleep(0)
        return await schema_check(request, policy, stage)

    policy = load_policies("policies/business.json")[0]
    checkers = default_checkers()
    checkers["schema"] = custom_schema
    engine = Engine([policy], checkers)
    request = CheckRequest(
        application_id=policy.application_id, policy_version=policy.version,
        content=json.dumps({"start_date": "2026-10-01", "end_date": "2026-09-30",
                            "purchase_amount": 100, "refund_amount": 101}),
        content_format="json",
    )
    response = asyncio.run(engine.check(request, Stage.INPUT))
    assert response.evaluation_status == "complete"
    assert response.recommended_action == Action.FLAG
    assert [c.status for c in response.checker_results] == ["passed", "flagged"]
    assert calls == [2]


@pytest.mark.parametrize("left,right", [(True, 5), ("3", 5), (None, 5), (3, None)])
def test_business_comparisons_reject_invalid_types(left, right):
    policy = Policy(application_id="demo", version="v1", rules=(Rule(checker_id="cross_field"),),
                    comparisons=(FieldComparison(rule_id="limit", left="/left", right="/right", operator="le"),))
    assert evaluate(policy, json.dumps({"left": left, "right": right})).checker_results[0].status == "flagged"


def test_nested_pointer_escaping_and_missing_values():
    policy = Policy(application_id="demo", version="v1", rules=(Rule(checker_id="cross_field"),),
                    comparisons=(FieldComparison(rule_id="limit", left="/a~1b/0", right="/c~0d", operator="le"),))
    assert evaluate(policy, '{"a/b":[2],"c~d":3}').checker_results[0].status == "passed"
    assert evaluate(policy, '{"a/b":[],"c~d":3}').checker_results[0].status == "flagged"


def test_stage_specific_checks_do_not_run_on_other_stage():
    policy = Policy(application_id="demo", version="v1", rules=(Rule(checker_id="schema", stages=(Stage.OUTPUT,)),),
                    output_schema={"type": "integer"})
    result = evaluate(policy, '"wrong"', Stage.INPUT)
    assert result.checker_results == ()
    assert next(g for g in result.groups if g.group_id == "structure").status == "not_applicable"
    assert evaluate(policy, '"wrong"', Stage.OUTPUT).findings


def test_planned_disabled_and_simulated_groups_are_not_passed():
    policy = Policy(application_id="demo", version="v1", rules=(Rule(checker_id="bias"),))
    groups = {g.group_id: g for g in evaluate(policy, '"hello"').groups}
    assert len(groups) == 10
    assert groups["bias"].status == "simulated"
    assert groups["accuracy"].status == "disabled"
    assert groups["scope"].status == "not_implemented"
    assert groups["sensitive_data"].status == "disabled"


def test_missing_required_adapter_is_incomplete_in_group():
    policy = Policy(application_id="demo", version="v1", rules=(Rule(checker_id="pii"),))
    assert evaluate(policy, '"hello"').groups[0].status == "incomplete"


@pytest.mark.parametrize("content", [
    "[" * (MAX_JSON_DEPTH + 1) + "0" + "]" * (MAX_JSON_DEPTH + 1),
    "[" + ",".join("0" for _ in range(1001)) + "]",
    "1" * 129, '"\\ud800"',
])
def test_parser_limits(content):
    with pytest.raises((ValueError, UnicodeError)):
        parse_json(content)


def test_brackets_inside_escaped_strings_do_not_count_as_nesting():
    value = '["' * 100
    assert parse_json(json.dumps(value)) == value
    assert parse_json("[" * MAX_JSON_DEPTH + "0" + "]" * MAX_JSON_DEPTH)


def test_http_rejects_oversize_before_request_validation():
    client = TestClient(create_app(credentials={"test-key": "demo"}))
    response = client.post("/v1/checks/input", content=b" " * 262_145, headers={"Content-Type": "application/json"})
    assert response.status_code == 413


@pytest.mark.parametrize("body,headers,status", [
    (b'{}', {"Content-Type":"text/plain"}, 415),
    (b'{}', {"Content-Type":"application/json", "Content-Encoding":"gzip"}, 415),
    (b'{}', {"Content-Type":"application/json; charset=utf-16"}, 415),
    (b'{"x":1,"x":2}', {"Content-Type":"application/json"}, 400),
    (b'\xff', {"Content-Type":"application/json"}, 400),
])
def test_invalid_outer_envelopes_are_rejected(body, headers, status):
    client = TestClient(create_app(credentials={"test-key": "demo"}))
    assert client.post("/v1/checks/input", content=body, headers=headers).status_code == status


def test_chunked_limit_is_independent_of_declared_size():
    called = False
    async def app(scope, receive, send):
        nonlocal called
        called = True
    events = iter([{"type":"http.request", "body":b"12345", "more_body":True},
                   {"type":"http.request", "body":b"67890", "more_body":False}])
    async def receive(): return next(events)
    sent = []
    async def send(event): sent.append(event)
    scope = {"type":"http", "method":"POST", "path":"/v1/checks/input",
             "headers":[(b"content-type",b"application/json"),(b"content-length",b"1")]}
    asyncio.run(JsonIngressLimits(app,max_bytes=8)(scope,receive,send))
    assert sent[0]["status"] == 413
    assert not called


def test_slow_body_has_a_deadline():
    async def app(*args): raise AssertionError("Must not parse")
    async def receive(): await asyncio.sleep(1)
    sent = []
    async def send(event): sent.append(event)
    scope = {"type":"http", "method":"POST", "path":"/v1/checks/input",
             "headers":[(b"content-type",b"application/json")]}
    asyncio.run(JsonIngressLimits(app,read_timeout=.001)(scope,receive,send))
    assert sent[0]["status"] == 408


def test_redaction_actions_require_a_transforming_checker():
    policy = Policy(application_id="demo", version="v1", rules=(Rule(checker_id="schema",on_finding=Action.REDACT),))
    with pytest.raises(ValueError, match="only by the pii checker"):
        Engine([policy], default_checkers())


def test_missing_redaction_adapter_withholds_content():
    policy = Policy(application_id="demo", version="v1", mode="enforce", rules=(Rule(checker_id="pii",on_finding=Action.REDACT),))
    result = evaluate(policy, '"private content"')
    assert result.redaction.status == "withheld"
    assert result.redaction.content is None
    assert result.effective_action == "block"
