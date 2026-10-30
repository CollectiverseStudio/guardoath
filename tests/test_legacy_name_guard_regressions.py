"""Adversarial regression tests for the repository-wide legacy-name guard.

Use a synthetic git repository to test real tracked-file enumeration. No
production files, checkers or existing test expectations are modified.
"""
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from test_no_legacy_project_names import test_tracked_text_files_contain_no_forbidden_legacy_names


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True)


@pytest.fixture
def tracked_repo(tmp_path):
    git(tmp_path, "init", "-q")
    return tmp_path


def run_guard(root):
    with patch("test_no_legacy_project_names.ROOT", root):
        test_tracked_text_files_contain_no_forbidden_legacy_names()


@pytest.mark.parametrize("filename", [
    "notes.txt", "slides.pptx", "nested/config.json", "source.py"])
def test_detects_retired_names_in_tracked_utf8_any_extension(tracked_repo, filename):
    path = tracked_repo / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("old project: " + "vali" + "nator", encoding="utf-8")
    git(tracked_repo, "add", filename)
    with pytest.raises(AssertionError, match="Legacy names"):
        run_guard(tracked_repo)


def test_untracked_text_is_not_scanned(tracked_repo):
    (tracked_repo / "untracked.txt").write_text("vali" + "sentry")
    run_guard(tracked_repo)


def test_binary_is_not_misclassified_as_utf8_text(tracked_repo):
    (tracked_repo / "binary.pptx").write_bytes(b"\xff\xfe\x00\x80")
    git(tracked_repo, "add", "binary.pptx")
    run_guard(tracked_repo)


def test_license_exception_is_exact_path_only(tracked_repo):
    allowed = tracked_repo / "guardoath/privacy_patterns/LICENSE"
    allowed.parent.mkdir(parents=True)
    allowed.write_text("pre" + "sidio")
    git(tracked_repo, "add", "guardoath/privacy_patterns/LICENSE")
    run_guard(tracked_repo)
    other = tracked_repo / "other/LICENSE"
    other.parent.mkdir(parents=True)
    other.write_text("pre" + "sidio")
    git(tracked_repo, "add", "other/LICENSE")
    with pytest.raises(AssertionError, match="other/LICENSE"):
        run_guard(tracked_repo)


def test_multiple_violations_report_all_file_locations(tracked_repo):
    for name in ("first.txt", "second.pptx"):
        (tracked_repo / name).write_text("old " + "vali" + "sentry")
        git(tracked_repo, "add", name)
    with pytest.raises(AssertionError) as exc:
        run_guard(tracked_repo)
    assert "first.txt:1" in str(exc.value)
    assert "second.pptx:1" in str(exc.value)
