"""PII redaction assembly equivalence: deterministic and privacy-safe.

The independent oracle reproduces the previous reverse-splicing logic.
The optimized assembler must not change labels, adjacency, positions or
what is passed to the existing second PII and checker revalidation.
"""
import asyncio
import random
import re

import pytest

from guardoath.pii import _assemble_redacted_text


def _previous_reverse_splice(text, spans):
    for start, end, entity in reversed(spans):
        text = text[:start] + f"[REDACTED_{entity}]" + text[end:]
    return text


@pytest.mark.parametrize("text,spans", [
    ("", []),
    ("nothing", []),
    ("secret", [(0, 6, "EMAIL_ADDRESS")]),
    ("abcdef", [(0, 1, "EMAIL_ADDRESS"), (1, 3, "US_SSN")]),
    ("prefix:alpha|beta:suffix", [(7, 12, "EMAIL_ADDRESS"), (13, 17, "US_SSN")]),
    ("abc def ghi", [(0, 3, "PHONE_NUMBER"), (8, 11, "CREDIT_CARD")]),
    ("A" * 2048, [(0, 1, "EMAIL_ADDRESS"), (2047, 2048, "PHONE_NUMBER")]),
])
def test_linear_assembly_matches_previous_reverse_splice(text, spans):
    assert _assemble_redacted_text(text, spans) == _previous_reverse_splice(text, spans)


@pytest.mark.parametrize("count", [1, 2, 25, 250, 1000])
def test_many_disjoint_spans_preserve_exact_private_output_without_quadratic_splicing(count):
    text = "".join(f"private{i:04}|" for i in range(count))
    spans = [(match.start(), match.end(), "EMAIL_ADDRESS" if idx % 2 else "US_SSN")
             for idx, match in enumerate(re.finditer(r"private\d{4}", text))]
    assert len(spans) == count
    output = _assemble_redacted_text(text, spans)
    assert output == _previous_reverse_splice(text, spans)
    assert "private" not in output
    assert output.count("[REDACTED_") == count


def test_many_synthetic_random_nonoverlapping_spans_parity():
    randomizer = random.Random(4102)
    for length in (1, 9, 128, 4096):
        text = "abcdefghijklmnop" * ((length + 15) // 16)
        text = text[:length]
        spans = []
        position = 0
        while position < length:
            start = min(length, position + randomizer.randrange(0, 4))
            if start >= length:
                break
            end = min(length, start + randomizer.randrange(1, 5))
            spans.append((start, end, randomizer.choice(("EMAIL_ADDRESS", "US_SSN", "SENSITIVE"))))
            position = end
        assert _assemble_redacted_text(text, spans) == _previous_reverse_splice(text, spans)



def test_redaction_assembly_checks_deadline_before_all_private_spans_are_processed():
    """Many valid findings cannot extend worker time to assemble an unsafe preview."""
    class CountedSpans(list):
        visited = 0

        def __iter__(self):
            for item in super().__iter__():
                self.visited += 1
                yield item

    text = "x|" * 6000
    spans = CountedSpans((2 * i, 2 * i + 1, "EMAIL_ADDRESS")
                         for i in range(6000))
    assert _assemble_redacted_text("x|x|", [(0, 1, "EMAIL_ADDRESS")]) == (
        "[REDACTED_EMAIL_ADDRESS]|x|"
    )
    calls = 0

    def deadline():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise TimeoutError("synthetic redaction-assembly deadline")

    with pytest.raises(TimeoutError, match="synthetic redaction-assembly deadline"):
        _assemble_redacted_text(text, spans, deadline)
    assert calls == 3
    assert spans.visited == 513  # no partial preview or 6000-span assembly



@pytest.mark.parametrize("stage", ["input", "output"])
def test_existing_engine_rechecks_multiple_real_redactions_with_no_raw_values(stage):
    pytest.importorskip("tldextract")
    pytest.importorskip("phonenumbers")
    from guardoath.checkers import default_checkers
    from guardoath.engine import Engine
    from guardoath.models import Action, CheckRequest, Policy, Rule, Stage

    selected_stage = Stage.INPUT if stage == "input" else Stage.OUTPUT
    policy = Policy(application_id="demo", version="v1", mode="enforce",
                    timeout_ms=500, rules=(Rule(checker_id="pii", on_finding=Action.REDACT),))
    original = "Email jane@example.com and SSN 219-09-9999."
    request = CheckRequest(application_id="demo", policy_version="v1", content=original)
    result = asyncio.run(Engine([policy], default_checkers(enable_pii=True)).check(request, selected_stage))
    assert result.evaluation_status == "complete"
    assert {finding.rule_id for finding in result.findings} >= {"pii.email_address", "pii.us_ssn"}
    assert result.redaction.status == "ready"
    assert result.effective_action == "redact"
    assert result.redaction.content == "Email [REDACTED_EMAIL_ADDRESS] and SSN [REDACTED_US_SSN]."
    assert all(item.status == "passed" for item in result.redaction.validation_results)
    assert "jane@example.com" not in result.model_dump_json()
    assert "219-09-9999" not in result.model_dump_json()
