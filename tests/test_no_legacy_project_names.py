"""Prevent accidentally reintroducing forbidden legacy project names.

Scan all tracked UTF-8 files regardless of filename extension. Binary files
that cannot be decoded as UTF-8 are skipped; a text file masquerading as .pptx
is still scanned. Git is the source of the tracked-file inventory.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Construct the forbidden names so this test does not itself trigger the scan.
FORBIDDEN = tuple(a + b for a, b in (
    ("vali", "nator"), ("vali", "sentry"), ("pre", "sidio")))
EXCEPTION = "guardoath/privacy_patterns/LICENSE"


def test_tracked_text_files_contain_no_forbidden_legacy_names():
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        capture_output=True, check=True)
    paths = [p.decode("utf-8") for p in result.stdout.split(b"\0") if p]
    errors = []
    pattern = re.compile("|".join(re.escape(x) for x in FORBIDDEN), re.IGNORECASE)
    for rel in paths:
        if rel == EXCEPTION:
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(content.splitlines(), 1):
            if pattern.search(line):
                errors.append(f"{rel}:{number}")
    assert not errors, "Legacy names found in tracked text: " + ", ".join(errors)
