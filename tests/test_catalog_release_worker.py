"""Real Git/compiler/signed-state lifecycle; synthetic external service transport."""
from copy import deepcopy
import base64
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import time
from urllib.parse import urlsplit

import pytest

from admin_test_support import AdminTestDynamo
from test_catalog_publication_worker import template, git, request, run, KEY, ORIGIN
from guardoath import catalog_release_worker as release, catalog_publication_worker as preparation, state_contracts
from guardoath.admin_catalog_drafts import AdminCatalogDrafts
from guardoath.admin_catalog_publication_state import (
    DynamoCatalogPublicationState, PublicationConflict, PublicationUnavailable, document_hash,
)
from guardoath.admin_catalog_publication_lifecycle import validate_deployment_receipt
from deploy.aws.deployment_evidence import configuration_evidence
from tools import verify_live_dashboard as live_parity
from fastapi.testclient import TestClient
from guardoath.app import create_app


class GitHubTransport:
    """Actual API response shapes, with no run dispatch or network access."""
    def __init__(self, sha):
        self.calls, self.runs, self.jobs = [], [], {}
        job_id = 1000
        for index, (name, jobs) in enumerate(sorted(release.REQUIRED_JOBS.items()), 1):
            self.runs.append({"id": index, "name": name, "run_attempt": 1,
                "head_sha": sha, "head_branch": "main", "event": "push",
                "created_at": "2026-11-24T12:00:00Z", "status": "completed", "conclusion": "success",
                "repository": {"full_name": release.REPOSITORY},
                "head_repository": {"full_name": release.REPOSITORY}})
            values = []
            for job in jobs:
                job_id += 1
                values.append({"id": job_id, "name": job, "head_sha": sha,
                    "run_id": index, "status": "completed", "conclusion": "success"})
            self.jobs[index] = {"total_count": len(values), "jobs": values}

    def read(self, path):
        self.calls.append(path)
        if "/actions/runs?" in path:
            return {"total_count": len(self.runs), "workflow_runs": deepcopy(self.runs)}
        return deepcopy(self.jobs[int(path.split("/runs/")[1].split("/")[0])])


def deployment_configuration():
    return {"FunctionName": release.FUNCTION,
        "FunctionArn": "arn:aws:lambda:us-east-1:839000214942:function:guardoath-site",
        "Runtime": "python3.13", "Handler": "index.handler", "Role": "arn:aws:iam::839000214942:role/synthetic",
        "Timeout": 15, "MemorySize": 512, "Version": "$LATEST", "Architectures": ["x86_64"],
        "CodeSha256": base64.b64encode(b"c" * 32).decode(), "RevisionId": "verified-revision-123456",
        "LastModified": "2026-11-24T12:00:00.000+0000", "LastUpdateStatus": "Successful",
        "Environment": {"Variables": {"SYNTHETIC_SECRET": "never-in-receipt"}}}


def deployment_receipt(sha, invocation, *, clock=time.time):
    config = deployment_configuration()
    return {"format": "guardoath.catalog-deployment-receipt.v1", "invocation_id": invocation,
        "source_sha": sha, "code_sha256": config["CodeSha256"],
        "backup_code_sha256": base64.b64encode(b"b" * 32).decode(), "backup_source_sha": None,
        "original_revision_id": "original-revision-123456", "upload_revision_id": "upload-revision-123456",
        "verified_revision_id": config["RevisionId"], "last_modified": config["LastModified"],
        "configuration_sha256": configuration_evidence(config)["configuration_sha256"],
        "account_id": release.ACCOUNT, "region": release.REGION, "function_name": release.FUNCTION,
        "stack_name": release.STACK, "distribution_id": "SYNTHETICDIST123", "invalidation_id": "SYNTHETICINVALID123",
        "observed_at": int(clock()), "automatic_rollback_owned": False, "last_update_status": "Successful",
        "release_source_parity_verified": True, "dashboard_ui_matches_local_source": True,
        "expected_source_sha": sha, "live_source_sha": sha, "verified_asset_count": 13,
        "expected_asset_count": 13, "production_live_acceptance": False}


class DeploymentTransport(release.GuardedDeployment):
    def __init__(self):
        self.calls, self.failure = [], None

    def preflight(self, source, receipt_path):
        assert source.is_dir() and receipt_path.parent.is_dir() and not receipt_path.exists()

    def invoke(self, source, receipt_path, invocation_id):
        self.calls.append((source, receipt_path, invocation_id))
        receipt = deployment_receipt(git(source, "rev-parse", "HEAD"), invocation_id)
        preparation._write_private(receipt_path, receipt)
        if self.failure == "lost":
            raise PublicationUnavailable("Synthetic deployment response lost")
        if self.failure == "wrong-source":
            receipt["source_sha"] = "1" * 40
        return receipt


class ReadbackTransport(release.AwsDeploymentReader):
    def __init__(self):
        self.calls, self.failure = [], False

    def observe(self, receipt, source):
        self.calls.append((deepcopy(receipt), source))
        if self.failure:
            raise PublicationUnavailable("Synthetic production readback mismatch")
        return {"verified_source_sha": receipt["source_sha"], "verified_code_sha256": receipt["code_sha256"],
            "verified_revision_id": receipt["verified_revision_id"],
            "configuration_sha256": receipt["configuration_sha256"], "last_modified": receipt["last_modified"]}


@pytest.fixture(scope="module")
def release_template(template, tmp_path_factory):
    root = Path(__file__).resolve().parents[1]
    source = tmp_path_factory.mktemp("catalog-release-template") / "source"
    git(template, "-c", "core.autocrlf=false", "clone", "--quiet", "--no-hardlinks", str(template), str(source))
    git(source, "config", "core.autocrlf", "false")
    git(source, "config", "user.name", "Synthetic release acceptance")
    git(source, "config", "user.email", "synthetic@guardoath.invalid")
    for name in ("deploy", "tools", ".github"):
        shutil.copytree(root / name, source / name,
                        ignore=shutil.ignore_patterns("build", "__pycache__", "*.pyc", "build-blocked-*"))
    git(source, "add", ".")
    git(source, "commit", "-m", "Trusted deployment and source verification fixture")
    return source


@pytest.fixture
def prepared(release_template, tmp_path, monkeypatch):
    source, bare = tmp_path / "repository", tmp_path / "synthetic-remote.git"
    git(release_template, "-c", "core.autocrlf=false", "clone", "--quiet", "--no-hardlinks", str(release_template), str(source))
    git(source, "config", "core.autocrlf", "false")
    git(source, "config", "user.name", "Synthetic release acceptance")
    git(source, "config", "user.email", "synthetic@guardoath.invalid")
    git(source, "init", "--bare", str(bare))
    git(source, "push", str(bare), "main:refs/heads/main")
    git(source, "remote", "set-url", "origin", ORIGIN)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_STATE", True)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE", True)
    remote = {"calls": [], "dry_runs": [], "failure": None, "path": bare}
    original_git = preparation._git

    def external_git(path, *args, **kwargs):
        if "push" not in args:
            return original_git(path, *args, **kwargs)
        assert args[:6] == ("-c", "push.followTags=false", "push", "--porcelain", "--no-follow-tags", "--recurse-submodules=no")
        assert args[-2] == "origin" and args[-1].endswith(":refs/heads/main")
        assert not any(argument.startswith("+") or argument in ("--force", "-f", "--force-with-lease") for argument in args)
        dry_run = "--dry-run" in args
        remote["dry_runs" if dry_run else "calls"].append(args)
        if dry_run and remote["failure"] == "denied":
            raise PublicationUnavailable("Synthetic fixed-origin push authentication denied")
        response = original_git(path, *args[:-2], str(bare), args[-1], **kwargs)
        if not dry_run and remote["failure"] == "lost":
            raise PublicationUnavailable("Synthetic push response lost")
        return response

    monkeypatch.setattr(preparation, "_git", external_git)
    monkeypatch.setattr(preparation, "_remote_main", lambda _path: git(bare, "rev-parse", "refs/heads/main"))
    fixture = request(source, tmp_path)
    result = run(source, fixture)
    fixture.update(repository=source, remote=remote, prepared=result,
        github=GitHubTransport(result["candidate_source_sha"]), deployment=DeploymentTransport(), readback=ReadbackTransport())
    yield fixture
    fixture["database"].close()


def advance(fixture, **changes):
    return release.advance_publication(fixture["request_id"], **{
        "state": fixture["state"], "drafts": fixture["drafts"], "repository": fixture["repository"],
        "work_root": fixture["work_root"], "github": fixture["github"],
        "deployment": fixture["deployment"], "readback": fixture["readback"], **changes})


def test_complete_real_git_signed_lifecycle_and_history_reopen(prepared, monkeypatch):
    base = git(prepared["repository"], "rev-parse", "HEAD")
    result = advance(prepared)
    assert result["control"]["state"] == "VERIFIED"
    assert result["completion"]["production_live_acceptance"] is False
    assert result["completion"]["verified_source_sha"] == prepared["prepared"]["candidate_source_sha"]
    assert len(result["ci"]["runs"]) == 7
    assert len(prepared["github"].calls) == 8
    assert sum(len(run["jobs"]) for run in result["ci"]["runs"]) == 25
    assert result["deployment"]["receipt"]["automatic_rollback_owned"] is False
    assert result["deployment"]["receipt"]["upload_revision_id"] != result["deployment"]["receipt"]["verified_revision_id"]
    assert git(prepared["remote"]["path"], "rev-parse", "main") == result["prepared"]["candidate_source_sha"]
    assert git(prepared["repository"], "rev-parse", "HEAD") == base
    assert len(prepared["remote"]["calls"]) == len(prepared["deployment"].calls) == 1
    assert not prepared["readback"].calls
    assert result["completion"]["recorded_at"] == result["deployment"]["receipt"]["observed_at"]
    assert prepared["state"].request_control()["available"] is True
    reopened = AdminTestDynamo(prepared["database_path"])
    reader = DynamoCatalogPublicationState(reopened, "audit-table", KEY)
    assert reader.publication_history(prepared["request_id"]) == result
    reopened.close()
    monkeypatch.setattr(preparation, "_remote_main", lambda _: pytest.fail("Historical completion must not execute again"))
    assert advance(prepared) == result
    assert "never-in-receipt" not in json.dumps(result)


@pytest.mark.parametrize("fault", ["remote", "tracked-change", "hidden-change", "push-url"])
def test_source_drift_refused_before_remote_claim(prepared, fault):
    source = prepared["work_root"] / prepared["request_id"] / "source"
    if fault == "remote":
        git(source, "push", str(prepared["remote"]["path"]), prepared["prepared"]["candidate_source_sha"] + ":refs/heads/main")
    elif fault == "push-url":
        git(source, "remote", "set-url", "--push", "origin", "https://example.invalid/other.git")
    else:
        target = source / "guardoath/models.py"
        target.write_bytes(target.read_bytes() + b"\n# synthetic tampering\n")
        if fault == "hidden-change":
            git(source, "update-index", "--assume-unchanged", "guardoath/models.py")
    with pytest.raises((PublicationConflict, PublicationUnavailable)):
        advance(prepared)
    assert not prepared["remote"]["calls"] and not prepared["deployment"].calls
    assert prepared["state"].publication_history(prepared["request_id"])["control"] is None


@pytest.mark.parametrize("fault", ["claim", "push"])
def test_unknown_remote_attempt_is_never_reclaimed(prepared, fault):
    if fault == "claim":
        prepared["database"].lose_transaction_response = True
    else:
        prepared["remote"]["failure"] = "lost"
    with pytest.raises(PublicationUnavailable):
        advance(prepared)
    prepared["database"].lose_transaction_response = False
    result = prepared["state"].publication_history(prepared["request_id"])
    assert result["control"]["state"] == "PUBLISHING" and result["remote"] is None
    with pytest.raises(PublicationConflict, match="already claimed"):
        advance(prepared)
    assert len(prepared["remote"]["calls"]) == (fault == "push")
    assert not prepared["deployment"].calls


def test_denied_push_setup_preserves_source_prepared_without_publication_claim(prepared):
    prepared["remote"]["failure"] = "denied"
    before = prepared["state"].publication_history(prepared["request_id"])
    remote_before = git(prepared["remote"]["path"], "rev-parse", "main")
    with pytest.raises(PublicationUnavailable, match="authentication denied"):
        advance(prepared)
    assert prepared["state"].publication_history(prepared["request_id"]) == before
    assert before["control"] is None and prepared["state"].read_control()["state"] == "SOURCE_PREPARED"
    assert git(prepared["remote"]["path"], "rev-parse", "main") == remote_before
    assert len(prepared["remote"]["dry_runs"]) == 1 and not prepared["remote"]["calls"]
    assert not prepared["github"].calls and not prepared["deployment"].calls


def test_waiting_ci_uses_fresh_reads_without_second_push(prepared):
    first = advance(prepared, phase="remote")
    assert first["control"]["state"] == "REMOTE_PUBLISHED"
    prepared["github"].runs[-1]["status"] = "in_progress"
    assert advance(prepared, phase="ci")["ci"] is None
    before = len(prepared["github"].calls)
    prepared["github"].runs[-1]["status"] = "completed"
    result = advance(prepared, phase="ci")
    assert result["control"]["state"] == "CI_VERIFIED"
    assert len(prepared["github"].calls) > before and len(prepared["remote"]["calls"]) == 1
    assert not prepared["deployment"].calls


@pytest.mark.parametrize("fault", ["claim", "lost", "wrong-source"])
def test_unknown_deployment_retains_claim_and_never_reexecutes(prepared, fault):
    advance(prepared, phase="remote")
    advance(prepared, phase="ci")
    if fault == "claim":
        prepared["database"].lose_transaction_response = True
    else:
        prepared["deployment"].failure = fault
    with pytest.raises(PublicationUnavailable):
        advance(prepared, phase="deploy")
    prepared["database"].lose_transaction_response = False
    result = prepared["state"].publication_history(prepared["request_id"])
    assert result["control"]["state"] == "DEPLOYING" and result["deployment"] is None
    with pytest.raises(PublicationConflict, match="already claimed"):
        advance(prepared)
    assert len(prepared["deployment"].calls) == (fault != "claim")
    assert prepared["state"].request_control()["available"] is False


def test_terminal_completion_requires_fresh_successful_observation(prepared):
    advance(prepared, phase="remote")
    advance(prepared, phase="ci")
    advance(prepared, phase="deploy")
    prepared["readback"].failure = True
    with pytest.raises(PublicationUnavailable):
        advance(prepared, phase="complete")
    assert prepared["state"].request_control()["available"] is False
    assert prepared["state"].publication_history(prepared["request_id"])["completion"] is None
    prepared["readback"].failure = False
    assert advance(prepared, phase="complete")["completion"] is not None
    assert len(prepared["deployment"].calls) == 1


def test_next_runner_is_bounded_to_current_request(prepared):
    result = release.process_next_publication(state=prepared["state"], drafts=prepared["drafts"],
        repository=prepared["repository"], work_root=prepared["work_root"],
        github=prepared["github"], deployment=prepared["deployment"], readback=prepared["readback"])
    assert result["completion"] is not None
    assert len(prepared["remote"]["calls"]) == len(prepared["deployment"].calls) == 1


@pytest.mark.parametrize("flag", ["ENABLE_NEW_CATALOG_PUBLICATION_STATE", "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE"])
@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_independent_source_gates_refuse_before_state_or_transport(monkeypatch, tmp_path, flag, value):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_STATE", True)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE", True)
    monkeypatch.setattr(state_contracts, flag, value)
    with pytest.raises(PublicationUnavailable):
        release.advance_publication("a" * 64, state=None, drafts=None, repository=tmp_path)
    with pytest.raises(PublicationUnavailable):
        release.process_next_publication(state=None, drafts=None, repository=tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("fault", ["missing-run", "pending", "failed-run", "newer-failure", "missing-job", "partial-page",
    "wrong-job-sha", "wrong-run-sha", "wrong-run-id", "duplicate-job", "job-skipped", "fork", "wrong-branch", "boolean-id"])
def test_ci_requires_actual_latest_complete_exact_source_evidence(fault):
    github = GitHubTransport("a" * 40)
    first = github.runs[0]
    jobs = github.jobs[first["id"]]
    if fault == "missing-run":
        github.runs.pop()
    elif fault == "pending":
        first["status"] = "in_progress"
    elif fault == "failed-run":
        first["conclusion"] = "failure"
    elif fault == "newer-failure":
        github.runs.append({**deepcopy(first), "id": 99, "created_at": "2026-11-24T12:01:00Z", "conclusion": "failure"})
    elif fault == "missing-job":
        jobs["jobs"].pop(); jobs["total_count"] -= 1
    elif fault == "partial-page":
        jobs["total_count"] += 1
    elif fault == "wrong-job-sha":
        jobs["jobs"][0]["head_sha"] = "b" * 40
    elif fault == "wrong-run-sha":
        first["head_sha"] = "b" * 40
    elif fault == "wrong-run-id":
        jobs["jobs"][0]["run_id"] = 99
    elif fault == "duplicate-job":
        jobs["jobs"].append(deepcopy(jobs["jobs"][0])); jobs["total_count"] += 1
    elif fault == "job-skipped":
        jobs["jobs"][0]["conclusion"] = "skipped"
    elif fault == "fork":
        first["head_repository"]["full_name"] = "someone/guardoath"
    elif fault == "wrong-branch":
        first["head_branch"] = "other"
    elif fault == "boolean-id":
        first["id"] = True
    if fault in ("missing-run", "pending"):
        assert release.collect_ci_evidence("a" * 40, github=github) is None
    else:
        with pytest.raises((PublicationConflict, PublicationUnavailable)):
            release.collect_ci_evidence("a" * 40, github=github)


def test_ci_canonical_receipt_contains_every_job_and_no_untrusted_metadata():
    github = GitHubTransport("a" * 40)
    github.runs[0]["irrelevant_private_field"] = "must-not-be-recorded"
    receipt = release.collect_ci_evidence("a" * 40, github=github)
    assert len(github.calls) == 8 and len(receipt["runs"]) == 7
    assert sum(len(item["jobs"]) for item in receipt["runs"]) == 25
    assert receipt["evidence_sha256"] == document_hash({"repository": release.REPOSITORY,
        "candidate_source_sha": "a" * 40, "runs": receipt["runs"]})
    assert "must-not-be-recorded" not in json.dumps(receipt)


def test_github_reader_uses_fixed_get_and_rejects_redirect(monkeypatch):
    calls = []
    class Response(io.BytesIO):
        status = 200
        def geturl(self):
            return "https://example.invalid/stolen"
    def open_request(request, timeout):
        calls.append((request.full_url, request.get_method(), request.get_header("Authorization"), timeout))
        return Response(b'{"total_count":0,"workflow_runs":[]}')
    monkeypatch.setattr(release, "build_opener", lambda *_: SimpleNamespace(open=open_request))
    reader = release.GitHubReleaseReader("synthetic-private-token")
    with pytest.raises(PublicationUnavailable):
        reader.read("/repos/CollectiverseStudio/guardoath/actions/runs?per_page=100")
    assert calls[0][:2] == ("https://api.github.com/repos/CollectiverseStudio/guardoath/actions/runs?per_page=100", "GET")
    assert calls[0][2] == "Bearer synthetic-private-token"
    with pytest.raises(PublicationUnavailable):
        reader.read("https://example.invalid/actions/runs")
    assert len(calls) == 1


def test_prerequisites_are_local_only_and_state_disabled(monkeypatch, capsys):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_STATE", False)
    release.main(["--prerequisites"])
    result = json.loads(capsys.readouterr().out)
    assert result["automatic_scheduling"] is result["request_processing_enabled"] is False
    assert result["worker_mode"] == "operator_one_request"
    assert result["production_configuration_bootstrap"] is True
    assert any("browser requests" in value for value in result["requirements"])


@pytest.mark.parametrize("fault", [None, "returncode", "wrong-invocation", "preexisting"])
def test_guarded_deployment_invokes_only_fixed_script_and_claim_bound_receipt(tmp_path, monkeypatch, fault, capsys):
    source = Path(__file__).resolve().parents[1]
    sha = git(source, "rev-parse", "HEAD")
    invocation = "d" * 64
    receipt_path = tmp_path / "receipt.json"
    if fault == "preexisting":
        receipt_path.write_text("retain-existing-evidence")
    deployment = release.GuardedDeployment(powershell=Path(sys.executable), profile="synthetic-production")
    original_run = release.subprocess.run
    calls = []
    monkeypatch.setenv("GUARDOATH_AUDIT_SIGNING_KEY", "synthetic-must-not-reach-child")
    monkeypatch.setenv("GH_TOKEN", "synthetic-private-github-token")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "synthetic-operator-authentication")

    def child(arguments, **kwargs):
        if arguments[0] != str(deployment.program):
            return original_run(arguments, **kwargs)
        calls.append((arguments, kwargs))
        assert arguments == [str(deployment.program), "-NoProfile", "-NonInteractive", "-File",
            str(source / release.DEPLOY_SCRIPT), "-Deploy", "-Profile", "synthetic-production",
            "-Region", "us-east-1", "-FunctionName", "guardoath-site", "-StackName", "guardoath-site",
            "-ReceiptPath", str(receipt_path), "-ReceiptInvocationId", invocation]
        assert kwargs["cwd"] == source and not kwargs.get("shell", False)
        assert kwargs["env"]["POWERSHELL_TELEMETRY_OPTOUT"] == "1"
        assert "GUARDOATH_AUDIT_SIGNING_KEY" not in kwargs["env"]
        assert kwargs["env"]["AWS_ACCESS_KEY_ID"] == "synthetic-operator-authentication"
        assert kwargs["env"]["GH_TOKEN"] == "synthetic-private-github-token"
        assert all("synthetic-private-github-token" not in argument for argument in arguments)
        receipt = deployment_receipt(sha, "e" * 64 if fault == "wrong-invocation" else invocation)
        preparation._write_private(receipt_path, receipt)
        return SimpleNamespace(returncode=1 if fault == "returncode" else 0)

    monkeypatch.setattr(release.subprocess, "run", child)
    if fault:
        with pytest.raises(PublicationUnavailable):
            deployment.invoke(source, receipt_path, invocation)
    else:
        result = deployment.invoke(source, receipt_path, invocation)
        assert result["source_sha"] == sha and result["invocation_id"] == invocation
        validate_deployment_receipt(result)
    assert len(calls) == (fault != "preexisting")
    if fault == "preexisting":
        assert receipt_path.read_text() == "retain-existing-evidence"
    assert "synthetic-operator-authentication" not in capsys.readouterr().out


@pytest.mark.parametrize("fault", [None, "account", "distribution", "code", "revision", "modified", "configuration", "in-progress", "parity"])
def test_resumed_deployment_uses_real_config_and_html_asset_verifiers(monkeypatch, fault):
    source = Path(__file__).resolve().parents[1]
    receipt = deployment_receipt("a" * 40, "d" * 64)
    configuration = deployment_configuration()
    account, distribution = release.ACCOUNT, receipt["distribution_id"]
    if fault == "account":
        account = "111111111111"
    elif fault == "distribution":
        distribution = "OTHERDISTRIBUTION"
    elif fault == "code":
        configuration["CodeSha256"] = base64.b64encode(b"x" * 32).decode()
    elif fault == "revision":
        configuration["RevisionId"] = "another-revision-123456"
    elif fault == "modified":
        configuration["LastModified"] = "2026-11-24T12:00:01.000+0000"
    elif fault == "configuration":
        configuration["Environment"]["Variables"]["SYNTHETIC_SECRET"] = "changed-secret"
    elif fault == "in-progress":
        configuration["LastUpdateStatus"] = "InProgress"
    clients = {
        "sts": SimpleNamespace(get_caller_identity=lambda: {"Account": account}),
        "cloudformation": SimpleNamespace(describe_stacks=lambda **kwargs: {"Stacks": [{
            "StackStatus": "UPDATE_COMPLETE", "Outputs": [
                {"OutputKey": "FunctionName", "OutputValue": release.FUNCTION},
                {"OutputKey": "DistributionId", "OutputValue": distribution}]}]}),
        "lambda": SimpleNamespace(get_function_configuration=lambda **kwargs: deepcopy(configuration)),
    }
    reader = release.AwsDeploymentReader()
    monkeypatch.setattr(reader, "_clients", lambda: clients)
    client = TestClient(create_app())
    calls = []

    class Response(io.BytesIO):
        status = 200
        def __init__(self, url, data):
            super().__init__(data)
            self.url = url
        def geturl(self):
            return self.url

    def external_http(request, timeout):
        url = request.full_url
        parsed = urlsplit(url)
        assert parsed.netloc == "guardoath.com"
        if parsed.path == "/healthz":
            data = json.dumps({"status": "ok", "source_sha": "b" * 40 if fault == "parity" else "a" * 40}).encode()
        else:
            response = client.get(parsed.path + ("?" + parsed.query if parsed.query else ""))
            assert response.status_code == 200
            data = response.content
        return Response(url, data)

    def parity_command(arguments, *, cwd, **kwargs):
        calls.append(arguments)
        assert arguments == [sys.executable, "-B", "-m", "tools.verify_live_dashboard",
                             "--url", "https://guardoath.com", "--expected-sha", "a" * 40]
        assert cwd == source
        result = live_parity.verify_release("https://guardoath.com", "a" * 40,
            static_root=source / "guardoath/static", open_url=external_http)
        if not result["release_source_parity_verified"]:
            raise PublicationUnavailable("Synthetic HTTP transport failed real source parity")
        return 0, json.dumps(result).encode()

    monkeypatch.setattr(preparation, "_run", parity_command)
    if fault:
        with pytest.raises(PublicationUnavailable):
            reader.observe(receipt, source)
    else:
        observed = reader.observe(receipt, source)
        assert observed["configuration_sha256"] == receipt["configuration_sha256"]
        assert observed["verified_revision_id"] == receipt["verified_revision_id"]
        assert observed["verified_source_sha"] == "a" * 40
        assert "never-in-receipt" not in json.dumps(observed)
    assert len(calls) == (fault is None or fault == "parity")


def test_next_runner_prepares_only_its_one_requested_intent(prepared, tmp_path):
    request_root = tmp_path / "another-audit-store"
    request_root.mkdir()
    fresh = request(prepared["repository"], request_root)
    class DynamicGitHub:
        current = None
        def read(self, path):
            if self.current is None:
                self.current = GitHubTransport(git(prepared["remote"]["path"], "rev-parse", "main"))
            return self.current.read(path)
    result = release.process_next_publication(state=fresh["state"], drafts=fresh["drafts"],
        repository=prepared["repository"], work_root=fresh["work_root"], github=DynamicGitHub(),
        deployment=prepared["deployment"], readback=prepared["readback"])
    assert result["intent"] == fresh["intent"] and result["completion"] is not None
    assert len(prepared["remote"]["calls"]) == len(prepared["deployment"].calls) == 1
    assert not prepared["readback"].calls
    fresh["database"].close()


def test_next_runner_without_request_is_idle_without_remote_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_STATE", True)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE", True)
    db = AdminTestDynamo()
    state = DynamoCatalogPublicationState(db, "audit-table", KEY)
    drafts = AdminCatalogDrafts(db, "audit-table", KEY)
    assert release.process_next_publication(state=state, drafts=drafts, repository=tmp_path) is None
    db.close()


def test_missing_local_program_does_not_consume_deployment_claim(prepared, tmp_path):
    advance(prepared, phase="remote")
    before = advance(prepared, phase="ci")
    missing = release.GuardedDeployment(powershell=tmp_path / "missing-powershell")
    with pytest.raises(PublicationUnavailable, match="no deployment claim was consumed"):
        advance(prepared, phase="deploy", deployment=missing)
    after = prepared["state"].publication_history(prepared["request_id"])
    assert after == before and after["control"]["state"] == "CI_VERIFIED"
    assert not prepared["deployment"].calls
    completed = advance(prepared)
    assert completed["completion"] is not None
    assert len(prepared["deployment"].calls) == 1


@pytest.mark.parametrize("fault", ["aws", "python", "private-path"])
def test_local_preflight_rejects_unavailable_tools_or_receipt_path_before_claim(tmp_path, monkeypatch, fault):
    source = Path(__file__).resolve().parents[1]
    deployment = release.GuardedDeployment(powershell=Path(sys.executable))
    python = str(Path(sys.executable).resolve())
    calls = []
    monkeypatch.setattr(release.shutil, "which", lambda name: None if fault == "aws" and name == "aws" else python)
    monkeypatch.setattr(preparation, "_run", lambda *_args, **_kwargs: (0, b"3 12\n" if fault == "python" else b"3 13\n"))
    def evidence(_source, operation, value):
        calls.append((operation, value))
        raise PublicationUnavailable("Synthetic unsafe private receipt destination")
    monkeypatch.setattr(release, "_deployment_evidence", evidence)
    with pytest.raises(PublicationUnavailable, match="no deployment claim was consumed"):
        deployment.preflight(source, tmp_path / "receipt.json")
    assert len(calls) == (fault == "private-path")


class ProductionConfigurationTransport:
    """Only the fixed read-only AWS SDK boundary is synthetic."""

    def __init__(self, database=None):
        self.calls = []
        self.account = release.ACCOUNT
        self.failure = False
        self.database = database
        self.configuration = deployment_configuration()
        self.configuration["State"] = "Active"
        self.configuration["Environment"]["Variables"].update({
            "GUARDOATH_ADMIN_ENABLED": "1",
            "GUARDOATH_ADMIN_AUDIT_TABLE": "existing-owner-audit",
            "GUARDOATH_TENANT_TABLE": "existing-tenants",
            "GUARDOATH_ADMIN_CURSOR_HMAC_KEY": "ab" * 32,
        })

    def client(self, name, *, region_name, config):
        assert region_name == "us-east-1"
        assert config.retries == {"total_max_attempts": 1}
        assert config.connect_timeout == 2 and config.read_timeout == 3
        self.calls.append(("client", name))
        if name == "sts":
            return SimpleNamespace(get_caller_identity=self.identity)
        if name == "lambda":
            return SimpleNamespace(get_function_configuration=self.function)
        assert name == "dynamodb" and self.database is not None
        return self.database

    def identity(self):
        self.calls.append(("identity",))
        return {"Account": self.account}

    def function(self, *, FunctionName):
        assert FunctionName == "guardoath-site"
        self.calls.append(("configuration", FunctionName))
        if self.failure:
            raise RuntimeError("Synthetic provider error containing never-in-receipt")
        return deepcopy(self.configuration)


def test_production_bootstrap_reuses_exact_existing_audit_derivation_without_persisting_secrets(capsys, caplog):
    provider = ProductionConfigurationTransport()
    before = dict(os.environ)
    table, key = release._production_audit_binding(provider)
    assert table == "existing-owner-audit"
    assert key == hmac.new(bytes.fromhex("ab" * 32), b"guardoath-admin-audit-v1", hashlib.sha256).digest()
    assert provider.calls == [("client", "sts"), ("identity",), ("client", "lambda"),
                              ("configuration", "guardoath-site")]
    assert dict(os.environ) == before
    assert capsys.readouterr().out == caplog.text == ""


@pytest.mark.parametrize("fault", ["account", "function", "arn", "in-progress", "inactive", "environment-error",
                                  "admin-disabled", "same-table", "missing-table", "bad-table", "bad-key", "provider"])
def test_production_bootstrap_refuses_wrong_or_incomplete_binding_without_state_access(fault, capsys, caplog):
    provider = ProductionConfigurationTransport()
    config = provider.configuration
    variables = config["Environment"]["Variables"]
    if fault == "account":
        provider.account = "111111111111"
    elif fault == "function":
        config["FunctionName"] = "other-function"
    elif fault == "arn":
        config["FunctionArn"] = "arn:aws:lambda:us-west-2:111111111111:function:guardoath-site"
    elif fault == "in-progress":
        config["LastUpdateStatus"] = "InProgress"
    elif fault == "inactive":
        config["State"] = "Inactive"
    elif fault == "environment-error":
        config["Environment"]["Error"] = {"Message": "never-in-receipt"}
    elif fault == "admin-disabled":
        variables["GUARDOATH_ADMIN_ENABLED"] = "0"
    elif fault == "same-table":
        variables["GUARDOATH_ADMIN_AUDIT_TABLE"] = variables["GUARDOATH_TENANT_TABLE"]
    elif fault == "missing-table":
        del variables["GUARDOATH_TENANT_TABLE"]
    elif fault == "bad-table":
        variables["GUARDOATH_ADMIN_AUDIT_TABLE"] = "untrusted/table"
    elif fault == "bad-key":
        variables["GUARDOATH_ADMIN_CURSOR_HMAC_KEY"] = "a" * 65
    else:
        provider.failure = True
    with pytest.raises(PublicationUnavailable, match="^Existing production audit configuration unavailable$"):
        release._production_audit_binding(provider)
    assert not any(call == ("client", "dynamodb") for call in provider.calls)
    assert len(provider.calls) == (2 if fault == "account" else 4)
    assert capsys.readouterr().out == caplog.text == ""


def test_production_bootstrap_is_source_gated_before_provider_reads(monkeypatch):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE", False)
    provider = ProductionConfigurationTransport()
    with pytest.raises(PublicationUnavailable, match="not enabled"):
        release._production_audit_binding(provider)
    assert not provider.calls


def test_production_config_cli_next_uses_real_signed_state_reader_once(tmp_path, monkeypatch, capsys):
    import boto3
    db = AdminTestDynamo()
    provider = ProductionConfigurationTransport(db)
    selected_profiles = []
    def session(*, profile_name):
        selected_profiles.append(profile_name)
        return provider
    monkeypatch.setattr(boto3, "Session", session)
    # Executable discovery is covered by the actual preflight tests; this
    # transport-only test retains the real bootstrap, state and bounded runner.
    monkeypatch.setattr(release, "_operator_preflight", lambda repo, root, deploy: (repo, root))
    release.main(["--next", "--production-config", "--aws-profile", "synthetic-existing-profile",
                  "--repository", str(tmp_path), "--work-root", str(tmp_path / "private-evidence")])
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "idle", "worker_mode": "operator_one_request", "automatic_scheduling": False}
    assert selected_profiles == ["synthetic-existing-profile"]
    assert provider.calls[-1] == ("client", "dynamodb") and len(provider.calls) == 5
    db.close()


@pytest.mark.parametrize("manual", [["--audit-table", "existing-table"], ["--audit-key-file", "private-key"]])
def test_production_config_cli_rejects_mixed_secret_sources_before_provider_access(monkeypatch, manual, capsys):
    import boto3
    monkeypatch.setattr(boto3, "Session", lambda **_: pytest.fail("Invalid CLI must not create provider session"))
    with pytest.raises(SystemExit) as error:
        release.main(["--next", "--production-config", *manual])
    assert error.value.code == 2
    assert "either --production-config or both" in capsys.readouterr().err


def test_production_config_cli_checks_local_setup_before_reading_secrets_or_claiming(tmp_path, monkeypatch, capsys):
    import boto3
    monkeypatch.setattr(boto3, "Session", lambda **_: pytest.fail("Local failure must precede provider access"))
    root = Path(__file__).resolve().parents[1]
    with pytest.raises(SystemExit) as error:
        release.main(["--next", "--production-config", "--repository", str(root),
                      "--work-root", str(tmp_path / "private-work"), "--powershell", str(tmp_path / "missing-pwsh")])
    assert error.value.code == 2
    assert "No publication claim was consumed" in capsys.readouterr().err


@pytest.mark.parametrize("token", ["synthetic-private token", "synthetic-private\n", "synthetic-private\x01", "x" * 4097])
def test_operator_github_credentials_are_validated_without_echoing_values(token):
    with pytest.raises(PublicationUnavailable, match="^Invalid operator GitHub authentication$"):
        release.GitHubReleaseReader(token)
