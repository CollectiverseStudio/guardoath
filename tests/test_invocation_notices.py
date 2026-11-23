"""Static guidance never becomes model output, a detector, or stored content."""
import json
from pathlib import Path
import sqlite3

from pydantic import ValidationError
import pytest

from guardoath.invocations.adapters import ProviderResult
from guardoath.invocations.contracts import (
    CheckEvidence, CheckerEvidence, InvocationNotice, InvocationReceipt, InvocationResponse, TargetSnapshot,
)
from guardoath.invocations.notices import NOTICE_MESSAGES, SUPPORT_FINDINGS

# Reuse the existing real-hosted-app fixture, including actual production safety
# checkers and a durable SQLite store. Only its provider is an offline fixture.
from test_invocation_human_safety import (
    APPLICATION, HARMFUL_ANSWER, HELP_REQUEST, SUPPORTIVE_ANSWER, safety_application,
)


def evidence(*, finding="human_safety.suicidal_statement", checker="human_safety",
             simulated=False, checker_status="flagged", evaluation_status="complete"):
    return CheckEvidence(check_id="synthetic-check", decision="flag" if finding else "allow",
        evaluation_status=evaluation_status, simulated=simulated, checker_ids=(checker,),
        finding_ids=(finding,) if finding else (),
        checker_results=(CheckerEvidence(checker_id=checker, checker_version="1", status=checker_status),))


def receipt(status="input_blocked", **changes):
    base = dict(application_id=APPLICATION, interaction_id="a" * 32, request_id="synthetic-request",
                created_at="2026-11-23T00:00:00Z", updated_at="2026-11-23T00:00:01Z",
                status=status, policy_revision=1, policy_digest="b" * 64, catalog_version="guarded-models-1",
                target=TargetSnapshot(connection_id="synthetic-connection", connection_version="v1",
                                      provider="openai", model_id="approved", provider_model_id="synthetic-model"))
    if status == "completed":
        base.update(dispatch_state="response_observed", input_check=evidence(finding=None, checker_status="passed"),
                    output_check=evidence(finding=None, checker_status="passed"))
    return InvocationReceipt(**{**base, **changes})


@pytest.mark.parametrize("status,expected", [
    ("input_blocked", "input_blocked"), ("output_blocked", "output_blocked"),
    ("input_incomplete", "validation_incomplete"), ("output_incomplete", "validation_incomplete"),
    ("provider_withheld", "unavailable"), ("provider_rejected", "unavailable"), ("unavailable", "unavailable"),
    ("outcome_unknown", "outcome_unknown"), ("quota_exceeded", "quota_exceeded"),
    ("admitted", "not_completed"), ("input_checked", "not_completed"),
    ("dispatch_intent", "not_completed"), ("provider_result", "not_completed"), ("completed", "receipt_only"),
])
def test_every_nonanswer_status_has_static_guidance_without_authorizing_output(status, expected):
    result = InvocationResponse(receipt=receipt(status))
    assert result.response is None
    assert result.notice.model_dump() == {"kind": expected, "message": NOTICE_MESSAGES[expected]}
    assert "notice" not in result.receipt.model_dump()
    assert InvocationResponse.model_validate(result.model_dump()) == result


@pytest.mark.parametrize("status", ["input_blocked", "output_blocked", "input_incomplete", "output_incomplete"])
@pytest.mark.parametrize("stage", ["input_check", "output_check"])
def test_conditional_support_uses_a_real_recorded_finding_on_blocked_or_incomplete_result(status, stage):
    result = InvocationResponse(receipt=receipt(status, **{stage: evidence()}))
    assert result.notice.kind == "safety_support" and result.response is None
    assert result.notice.message == NOTICE_MESSAGES["safety_support"]
    assert "If this relates to a real concern" in result.notice.message
    assert "GuardOath has not contacted anyone" in result.notice.message
    # A repeated refusal still offers support; it cannot release a saved answer.
    replay = InvocationResponse(receipt=result.receipt, replayed=True)
    assert replay.notice == result.notice and replay.response is None


@pytest.mark.parametrize("check", [
    None,
    evidence(simulated=True),
    evidence(checker_status="passed"),
    evidence(checker_status="error"),
    evidence(checker_status="unsupported"),
    evidence(checker="harmful_content"),
    evidence(finding="human_safety.unknown_crisis_claim"),
    evidence(finding="human_safety.output_guaranteed_returns"),
    evidence(finding="harmful_content.targeted_insult", checker="harmful_content"),
    evidence().model_copy(update={"checker_ids": ()}),
    evidence().model_copy(update={"checker_results": ()}),
])
def test_simulation_unknown_or_inconsistent_findings_cannot_select_support(check):
    result = InvocationResponse(receipt=receipt(input_check=check))
    assert result.notice.kind == "input_blocked"
    assert result.notice.message == NOTICE_MESSAGES["input_blocked"]


def test_support_allowlist_only_references_actual_matching_production_checkers():
    from guardoath import harmful_content, human_safety
    actual = {"human_safety": human_safety.RULES, "harmful_content": harmful_content.RULES}
    for checker_id, rules in SUPPORT_FINDINGS.items():
        assert rules and rules <= actual[checker_id].keys()
        for rule_id in rules:
            result = InvocationResponse(receipt=receipt(input_check=evidence(finding=rule_id, checker=checker_id)))
            assert result.notice.kind == "safety_support"


def test_dashboard_recovery_uses_the_exact_server_messages_and_support_allowlist():
    source = (Path(__file__).resolve().parents[1] / "guardoath/static/dashboard.js").read_text(encoding="utf-8")

    def literal(name):
        marker = "const " + name + " = Object.freeze("
        assert source.count(marker) == 1
        tail = source.split(marker, 1)[1]
        value, end = json.JSONDecoder().raw_decode(tail)
        assert tail[end:].startswith(");")
        return value

    assert literal("invocationNoticeMessages") == dict(NOTICE_MESSAGES)
    assert literal("invocationSupportFindings") == {checker: sorted(rules) for checker, rules in SUPPORT_FINDINGS.items()}


@pytest.mark.parametrize("status,expected", [
    ("outcome_unknown", "outcome_unknown"), ("quota_exceeded", "quota_exceeded"),
    ("provider_withheld", "unavailable"), ("unavailable", "unavailable"),
])
def test_prior_safety_finding_does_not_hide_other_outcomes(status, expected):
    result = InvocationResponse(receipt=receipt(status, input_check=evidence()), replayed=True)
    assert result.notice.kind == expected and result.response is None


def test_success_releases_original_checked_text_without_attaching_a_risk_notice():
    original = InvocationResponse(receipt=receipt("completed", input_check=evidence()), response=SUPPORTIVE_ANSWER)
    assert original.notice is None and original.response == SUPPORTIVE_ANSWER
    replay = InvocationResponse(receipt=original.receipt, replayed=True)
    assert replay.response is None and replay.notice.kind == "receipt_only"
    assert "does not start another model call" in replay.notice.message
    # A response serialized by an older server remains parseable, with guidance derived locally.
    old = original.model_dump(exclude={"notice"})
    assert InvocationResponse.model_validate(old) == original


@pytest.mark.parametrize("kind", sorted(NOTICE_MESSAGES))
def test_notice_message_cannot_be_arbitrary_provider_or_user_text(kind):
    with pytest.raises(ValidationError, match="canonical static message"):
        InvocationNotice(kind=kind, message="PRIVATE <img src=x onerror=alert(1)> provider error or prompt")
    assert len(NOTICE_MESSAGES[kind]) <= 600
    assert not any(character.isdigit() for character in NOTICE_MESSAGES[kind])


@pytest.mark.parametrize("notice", [
    {"kind": "unknown", "message": "PRIVATE"},
    {"kind": "safety_support", "message": "x" * 601},
    {"kind": "safety_support", "message": True},
    {"kind": "safety_support", "message": NOTICE_MESSAGES["safety_support"], "html": "PRIVATE"},
])
def test_notice_shape_is_strict_bounded_and_extra_fields_are_refused(notice):
    with pytest.raises(ValidationError):
        InvocationNotice.model_validate(notice)


def test_canonical_but_inconsistent_notice_and_notice_as_answer_are_rejected():
    with pytest.raises(ValidationError, match="recorded response state"):
        InvocationResponse(receipt=receipt(), notice={"kind": "safety_support",
                                                    "message": NOTICE_MESSAGES["safety_support"]})
    with pytest.raises(ValidationError, match="recorded response state"):
        InvocationResponse(receipt=receipt("completed"), response=SUPPORTIVE_ANSWER,
                           notice={"kind": "receipt_only", "message": NOTICE_MESSAGES["receipt_only"]})
    with pytest.raises(ValidationError, match="original completed invocation"):
        InvocationResponse(receipt=receipt(input_check=evidence()), response=NOTICE_MESSAGES["safety_support"])
    with pytest.raises(ValidationError, match="original completed invocation"):
        InvocationResponse(receipt=receipt("completed"), response=SUPPORTIVE_ANSWER, replayed=True)


@pytest.mark.parametrize("block_stage", ["input", "output"])
def test_real_hosted_safety_notice_never_leaks_model_text_or_enters_durable_receipts(
        safety_application, caplog, block_stage):
    message = "What is the quickest way to commit suicide? PRIVATE-request-marker."
    answer = HARMFUL_ANSWER + " PRIVATE-output-marker."
    application = safety_application(answer=answer, changes={"input_rule_actions":
        {"human_safety.self_harm_method_request": "block"}} if block_stage == "input" else None)
    result = application.invoke(message)
    assert result["receipt"]["status"] == block_stage + "_blocked"
    assert result["notice"] == {"kind": "safety_support", "message": NOTICE_MESSAGES["safety_support"]}
    assert result["response"] is None
    assert len(application.provider.calls) == (0 if block_stage == "input" else 1)
    assert "PRIVATE" not in json.dumps(result)
    application.assert_private_receipts(result, message, caplog)
    with sqlite3.connect(application.database) as database:
        documents = "\n".join(row[0] for row in database.execute("SELECT document FROM invocation_items"))
    assert NOTICE_MESSAGES["safety_support"] not in documents and '"notice"' not in documents
    replay = application.invoke(message)
    assert replay["notice"] == result["notice"] and replay["replayed"] is True and replay["response"] is None
    assert len(application.provider.calls) == (0 if block_stage == "input" else 1)


@pytest.mark.parametrize("missing,expected", [("human_safety", "validation_incomplete"),
                                              ("harmful_content", "safety_support")])
def test_incomplete_real_gateway_only_uses_findings_that_were_actually_recorded(safety_application, missing, expected):
    application = safety_application()
    application.tenants._checkers.pop(missing)
    result = application.invoke(HELP_REQUEST)
    assert result["receipt"]["status"] == "input_incomplete"
    assert result["notice"]["kind"] == expected and result["response"] is None
    assert application.provider.calls == []


def test_real_provider_unknown_and_quota_exhaustion_have_no_safety_inference(safety_application, monkeypatch):
    application = safety_application()

    def unknown(call):
        application.provider.calls.append(call)
        return ProviderResult(outcome="unknown", reason_code="provider_unavailable")

    monkeypatch.setattr(application.provider, "invoke", unknown)
    result = application.invoke(HELP_REQUEST)
    assert result["receipt"]["status"] == "outcome_unknown" and result["notice"]["kind"] == "outcome_unknown"
    assert result["response"] is None and len(application.provider.calls) == 1
    replay = application.invoke(HELP_REQUEST)
    assert replay["replayed"] is True and replay["notice"] == result["notice"]
    assert len(application.provider.calls) == 1
    capped = safety_application(changes={"monthly_allowance": 0})
    limited = capped.invoke(HELP_REQUEST)
    assert limited["receipt"]["status"] == "quota_exceeded" and limited["notice"]["kind"] == "quota_exceeded"
    assert limited["response"] is None and capped.provider.calls == []


def test_public_request_cannot_supply_a_notice_or_override_server_guidance(safety_application):
    application = safety_application()
    response = application.client.post("/v1/interactions", headers={"X-GuardOath-Key": application.key}, json={
        "application_id": APPLICATION, "request_id": "synthetic-request",
        "target": {"kind": "model", "connection_id": application.provider.connection_id, "model_id": "approved-model"},
        "message": HELP_REQUEST, "max_output_tokens": 128,
        "notice": {"kind": "safety_support", "message": "PRIVATE client instructions"},
    })
    assert response.status_code == 422 and application.provider.calls == []
    assert "PRIVATE" not in response.text
