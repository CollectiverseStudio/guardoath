"""Release provenance uses a real committed SHA, never a fabricated deployment claim."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app, release_source_sha

ROOT = Path(__file__).resolve().parents[1]
SHA = "c" * 40


def load_build():
    spec = importlib.util.spec_from_file_location("lambda_build", ROOT / "deploy/aws/build_lambda.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_source_reader_accepts_only_exact_full_git_sha(tmp_path):
    meta = tmp_path / "_release_source.json"
    assert release_source_sha(meta) is None
    for raw in ("not-json", '{"source_sha":null}', '{"source_sha":"short"}',
                '{"source_sha":"'+ ("g" * 40) +'"}', "[]"):
        meta.write_text(raw, encoding="utf-8")
        assert release_source_sha(meta) is None
    meta.write_text(json.dumps({"source_sha": SHA}), encoding="utf-8")
    assert release_source_sha(meta) == SHA


def test_healthz_exposes_public_source_identity_without_tenant_data(monkeypatch):
    import guardoath.app as app
    monkeypatch.setattr(app, "release_source_sha", lambda: SHA)
    response = TestClient(create_app(credentials={"test-key":"demo"})).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "profile": "local-prototype", "source_sha": SHA,
    }
    assert "secret" not in response.text.lower()
    assert "tenant_id" not in response.text


def test_build_source_sha_requires_clean_committed_app(monkeypatch, tmp_path):
    build = load_build()
    seen = []
    class Result:
        def __init__(self, stdout):
            self.stdout = stdout
    def git(command, *, check, capture_output, text):
        assert command[:3] == ["git", "-C", str(tmp_path)]
        assert check and capture_output and text
        seen.append(command[3:])
        return Result(SHA + "\n" if "rev-parse" in command else "")
    monkeypatch.setattr(build.subprocess, "run", git)
    assert build.release_source_sha(tmp_path) == SHA
    assert any("status" in args for args in seen)
    assert any("untracked-files=all" in item for args in seen for item in args)


def test_build_refuses_modified_guardoath_source(monkeypatch, tmp_path):
    build = load_build()
    class Result:
        def __init__(self, stdout):
            self.stdout = stdout
    def git(command, *, check, capture_output, text):
        return Result(SHA if "rev-parse" in command else " M guardoath/app.py\n")
    monkeypatch.setattr(build.subprocess, "run", git)
    with pytest.raises(ValueError, match="uncommitted changes"):
        build.release_source_sha(tmp_path)


def test_build_refuses_missing_or_invalid_git_head(monkeypatch, tmp_path):
    build = load_build()
    class Result:
        def __init__(self, stdout):
            self.stdout = stdout
    monkeypatch.setattr(build.subprocess, "run",
                        lambda command, **kwargs: Result("incomplete"))
    with pytest.raises(ValueError, match="full source commit"):
        build.release_source_sha(tmp_path)
