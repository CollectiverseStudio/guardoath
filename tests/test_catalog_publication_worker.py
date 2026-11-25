"""Real Git/source/app worker acceptance with synthetic remote and audit transport."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from admin_test_support import AdminTestDynamo
from guardoath import catalog_publication_worker as worker, state_contracts
from guardoath.admin_catalog_drafts import AdminCatalogDrafts
from guardoath.admin_catalog_manifest import reviewed_metadata_manifest
from guardoath.admin_catalog_preview import preview_rule_description
from guardoath.admin_catalog_publication import source_plan_from_manifest
from guardoath.admin_catalog_publication_state import (
    DynamoCatalogPublicationState, EMPTY_CONTROL_HASH, PublicationConflict,
    PublicationUnavailable, document_hash,
)

OWNER = "12345678-1234-1234-1234-123456789012"
KEY = b"synthetic-worker-audit-key-not-production-0123456789"
ORIGIN = "https://github.com/CollectiverseStudio/guardoath.git"
TARGET = {"category_id": "agent_safety", "check_id": "lexical_guards", "rule_id": "agent.git_force"}


def git(root, *args):
    result = subprocess.run(["git", "-c", "core.longpaths=true", "-C", str(root), *map(str, args)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = Path(__file__).resolve().parents[1]
    source = tmp_path_factory.mktemp("catalog-worker-template") / "source"
    source.mkdir()
    for name in ("guardoath", "policies"):
        shutil.copytree(root / name, source / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "_release_source.json"))
    (source / "tests").mkdir()
    for name in ("catalog_source_roundtrip_runner.py", "admin_test_support.py"):
        shutil.copy2(root / "tests" / name, source / "tests" / name)
    for name in (".gitignore", "pyproject.toml", "README.md"):
        shutil.copy2(root / name, source / name)
    git(source, "init", "-b", "main")
    git(source, "config", "core.autocrlf", "false")
    git(source, "config", "user.name", "Synthetic catalog acceptance")
    git(source, "config", "user.email", "synthetic@guardoath.invalid")
    git(source, "add", ".")
    git(source, "commit", "-m", "Synthetic exact source fixture")
    return source


@pytest.fixture
def repository(template, tmp_path, monkeypatch):
    source = tmp_path / "repository"
    result = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.longpaths=true",
                             "clone", "--quiet", "--no-hardlinks", str(template), str(source)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    git(source, "config", "core.autocrlf", "false")
    git(source, "remote", "set-url", "origin", ORIGIN)
    git(source, "config", "user.name", "Synthetic catalog acceptance")
    git(source, "config", "user.email", "synthetic@guardoath.invalid")
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_STATE", True)
    # Only the remote network call is synthetic. All local Git, source compiler,
    # fresh application, shared Engine and signed persistence boundaries are real.
    monkeypatch.setattr(worker, "_remote_main", lambda path: git(path, "rev-parse", "HEAD"))
    return source


def source_registry(source):
    result = subprocess.run([sys.executable, "-B", "-c",
        "import json; from guardoath.admin_catalog import published_catalog; print(json.dumps(published_catalog()))"],
        cwd=source, env=worker._environment(), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def signed_review(source, database, *, reverse=False, reference="worker-metadata-draft-0001"):
    registry = source_registry(source)
    rules = [rule for category in registry["domains"] for check in category["controls"]
             for rule in check["rules"] if rule["rule_id"] == TARGET["rule_id"]]
    assert len(rules) == 1
    rule = rules[0]
    history = json.loads((source / worker.HISTORY).read_text())
    title, use_case = "Reviewed worker guidance for force push", rule["use_case"]
    if reverse:
        last = history["releases"][-1]
        title = last["changes"].get("title", {"before": rule["title"]})["before"]
        use_case = last["changes"].get("use_case", {"before": rule["use_case"]})["before"]
    elif title == rule["title"]:
        title = "Alternate reviewed worker force push guidance"
    proposal = {"expected_registry_version": registry["registry_version"],
        "proposed_registry_version": str(int(registry["registry_version"]) + 1),
        **TARGET, "title": title, "use_case": use_case}
    drafts = AdminCatalogDrafts(database, "audit-table", KEY,
        clock=lambda: datetime.now(timezone.utc))
    draft = drafts.save(actor=OWNER, nonce=reference,
        reason="Clarify reviewed source metadata with worker acceptance",
        preview=preview_rule_description(lambda: registry, **proposal), title=title, use_case=use_case)
    review = drafts.review(actor=OWNER, draft_id=draft["draft_id"], nonce=reference + "-review",
        reason="Independently inspect metadata before publication request",
        confirmation="REVIEW " + draft["draft_id"][-8:],
        baseline_sha256=draft["baseline_sha256"], proposal_sha256=draft["proposal_sha256"])
    plan = source_plan_from_manifest(reviewed_metadata_manifest(lambda: registry, draft=draft, review=review))
    return drafts, draft, review, plan


def request(source, tmp_path, *, reverse=False):
    database_path = tmp_path / "audit.sqlite3"
    database = AdminTestDynamo(database_path)
    drafts, draft, review, plan = signed_review(source, database, reverse=reverse)
    state = DynamoCatalogPublicationState(database, "audit-table", KEY)
    request_id = state.request_id(OWNER, "worker-publication-action-0001")
    fields = {"request_id": request_id, "actor_sub": OWNER,
        "review_id": review["review_id"], "draft_id": draft["draft_id"],
        "review_sha256": document_hash(review), "draft_sha256": document_hash(draft),
        "base_source_sha": git(source, "rev-parse", "HEAD"),
        "operation": "reverse_latest" if reverse else "publish_metadata",
        "registry_version": plan["registry_version"],
        "candidate_registry_version": plan["candidate_registry_version"],
        "baseline_sha256": plan["reviewed_source_sha256"],
        "candidate_metadata_sha256": plan["candidate_metadata_sha256"],
        "target": deepcopy(TARGET), "plan": plan, "plan_sha256": document_hash(plan),
        "reason": "Explicit reviewed metadata publication request", "created_at": int(time.time()),
        "request_hash": document_hash({"review_id": review["review_id"], "operation": "reverse" if reverse else "publish"})}
    saved = state.reserve(fields, EMPTY_CONTROL_HASH)
    return {"database": database, "database_path": database_path, "state": state,
            "drafts": drafts, "intent": saved, "request_id": request_id,
            "work_root": tmp_path / "preparations"}


def run(source, fixture):
    return worker.prepare_publication(fixture["request_id"], state=fixture["state"],
        drafts=fixture["drafts"], repository=source, work_root=fixture["work_root"])


@pytest.mark.parametrize("phase", [False, None, 1, "true"])
def test_source_gate_refuses_before_state_source_or_cloud_access(tmp_path, monkeypatch, phase):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_STATE", phase)
    with pytest.raises(PublicationUnavailable, match="not enabled"):
        worker.prepare_publication("a" * 64, state=None, drafts=None, repository=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_real_source_commit_app_engine_and_signed_receipt_are_local_only(repository, tmp_path, monkeypatch):
    fixture = request(repository, tmp_path)
    base = git(repository, "rev-parse", "HEAD")
    before = (repository / worker.HISTORY).read_bytes()
    result = run(repository, fixture)
    assert result["status"] == "source_prepared"
    assert result["remote_published"] is result["ci_verified"] is result["deployed"] is False
    assert result["base_source_sha"] == base and result["candidate_source_sha"] != base
    assert git(repository, "rev-parse", "HEAD") == base
    assert git(repository, "branch", "--format=%(refname:short)") == "main"
    assert git(repository, "status", "--porcelain") == ""
    assert (repository / worker.HISTORY).read_bytes() == before
    workspace = fixture["work_root"] / fixture["request_id"]
    candidate = workspace / "source"
    assert git(candidate, "rev-list", "--parents", "-n", "1", "HEAD") == result["candidate_source_sha"] + " " + base
    assert git(candidate, "diff-tree", "--no-commit-id", "--name-only", "-r", base, "HEAD") == worker.HISTORY
    assert hashlib.sha256((candidate / worker.HISTORY).read_bytes()).hexdigest() == result["history_sha256"]
    baseline_report = json.loads((workspace / "baseline.json").read_text())
    candidate_report = json.loads((workspace / "candidate.json").read_text())
    assert baseline_report["tenant_rows"] == candidate_report["tenant_rows"]
    assert baseline_report["outcomes"] == candidate_report["outcomes"]
    assert len(candidate_report["outcomes"]) == 12
    reopened = AdminTestDynamo(fixture["database_path"])
    reader = DynamoCatalogPublicationState(reopened, "audit-table", KEY)
    assert reader.history(fixture["request_id"])[1] == result
    assert reader.read_control()["state"] == "SOURCE_PREPARED"
    reopened.close()
    monkeypatch.setattr(worker, "_remote_main", lambda _: pytest.fail("History must not reexecute source work"))
    assert run(repository, fixture) == result


@pytest.mark.parametrize("fault", ["dirty", "untracked", "detached", "origin", "remote", "hidden-change", "symlink", "wrong-root"])
def test_source_admission_failures_never_claim(repository, tmp_path, monkeypatch, fault):
    fixture = request(repository, tmp_path)
    source = repository
    if fault == "dirty":
        (source / "README.md").write_text("Changed source\n")
    elif fault == "untracked":
        (source / "unexpected.txt").write_text("Unexpected\n")
    elif fault == "detached":
        git(source, "checkout", "--detach")
    elif fault == "origin":
        git(source, "remote", "set-url", "origin", "https://github.com/untrusted/project.git")
    elif fault == "remote":
        monkeypatch.setattr(worker, "_remote_main", lambda _: "f" * 40)
    elif fault == "hidden-change":
        git(source, "update-index", "--assume-unchanged", "README.md")
        (source / "README.md").write_text("Hidden from ordinary git status\n")
        assert git(source, "status", "--porcelain") == ""
    elif fault == "symlink":
        source = tmp_path / "linked-source"
        try:
            source.symlink_to(repository, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            # Directory junctions need no Windows symlink privilege and still
            # exercise the real resolve-versus-supplied-path rejection.
            result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(source), str(repository)],
                                    capture_output=True, text=True)
            assert result.returncode == 0, result.stdout + result.stderr
    elif fault == "wrong-root":
        source = repository / "guardoath"
    with pytest.raises((PublicationUnavailable, PublicationConflict)):
        run(source, fixture)
    assert fixture["state"].read_control()["state"] == "REQUESTED"
    assert fixture["state"].history(fixture["request_id"])[1] is None
    assert not fixture["work_root"].exists()


def test_review_binding_corruption_fails_before_checkout(repository, tmp_path):
    fixture = request(repository, tmp_path)
    row = fixture["database"].get_item(TableName="audit-table",
        Key={"event_id": {"S": fixture["intent"]["review_id"]}})["Item"]
    row["integrity_mac"]["S"] = "0" * 64
    fixture["database"].seed("audit-table", row)
    with pytest.raises(PublicationUnavailable, match="binding"):
        run(repository, fixture)
    assert fixture["state"].read_control()["state"] == "REQUESTED"
    assert not fixture["work_root"].exists()


def test_lost_claim_acknowledgement_never_writes_source_or_retries(repository, tmp_path, monkeypatch):
    fixture = request(repository, tmp_path)
    original = fixture["database"].put_item
    def uncertain(**kwargs):
        result = original(**kwargs)
        row = kwargs["Item"].get("catalog_publication", {}).get("S", "")
        if row and json.loads(row).get("state") == "PREPARING":
            raise TimeoutError("Synthetic acknowledgement loss")
        return result
    monkeypatch.setattr(fixture["database"], "put_item", uncertain)
    with pytest.raises(PublicationUnavailable):
        run(repository, fixture)
    assert fixture["state"].read_control()["state"] == "PREPARING"
    candidate = fixture["work_root"] / fixture["request_id"] / "source"
    assert git(candidate, "rev-parse", "HEAD") == fixture["intent"]["base_source_sha"]
    assert git(candidate, "status", "--porcelain") == ""
    monkeypatch.setattr(worker, "_remote_main", lambda _: pytest.fail("Unknown claim cannot retry native work"))
    with pytest.raises(PublicationConflict, match="already claimed"):
        run(repository, fixture)


def test_lost_completion_response_recovers_only_existing_signed_result(repository, tmp_path, monkeypatch):
    fixture = request(repository, tmp_path)
    fixture["database"].lose_transaction_response = True
    with pytest.raises(PublicationUnavailable):
        run(repository, fixture)
    saved = fixture["state"].history(fixture["request_id"])[1]
    assert saved and fixture["state"].read_control()["state"] == "SOURCE_PREPARED"
    monkeypatch.setattr(worker, "_remote_main", lambda _: pytest.fail("Recovery cannot run a second worker"))
    assert run(repository, fixture) == saved


@pytest.mark.parametrize("change", ["tracked", "untracked"])
def test_out_of_band_source_change_after_claim_retains_failed_attempt(repository, tmp_path, monkeypatch, change):
    fixture = request(repository, tmp_path)
    original = fixture["state"].claim
    def race(request_id):
        claim = original(request_id)
        path = fixture["work_root"] / request_id / "source" / ("README.md" if change == "tracked" else "extra.py")
        path.write_text("Unexpected concurrent edit\n")
        return claim
    monkeypatch.setattr(fixture["state"], "claim", race)
    with pytest.raises(PublicationUnavailable):
        run(repository, fixture)
    assert fixture["state"].read_control()["state"] == "PREPARING"
    assert fixture["state"].history(fixture["request_id"])[1] is None
    candidate = fixture["work_root"] / fixture["request_id"] / "source"
    assert (candidate / worker.HISTORY).read_bytes() == (repository / worker.HISTORY).read_bytes()


def test_fresh_exact_inverse_uses_real_reversal_cli_and_higher_version(repository, tmp_path):
    # Establish real shipped metadata in this isolated source fixture. This does
    # not release an old publication control or simulate production deployment.
    database = AdminTestDynamo()
    _, _, _, forward = signed_review(repository, database, reference="existing-source-review-0001")
    plan = tmp_path / "existing-source-plan.json"
    plan.write_text(json.dumps(forward))
    worker._run([sys.executable, "-B", "-m", "guardoath.catalog_source_release", "--plan", str(plan),
        "--write", "--confirm-registry-version", forward["candidate_registry_version"]], cwd=repository)
    git(repository, "add", worker.HISTORY)
    git(repository, "commit", "-m", "Synthetic already-shipped reviewed source")
    fixture = request(repository, tmp_path, reverse=True)
    result = run(repository, fixture)
    candidate = fixture["work_root"] / fixture["request_id"] / "source"
    history = json.loads((candidate / worker.HISTORY).read_text())
    assert int(result["candidate_registry_version"]) == int(forward["candidate_registry_version"]) + 1
    assert history["releases"][-1]["changes"] == {
        field: {"before": delta["after"], "after": delta["before"]}
        for field, delta in history["releases"][-2]["changes"].items()}
    assert history["releases"][-1]["review_id"] != history["releases"][-2]["review_id"]


def test_extra_python_source_after_compile_is_rejected_before_candidate_boot(repository, tmp_path, monkeypatch):
    fixture = request(repository, tmp_path)
    native_run = worker._run
    accepted = []
    native_inspect = worker._inspect_app
    def unexpected_source(arguments, *, cwd, **kwargs):
        result = native_run(arguments, cwd=cwd, **kwargs)
        if "guardoath.catalog_source_release" in arguments and "--write" in arguments:
            (cwd / "extra_module.py").write_text("# Unreviewed local source\n")
        return result
    def inspect(source, work, name):
        accepted.append(name)
        return native_inspect(source, work, name)
    monkeypatch.setattr(worker, "_run", unexpected_source)
    monkeypatch.setattr(worker, "_inspect_app", inspect)
    with pytest.raises(PublicationUnavailable, match="before app acceptance"):
        run(repository, fixture)
    assert accepted == ["baseline"]
    assert fixture["state"].read_control()["state"] == "PREPARING"
    assert fixture["state"].history(fixture["request_id"])[1] is None


@pytest.mark.parametrize("permission,length", [(0o644, 64), (0o600, 20), (0o600, 130)])
def test_cli_key_reader_rejects_public_or_malformed_secret_files(tmp_path, permission, length):
    path = tmp_path / "key"
    path.write_text("a" * length)
    path.chmod(permission)
    with pytest.raises(PublicationUnavailable, match="private audit"):
        worker._audit_key(path)


def test_cli_key_reader_uses_private_bounded_hex_without_printing_secret(tmp_path):
    path = tmp_path / "key"
    path.write_text("ab" * 32 + "\n")
    path.chmod(0o600)
    if os.name == "posix":
        assert worker._audit_key(path) == bytes.fromhex("ab" * 32)
    else:
        # chmod cannot prove a private Windows DACL. This host is explicitly
        # unsupported by the secret-file CLI, not silently passed or skipped.
        with pytest.raises(PublicationUnavailable, match="private audit"):
            worker._audit_key(path)


def test_cli_key_reader_refuses_nonregular_sources_without_blocking(tmp_path, monkeypatch):
    path = tmp_path / "not-a-key-file"
    if os.name == "posix":
        os.mkfifo(path, 0o600)
        native_open = os.open
        def bounded_open(name, flags, *args, **kwargs):
            # Keep a regression from hanging the test process before it can
            # report that a real private FIFO is not an acceptable key source.
            assert flags & os.O_NONBLOCK
            return native_open(name, flags, *args, **kwargs)
        monkeypatch.setattr(worker.os, "open", bounded_open)
    else:
        path.mkdir()
    with pytest.raises(PublicationUnavailable, match="private audit"):
        worker._audit_key(path)
