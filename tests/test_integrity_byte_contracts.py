"""Independent content-integrity regressions for exact bytes and failure decisions.

An integrity digest is a configured reference, not proof of trusted origin,
authorization, harmlessness, or lack of data poisoning.
"""
import asyncio
import base64
import hashlib

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import CheckRequest, IntegrityPolicy, Policy, Rule, Stage


def assess(content, stage, approved, *, encoding="utf8", max_bytes=16384, content_format="text"):
    policy = Policy(
        application_id="demo",
        version="integrity-exact-v1",
        mode="enforce",
        rules=(Rule(checker_id="integrity", on_finding="flag", on_output_finding="block"),),
        integrity=IntegrityPolicy(sha256=hashlib.sha256(approved).hexdigest(),
                                  encoding=encoding, max_bytes=max_bytes),
    )
    result = asyncio.run(Engine([policy], default_checkers()).check(
        CheckRequest(application_id="demo", policy_version=policy.version,
                     content=content, content_format=content_format),
        stage,
    ))
    return result, policy.integrity.sha256


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("content,approved", [
    ("demo dataset\n", b"demo dataset\n"),
    ("abc", b"abc"),
    ("caf\u00e9", "caf\u00e9".encode("utf-8")),
])
def test_exact_utf8_bytes_pass_both_stages(stage, content, approved):
    response, digest = assess(content, stage, approved)
    assert response.mode == "enforce"
    assert response.evaluation_status == "complete"
    assert not response.findings
    assert response.recommended_action == "allow"
    assert response.effective_action == "allow"
    assert any(c.checker_id == "integrity" and c.status == "passed"
               for c in response.checker_results)
    assert digest not in response.model_dump_json()


@pytest.mark.parametrize("stage,action", [
    (Stage.INPUT, "flag"), (Stage.OUTPUT, "block"),
])
@pytest.mark.parametrize("mutated", [
    "demo dataset",
    "demo dataset\r\n",
    "\ufeffdemo dataset\n",
    "Demo dataset\n",
    "demo dataset \n",
])
def test_byte_mutations_are_detected_without_normalization(stage, action, mutated):
    response, digest = assess(mutated, stage, b"demo dataset\n")
    assert response.evaluation_status == "complete"
    assert {f.rule_id for f in response.findings} == {"structure.integrity_digest_mismatch"}
    assert response.recommended_action == action
    assert response.effective_action == action
    assert digest not in response.model_dump_json()
    assert mutated not in [f.explanation for f in response.findings]


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("approved", [b"\x00\xff\x10", b"demo dataset\n"])
def test_strict_base64_is_hashed_after_single_decode(stage, approved):
    content = base64.b64encode(approved).decode("ascii")
    response, digest = assess(content, stage, approved, encoding="base64")
    assert response.evaluation_status == "complete"
    assert not response.findings
    assert response.recommended_action == "allow"
    assert response.effective_action == "allow"
    assert digest not in response.model_dump_json()


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("content", [
    "YWJj$", "YW Jj", "YWJj\n", "base64:YWJj", "Y", "YWJj====", "YR==",
])
def test_invalid_base64_is_incomplete_and_blocks(stage, content):
    response, digest = assess(content, stage, b"abc", encoding="base64")
    assert response.evaluation_status == "incomplete"
    assert not response.findings
    assert response.recommended_action == "block"
    assert response.effective_action == "block"
    assert any(c.checker_id == "integrity" and c.status == "error"
               and c.error_code == "integrity_invalid_encoding" for c in response.checker_results)
    assert digest not in response.model_dump_json()


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("encoding", ["utf8", "base64"])
@pytest.mark.parametrize("over_limit", [False, True])
def test_decoded_byte_limit_is_enforced_at_exact_boundary(stage, encoding, over_limit):
    approved = b"abc"
    raw = b"abcd" if over_limit else approved
    content = (raw.decode("ascii") if encoding == "utf8"
               else base64.b64encode(raw).decode("ascii"))
    response, digest = assess(content, stage, approved, encoding=encoding, max_bytes=3)
    assert response.evaluation_status == ("incomplete" if over_limit else "complete")
    assert not response.findings
    assert response.recommended_action == ("block" if over_limit else "allow")
    assert response.effective_action == ("block" if over_limit else "allow")
    assert any(c.checker_id == "integrity"
               and c.status == ("error" if over_limit else "passed")
               and c.error_code == ("integrity_byte_limit" if over_limit else None)
               for c in response.checker_results)
    assert digest not in response.model_dump_json()


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_content_format_label_does_not_canonicalize_bytes(stage):
    # JSON-looking content may be structurally invalid but the integrity-only
    # checker hashes raw UTF-8; it is not a JSON parser.
    content = '{"x": 1, "x": 2}'
    response, _ = assess(content, stage, content.encode("utf-8"), content_format="json")
    assert response.evaluation_status == "complete"
    assert not response.findings
    assert response.recommended_action == "allow"
    assert response.effective_action == "allow"
