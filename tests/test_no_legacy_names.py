"""Reject accidental reintroduction of legacy names and package paths."""
from pathlib import Path
import importlib.util
import re

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"valinator|valisentry|vali[-_ ]sentry", re.I)
SKIP = {".git", ".dashboard-venv", "node_modules", "__pycache__",
        ".guardoath-reports", "test-results", "playwright-report", ".pytest_cache"}
ALLOWED = {"docs/guardoath-migration.md", "guardoath/legacy_env.py",
           "tests/test_legacy_env.py", "tests/test_no_legacy_names.py"}
HISTORY = 'GuardOath was previously developed as "Valinator" and briefly branded "Valisentry".'


def test_no_legacy_names_in_tracked_source():
    problems = []
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if any(part in SKIP or part.startswith(".venv") or part.endswith(".egg-info")
               for part in rel.parts):
            continue
        if PATTERN.search(rel.as_posix()):
            problems.append(f"Legacy path: {rel}")
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        if rel.as_posix() in ALLOWED:
            continue
        for number, line in enumerate(content.splitlines(), 1):
            if PATTERN.search(line) and not (
                    rel.as_posix() == "README.md" and line == HISTORY):
                problems.append(f"{rel}:{number}")
    assert not problems, "\n".join(problems)
    assert importlib.util.find_spec("valinator") is None
    assert importlib.util.find_spec("guardoath") is not None
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.splitlines().count(HISTORY) == 1
