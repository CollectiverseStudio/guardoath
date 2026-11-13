"""Bounded file-content classification for the future authenticated attachment ingress.

This module *only* classifies server-received bytes. It does not accept a filename,
MIME label, URL or chat text as evidence of an attachment. No format here is
considered inspected for malicious content except structurally recognized CSV/TSV
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
# Include directional marks that are visually silent but can precede a formula
# trigger and bypass the strict CSV formula-prefix checker. Conservative policy:
# ambiguous display semantics are a structural inspection failure, not clean CSV.
UNSAFE_BIDI_CONTROLS = frozenset("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")

# Some importers silently ignore leading invisible format characters, making
# a formula prefix hard for a human reviewer to see. Reject only if one of
# these invisibles occurs *before* an actual formula trigger; ordinary Unicode
# names, Persian joiners and emoji sequences elsewhere in a cell are valid.
# This is defensive ambiguity rejection, not an assertion about every
# spreadsheet application's interpretation of these code points.
# A UTF-8 BOM is stripped only once at the START OF THE FILE. An additional
# U+FEFF at the start of an individual cell may conceal a spreadsheet formula;
# treat it as an invisible prefix, not harmless leading whitespace.
INVISIBLE_FORMULA_PREFIX = frozenset("\u00ad\u034f\u180e\u200b\u200c\u200d\u2060\ufeff")
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


def has_pdf_header_preamble(data: bytes) -> bool:
    """Conservatively reject PDF/CSV polyglots with an early PDF header at a\n    line start or CSV/TSV cell start.

    PDF readers can tolerate a preamble before %PDF-, so byte-zero sniffing can
    mistake a two-column CSV-looking polyglot for an inspected spreadsheet.
    Inspect at most the first 1,024 bytes; the result is unsupported, NEVER
    evidence that GuardOath parsed or scanned a PDF.
    """
    def starts_header(fragment: bytes) -> bool:
        # A CSV delimiter or quote can conceal a real PDF header within a
        # syntactically valid CSV cell. PDF readers search the early byte range,
        # not necessarily a line boundary. Never certify such a polyglot as CSV.
        visible = fragment.lstrip(b' \\t"')
        if visible.startswith(b"\\xef\\xbb\\xbf"):
            visible = visible[3:].lstrip(b' \\t"')
        return visible.startswith(b"%PDF-")

    for line in data[:1024].splitlines():
        if starts_header(line):
            return True
        # Evaluate CSV- and TSV-cell starts, including quotes and whitespace.
        # This is deliberately conservative, and is not a complete PDF parser.
        for fragment in line.replace(b"\\t", b",").split(b",")[1:]:
            if starts_header(fragment):
                return True
    return False


@dataclass(frozen=True)
class FileProbe:
    kind: str
    status: str
    byte_count: int
    sha256: str
    reason: str

    @property
    def csv_text_supported(self) -> bool:
        return self.kind in ("csv", "tsv") and self.status == "recognized"


def classify_attachment(data: bytes) -> FileProbe:
    """Classify real, already-received bytes without inspecting other file formats.

    Only strict UTF-8 comma- or tab-separated tables with >=2 records and
    stable column counts are recognized as CSV or TSV. Ambiguous or unsupported inputs must not be treated
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
    if has_pdf_header_preamble(data):
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
    # Recognize TSV from the actual first record, not an extension or MIME label.
    # Requiring a tab and no comma in its header keeps ordinary comma CSV
    # unchanged, including comma CSV with tabs inside later fields. TSV columns
    # may contain quoted commas; mixed-delimiter/ambiguous headers are not trusted.
    first_record = value.splitlines()[0] if value.splitlines() else ""
    is_tsv = "\t" in first_record and "," not in first_record
    delimiter = "\t" if is_tsv else ","
    kind = "tsv" if is_tsv else "csv"
    try:
        rows = csv.reader(StringIO(value, newline=""), delimiter=delimiter, strict=True)
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

    # A spreadsheet importer may use the alternate delimiter if both comma and
    # tab bytes occur. Do not attest a hidden formula that is only at the start
    # of an alternate-dialect cell. Keep this independent pass bounded and
    # fail closed if it cannot parse the ambiguous export.
    if "," in value and "\t" in value:
        other = "," if is_tsv else "\t"
        other_cells = 0
        try:
            for row_number, row in enumerate(
                    csv.reader(StringIO(value, newline=""), delimiter=other, strict=True), 1):
                other_cells += len(row)
                if (row_number > MAX_CSV_ROWS or len(row) > MAX_CSV_COLUMNS
                        or other_cells > MAX_CSV_CELLS):
                    return result("unknown", "invalid", "csv_budget_exceeded")
                if any(has_hidden_csv_formula_prefix(cell) for cell in row):
                    return result("unknown", "invalid", "unsafe_csv_invisible_formula_prefix")
        except csv.Error:
            return result("unknown", "invalid", "malformed_csv")

    return result(kind, "recognized", kind + "_structure_recognized_only")
