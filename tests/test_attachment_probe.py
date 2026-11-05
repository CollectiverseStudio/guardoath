"""Evidence for trusted-byte file classification; no real files or external scanners."""
import hashlib
import pytest

from guardoath.attachment_probe import MAX_ATTACHMENT_BYTES, classify_attachment


@pytest.mark.parametrize("payload", [
    b"name,entry\nAlice,=1+2",
    b"\xef\xbb\xbfname,entry\r\nAlice,\"=2+3\"",
    b"name,entry\nBob,'=1+2",
])
def test_csv_bytes_are_recognized_but_not_claimed_fully_scanned(payload):
    result = classify_attachment(payload)
    assert result.kind == "csv"
    assert result.status == "recognized"
    assert result.reason == "csv_structure_recognized_only"
    assert result.csv_text_supported
    assert result.byte_count == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert "Alice" not in str(result) and "=1+2" not in str(result)


@pytest.mark.parametrize("payload,kind", [
    (b"%PDF-1.7\nsynthetic", "pdf"),
    (b"PK\x03\x04synthetic", "zip_container"),
    (b"PK\x05\x06synthetic", "zip_container"),
    (bytes.fromhex("D0CF11E0A1B11AE1") + b"synthetic", "ole_container"),
    (b"\x89PNG\r\n\x1a\nsynthetic", "image"),
    (b"\xff\xd8\xffsynthetic", "image"),
])
def test_known_binary_signatures_are_not_claimed_inspected(payload, kind):
    result = classify_attachment(payload)
    assert result.kind == kind
    assert result.status == "unsupported"
    assert not result.csv_text_supported
    assert result.reason.endswith("_parser_unavailable")


@pytest.mark.parametrize("payload", [
    b"I have attached my resume.",
    b"Please see report.csv attached.",
    b"alpha,beta\nonly-one-column",
    b"alpha,beta",
    b"alpha,beta\n1,2,3",
    b"plain text\nno file content",
    b"\xff\xfea\x00b\x00",
])
def test_prompt_mentions_and_ambiguous_text_do_not_activate_csv(payload):
    result = classify_attachment(payload)
    assert result.status != "recognized"
    assert not result.csv_text_supported


@pytest.mark.parametrize("payload,reason", [
    (b"", "empty_attachment"),
    (b"A" * (MAX_ATTACHMENT_BYTES + 1), "attachment_budget_exceeded"),
    (b"a,b\n\"unterminated", "malformed_csv"),
    (b"a,b\n\x00,1", "binary_or_unsupported_encoding"),
    (b"a,b\n" + b"1,2\n" * 1000, "csv_budget_exceeded"),
    ((b"c," * 256) + b"c\n1,2", "csv_budget_exceeded"),
])
def test_invalid_or_overbudget_inputs_are_not_clean_or_successful(payload, reason):
    result = classify_attachment(payload)
    assert result.status in {"invalid", "unsupported"}
    assert result.reason == reason
    assert not result.csv_text_supported


def test_unsupported_api_inputs_and_stable_non_leaking_metadata():
    with pytest.raises(TypeError, match="server-received bytes"):
        classify_attachment("name,entry\nAlice,=1+2")
    result = classify_attachment(b"a,b\n1,2")
    assert set(vars(result)) == {"kind", "status", "byte_count", "sha256", "reason"}
