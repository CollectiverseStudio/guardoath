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
        if count < 2:
            return result("unknown", "unsupported", "ambiguous_plain_text")
    except csv.Error:
        return result("unknown", "invalid", "malformed_csv")

    return result("csv", "recognized", "csv_structure_recognized_only")
