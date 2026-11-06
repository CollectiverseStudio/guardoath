"""Bounded file-content classification for the future authenticated attachment ingress.

This module *only* classifies server-received bytes. It does not accept a filename,
MIME label, URL or chat text as evidence of an attachment. No format here is
considered inspected for malicious content except structurally recognized CSV
(which still requires the independent csv_formula rule).
"""
from dataclasses import dataclass
import csv
import hashlib
from io import StringIO

MAX_ATTACHMENT_BYTES = 32_000
MAX_CSV_ROWS = 1_000
MAX_CSV_COLUMNS = 256
MAX_CSV_CELLS = 4_000


# Bytes that cannot be relied on as visible CSV data. The fixed-format parser
# accepts these in otherwise valid text, but terminal escapes and bidirectional
# override/isolate characters can conceal the displayed value from the reviewer.
# Reject them at the *byte classification* boundary before any CSV checker can
# return a clean decision. Tabs, CR/LF and ordinary international text remain valid.
UNSAFE_BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")

# Some importers silently ignore leading invisible format characters, making
# a formula prefix hard for a human reviewer to see. Reject only if one of
# these invisibles occurs *before* an actual formula trigger; ordinary Unicode
# names, Persian joiners and emoji sequences elsewhere in a cell are valid.
# This is defensive ambiguity rejection, not an assertion about every
# spreadsheet application's interpretation of these code points.
INVISIBLE_FORMULA_PREFIX = frozenset("\u00ad\u034f\u180e\u200b\u200c\u200d\u2060")
FORMULA_PREFIX = frozenset("=+-@")
LEADING_CSV_WHITESPACE = frozenset(" \t\r\n\u00a0\ufeff")


def has_hidden_csv_formula_prefix(cell: str) -> bool:
    """Detect an invisible leading character concealing a formula trigger.

    Work is linear in the length of this already size-bounded CSV cell.
    Preserve interior joiners, legitimate non-formula text and apostrophe escapes.
    """
    saw_invisible = False
    for character in cell:
        if character in INVISIBLE_FORMULA_PREFIX:
            saw_invisible = True
        elif character in LEADING_CSV_WHITESPACE:
            continue
        else:
            return saw_invisible and character in FORMULA_PREFIX
    return False


def has_unsafe_csv_controls(value: str) -> bool:
    return any(
        (0 < ord(char) < 32 and char not in "\t\r\n")
        # C1 includes NEL, CSI and OSC, which can change line/terminal rendering
        # even when a strict UTF-8 CSV parser accepts the cell as ordinary text.
        or 0x7F <= ord(char) <= 0x9F
        or char in UNSAFE_BIDI_CONTROLS
        for char in value
    )


@dataclass(frozen=True)
class FileProbe:
    kind: str
    status: str
    byte_count: int
    sha256: str
    reason: str

    @property
    def csv_text_supported(self) -> bool:
        return self.kind == "csv" and self.status == "recognized"


def classify_attachment(data: bytes) -> FileProbe:
    """Classify real, already-received bytes without inspecting other file formats.

    Only a strict UTF-8 CSV with >=2 records and stable column counts is
    recognized as CSV. Ambiguous or unsupported inputs must not be treated
    as a successful document scan. Upper bound before any parser work.
    """
    if not isinstance(data, bytes):
        raise TypeError("Expected server-received bytes")
    size = len(data)
    # Hash is opaque and never reveals the file body; no filename is accepted.
    digest = hashlib.sha256(data).hexdigest()

    def result(kind: str, status: str, reason: str) -> FileProbe:
        return FileProbe(kind, status, size, digest, reason)

    if not size:
        return result("unknown", "invalid", "empty_attachment")
    if size > MAX_ATTACHMENT_BYTES:
        return result("unknown", "invalid", "attachment_budget_exceeded")
    if data.startswith(b"%PDF-"):
        return result("pdf", "unsupported", "pdf_parser_unavailable")
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return result("zip_container", "unsupported", "zip_parser_unavailable")
    if data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        return result("ole_container", "unsupported", "ole_parser_unavailable")
    if data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")):
        return result("image", "unsupported", "image_parser_unavailable")

    try:
        # BOM is valid UTF-8 CSV, but UTF-16 and other encodings remain unsupported.
        value = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return result("unknown", "unsupported", "unsupported_encoding")
    if "\x00" in value:
        return result("unknown", "unsupported", "binary_or_unsupported_encoding")
    if has_unsafe_csv_controls(value):
        return result("unknown", "invalid", "unsafe_csv_control_characters")
    try:
        rows = csv.reader(StringIO(value, newline=""), strict=True)
        first_columns = None
        count = cells = 0
        for row in rows:
            count += 1
            cells += len(row)
            if count > MAX_CSV_ROWS or len(row) > MAX_CSV_COLUMNS or cells > MAX_CSV_CELLS:
                return result("unknown", "invalid", "csv_budget_exceeded")
            if first_columns is None:
                first_columns = len(row)
                if first_columns < 2:
                    return result("unknown", "unsupported", "ambiguous_plain_text")
            if len(row) != first_columns:
                return result("unknown", "unsupported", "inconsistent_csv_columns")
            if any(has_hidden_csv_formula_prefix(cell) for cell in row):
                return result("unknown", "invalid", "unsafe_csv_invisible_formula_prefix")
        if count < 2:
            return result("unknown", "unsupported", "ambiguous_plain_text")
    except csv.Error:
        return result("unknown", "invalid", "malformed_csv")

    return result("csv", "recognized", "csv_structure_recognized_only")
