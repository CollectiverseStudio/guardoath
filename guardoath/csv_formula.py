"""CSV/TSV spreadsheet export formula signals; does not execute formulas or alter content.

Intended only for a dedicated delimited-export policy. Never use to classify arbitrary prose.
"""
import csv
from io import StringIO

from guardoath.attachment_probe import has_hidden_csv_formula_prefix, has_unsafe_csv_controls
from guardoath.models import CheckerResult, Finding

MAX_CSV_ROWS = 1000
MAX_CSV_CELLS = 4000
MAX_CSV_COLUMNS = 256
# Spreadsheet applications vary in their import rules. Conservative indicators only.
FORMULA_PREFIXES = ("=", "+", "-", "@")


async def csv_formula_check(request, policy, stage):
    def result(status, *, findings=(), code=None):
        return CheckerResult(
            checker_id="csv_formula", checker_version="1", status=status,
            findings=findings, error_code=code,
        )

    if request.content_format != "text":
        return result("error", code="csv_text_required")
    if "\x00" in request.content:
        return result("error", code="csv_invalid_content")
    # The file-byte ingress already rejects deceptive control characters. The
    # same checker also runs on ordinary text checks, which bypass that probe:
    # never let those direct Input/Output routes call a hidden formula clean.
    if has_unsafe_csv_controls(request.content):
        return result("error", code="unsafe_csv_control_characters")
    # Match the byte probe's conservative TSV recognition. The file ingress
    # already demands bounded, structurally valid rows before this checker.
    # Continue using comma parsing for ordinary CSV and chat examples.
    first_record = request.content.splitlines()[0] if request.content.splitlines() else ""
    delimiter = "\t" if "\t" in first_record and "," not in first_record else ","
    # If an export mixes comma and tab delimiters, evaluate BOTH plausible
    # spreadsheet import interpretations. A hostile data row must not evade
    # detection by choosing the header delimiter while a different importer
    # splits a second cell on the other character. Each pass stays bounded.
    delimiters = (delimiter,)
    if "," in request.content and "\t" in request.content:
        delimiters += ("\t" if delimiter == "," else ",",)
    dangerous = False
    try:
        for separator in delimiters:
            total = 0
            reader = csv.reader(StringIO(request.content, newline=""),
                                delimiter=separator, strict=True)
            for row_number, row in enumerate(reader, start=1):
                if row_number > MAX_CSV_ROWS or len(row) > MAX_CSV_COLUMNS:
                    return result("error", code="csv_inspection_budget_exceeded")
                total += len(row)
                if total > MAX_CSV_CELLS:
                    return result("error", code="csv_inspection_budget_exceeded")
                for cell in row:
                    # Reuse the byte-ingress display-integrity policy on BOTH
                    # plausible CSV/TSV dialects, including direct text checks.
                    if has_hidden_csv_formula_prefix(cell):
                        return result("error", code="unsafe_csv_invisible_formula_prefix")
                    # Preserve protective apostrophes. Spreadsheet importers may
                    # trim leading whitespace, BOM or nonbreaking space.
                    value = cell.lstrip(" \t\r\n\ufeff\u00a0")
                    if value.startswith(FORMULA_PREFIXES):
                        dangerous = True
    except (csv.Error, UnicodeError):
        return result("error", code="csv_invalid_content")

    if not dangerous:
        return result("passed")
    return result("flagged", findings=(Finding(
        rule_id="csv_formula.potential_formula",
        category="data_integrity",
        severity="high",
        explanation="A CSV or TSV field begins with a spreadsheet formula trigger after leading whitespace. Review the export or neutralize spreadsheet formula interpretation before opening it.",
    ),))


csv_formula_check.checker_version = "1"
