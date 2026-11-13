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



@pytest.mark.parametrize("payload", [
    b"name,value\nrow,\x1b=2+3",                    # ANSI escape in a CSV cell
    b"name,value\nrow,\x07data",                   # terminal bell
    b"name,value\nrow,\x7fdata",                   # DEL control
    b"name,value\nrow,\"\x1b[31m=3+4\"",          # quoted cell does not grant trust
    "name,value\nrow,\u202e=3+4".encode("utf-8"), # right-to-left override
    "name,value\nrow,\u2066=3+4\u2069".encode("utf-8"), # bidi isolate
])
def test_unsafe_control_or_bidi_formatting_never_counts_as_clean_csv(payload):
    report = classify_attachment(payload)
    assert report.kind == "unknown"
    assert report.status == "invalid"
    assert report.reason == "unsafe_csv_control_characters"
    assert not report.csv_text_supported
    assert report.byte_count == len(payload)
    assert report.sha256 == hashlib.sha256(payload).hexdigest()
    assert "row" not in str(report)


@pytest.mark.parametrize("payload", [
    b"name,value\nrow,normal\ttext",
    b'name,value\nrow,"quoted\nmultiline"',
    "name,value\nمستخدم,قيمة".encode("utf-8"),
])
def test_ordinary_unicode_and_legal_csv_tabs_and_multiline_are_recognized(payload):
    report = classify_attachment(payload)
    assert report.kind == "csv"
    assert report.status == "recognized"
    assert report.csv_text_supported


@pytest.mark.parametrize("invisible", [
    "\u200b",  # ZERO WIDTH SPACE
    "\u2060",  # WORD JOINER
    "\u00ad",  # SOFT HYPHEN
    "\u200c",  # ZERO WIDTH NON-JOINER
    "\u200d",  # ZERO WIDTH JOINER
    "\u034f",  # COMBINING GRAPHEME JOINER
    "\u180e",  # MONGOLIAN VOWEL SEPARATOR
    "\ufeff",  # In-cell BOM is an invisible formula prefix (unlike file-start BOM)
])
@pytest.mark.parametrize("trigger", ["=", "+", "-", "@"])
def test_invisible_leading_csv_formula_prefix_is_never_attested_clean(invisible, trigger):
    payload = ("name,value\\nSynthetic,  " + invisible + " " + trigger + "2+3").replace("\\n", "\n").encode("utf-8")
    report = classify_attachment(payload)
    assert report.kind == "unknown"
    assert report.status == "invalid"
    assert report.reason == "unsafe_csv_invisible_formula_prefix"
    assert report.byte_count == len(payload)
    assert report.sha256 == hashlib.sha256(payload).hexdigest()
    assert not report.csv_text_supported
    assert "Synthetic" not in str(report)


@pytest.mark.parametrize("value", [
    "\u200btext",           # invisible followed by benign ordinary text
    "text\u200b=2+3",      # invisible is not a leading prefix
    "👩\u200d🔬",           # Unicode emoji joiner inside a cell
    "\ufeffordinary",       # in-cell BOM before benign text is not a formula
    "\u200cفارسی",         # script joiner with no formula marker
    "'\u200b=2+3",          # explicit leading apostrophe is not a formula trigger
    "normal value",
])
def test_benign_unicode_cells_and_escaped_formula_text_remain_supported(value):
    payload = ("name,value\nSynthetic," + value).encode("utf-8")
    report = classify_attachment(payload)
    assert report.status == "recognized"
    assert report.kind == "csv"
    assert report.reason == "csv_structure_recognized_only"


@pytest.mark.parametrize("control", [chr(point) for point in range(0x80, 0xA0)])
def test_every_utf8_c1_control_is_invalid_csv_not_inspected(control):
    # U+0085 (NEL), U+009B (CSI) and U+009D (OSC) can change rendering;
    # the same structural fail-closed contract applies to all 32 C1 controls.
    payload = ("name,value\\nSynthetic," + control + "=2+3").replace("\\n", "\n").encode("utf-8")
    result = classify_attachment(payload)
    assert result.kind == "unknown"
    assert result.status == "invalid"
    assert result.reason == "unsafe_csv_control_characters"
    assert result.byte_count == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert not result.csv_text_supported
    assert "Synthetic" not in str(result) and "=2+3" not in str(result)


@pytest.mark.parametrize("value", [
    "Málaga", "東京", "مرحبا", "👩\\u200d🔬", "plain text with normal spaces",
])
def test_c1_integrity_check_keeps_legitimate_international_csv(value):
    payload = ("name,value\\nSynthetic," + value).replace("\\n", "\n").encode("utf-8")
    report = classify_attachment(payload)
    assert report.kind == "csv" and report.status == "recognized"
    assert report.reason == "csv_structure_recognized_only"


@pytest.mark.parametrize("payload", [
    b"column,value\n%PDF-1.7,synthetic\nrow,1",
    b"column,value\n  %PDF-1.7,synthetic\nrow,1",
    b"\xef\xbb\xbfcolumn,value\r\n\t%PDF-1.7,synthetic\r\nrow,1",
    b"column,value\r%PDF-1.7,synthetic\rrow,1",
])
def test_csv_shaped_pdf_preamble_is_unsupported_not_inspected(payload):
    """A PDF header within its early preamble defeats byte-zero magic checks."""
    result = classify_attachment(payload)
    assert (result.kind, result.status, result.reason) == (
        "pdf", "unsupported", "pdf_parser_unavailable")
    assert not result.csv_text_supported
    assert result.byte_count == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert "synthetic" not in str(result)


@pytest.mark.parametrize("payload", [
    b"column,value\\nrow,%PDF-1.7\\nrow,1",
    b'column,value\\nrow," %PDF-1.7"\\nrow,1',
    b"column\\tvalue\\nrow\\t%PDF-1.7\\nrow\\t1",
    b"column,value\\nrow,\\xef\\xbb\\xbf%PDF-1.7\\nrow,1",
])
def test_pdf_header_at_csv_or_tsv_cell_start_is_not_certified(payload):
    """A PDF reader may locate the header inside the first 1,024 bytes."""
    report = classify_attachment(payload)
    assert (report.kind, report.status, report.reason) == (
        "pdf", "unsupported", "pdf_parser_unavailable")
    assert not report.csv_text_supported
    assert report.sha256 == hashlib.sha256(payload).hexdigest()
    assert "%PDF-1.7" not in str(report)


def test_inline_pdf_literal_in_csv_cell_is_not_a_pdf_header():
    payload = b"column,value\nrow,ordinary %PDF-1.7 reference"
    result = classify_attachment(payload)
    assert (result.kind, result.status) == ("csv", "recognized")
    assert result.csv_text_supported



def test_file_start_bom_is_distinct_from_a_concealed_in_cell_bom():
    """Only the initial UTF-8 signature is stripped by utf-8-sig."""
    header_bom = b"\xef\xbb\xbf"
    in_cell_bom = b"\xef\xbb\xbf"
    safe = classify_attachment(header_bom + b"label,value\nSynthetic,ordinary")
    assert safe.kind == "csv" and safe.status == "recognized"

    hidden = header_bom + b"label,value\nSynthetic," + in_cell_bom + b"=2+3"
    result = classify_attachment(hidden)
    assert (result.kind, result.status, result.reason) == (
        "unknown", "invalid", "unsafe_csv_invisible_formula_prefix")
    assert result.sha256 == hashlib.sha256(hidden).hexdigest()
    assert not result.csv_text_supported
    assert "Synthetic" not in str(result) and "=2+3" not in str(result)
