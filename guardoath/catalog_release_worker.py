"""Advance one signed catalog release using trusted operator configuration.

The worker publishes only the retained, verified candidate to the fixed main
repository, observes actual complete CI, and invokes the source-controlled
guarded deployment. An acknowledged phase claim permits one external attempt.
An uncertain push/deployment is never retried or adopted as a new authority.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from guardoath.admin_catalog_drafts import AdminCatalogDrafts
from guardoath.admin_catalog_publication_state import (
    DynamoCatalogPublicationState, PublicationConflict, PublicationUnavailable,
    document_hash,
)
from guardoath.catalog_metadata import MAX_HISTORY_BYTES, strict_catalog_json
from guardoath import catalog_publication_worker as preparation
from guardoath.admin_catalog_publication_lifecycle import (
    REQUIRED_WORKFLOWS, validate_deployment_receipt,
)

REPOSITORY = "CollectiverseStudio/guardoath"
BRANCH = "refs/heads/main"
ACCOUNT = "839000214942"
REGION = "us-east-1"
FUNCTION = STACK = "guardoath-site"
DEPLOY_SCRIPT = "deploy/aws/Deploy-Verified.ps1"
REQUIRED_JOBS = REQUIRED_WORKFLOWS
OPERATOR_REQUIREMENTS = (
    "An operator must run this bounded worker; browser requests do not start GitHub or AWS operations.",
    "Both source-controlled catalog writer gates require an accepted compatible baseline and explicit enablement.",
    "Use a private external work directory and a clean current-main checkout of CollectiverseStudio/guardoath.",
    "Git needs authorized fixed-origin push access; GitHub API access must enumerate every exact-candidate job.",
    "Deployment requires Python 3.13, PowerShell, AWS CLI and the existing authorized production profile.",
    "Use the existing audit table/signing key; the standalone private-key-file CLI requires POSIX permissions.",
    "A claimed or uncertain external operation requires inspection and is never automatically repeated.",
)
_MAX_JSON = 8 * 1024 * 1024
_SHA = re.compile(r"^[a-f0-9]{40}$")
_HEX = re.compile(r"^[a-f0-9]{64}$")
_PROFILE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _now(clock, minimum=1):
    value = clock()
    if type(value) not in (int, float) or not minimum <= value <= 253402300799:
        raise PublicationUnavailable("Release evidence clock unavailable")
    return int(value)


def _positive(value):
    return type(value) is int and value > 0


def _created(value):
    try:
        if type(value) is not str or not value.endswith("Z"):
            raise ValueError()
        return datetime.fromisoformat(value[:-1] + "+00:00").timestamp()
    except Exception:
        raise PublicationUnavailable("GitHub run creation evidence unavailable") from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise PublicationUnavailable("Release evidence redirect refused")


class GitHubReleaseReader:
    """Bounded GET-only GitHub transport; no dispatch or caller-selected URL."""

    def __init__(self, token: str | None = None):
        if token is not None and (type(token) is not str or not 1 <= len(token) <= 4096
                                  or any(character.isspace() for character in token)):
            raise PublicationUnavailable("Invalid operator GitHub authentication")
        self._token = token

    def read(self, path: str):
        if (type(path) is not str or not path.startswith("/repos/" + REPOSITORY + "/actions/")
                or ".." in path or "#" in path):
            raise PublicationUnavailable("Untrusted release evidence path")
        url = "https://api.github.com" + path
        headers = {"User-Agent": "GuardOath-Catalog-Release", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self._token is not None:
            headers["Authorization"] = "Bearer " + self._token
        try:
            with build_opener(_NoRedirect()).open(Request(url, headers=headers), timeout=10) as response:
                if response.status != 200 or response.geturl() != url:
                    raise ValueError()
                raw = response.read(_MAX_JSON + 1)
            if len(raw) > _MAX_JSON:
                raise ValueError()
            value = strict_catalog_json(raw)
            if type(value) is not dict:
                raise ValueError()
            return value
        except Exception:
            raise PublicationUnavailable("Actual GitHub release evidence unavailable") from None


def _enumeration(response, field):
    if (type(response) is not dict or type(response.get("total_count")) is not int
            or type(response.get(field)) is not list or response["total_count"] != len(response[field])
            or not 0 <= response["total_count"] <= 100
            or any(type(row) is not dict for row in response[field])):
        raise PublicationUnavailable("Incomplete GitHub release enumeration")
    return response[field]


def collect_ci_evidence(source_sha: str, *, github=None, clock=time.time):
    """Return fresh exact-source evidence, or None while required jobs are pending.

    An older green run cannot replace a newer failed/pending run. A failed run,
    missing matrix member, partial page, wrong repository or wrong SHA refuses
    acceptance. Waiting is read-only; this worker never reruns a workflow.
    """
    if type(source_sha) is not str or not _SHA.fullmatch(source_sha):
        raise PublicationUnavailable("Exact candidate source is required")
    github = github or GitHubReleaseReader()
    response = github.read("/repos/" + REPOSITORY + "/actions/runs?" +
                           urlencode({"head_sha": source_sha, "per_page": 100}))
    observed = _enumeration(response, "workflow_runs")
    if (any(not _positive(row.get("id")) for row in observed)
            or len({row["id"] for row in observed}) != len(observed)):
        raise PublicationUnavailable("Duplicate GitHub workflow evidence")
    runs, seen_jobs, seen_runs = [], set(), set()
    for name, expected_jobs in sorted(REQUIRED_JOBS.items()):
        matching = [row for row in observed if row.get("name") == name]
        if not matching:
            return None
        for row in matching:
            if (not _positive(row.get("id")) or not _positive(row.get("run_attempt"))
                    or row.get("head_sha") != source_sha or row.get("head_branch") != "main"
                    or row.get("event") not in ("push", "workflow_dispatch")
                    or type(row.get("repository")) is not dict
                    or type(row.get("head_repository")) is not dict
                    or row.get("repository", {}).get("full_name") != REPOSITORY
                    or row.get("head_repository", {}).get("full_name") != REPOSITORY):
                raise PublicationUnavailable("Workflow is not bound to the fixed candidate repository")
        run = max(matching, key=lambda row: (_created(row.get("created_at")), row["id"]))
        if run.get("status") != "completed":
            return None
        if run.get("conclusion") != "success":
            raise PublicationConflict("Current exact-candidate CI has not succeeded")
        if run["id"] in seen_runs:
            raise PublicationUnavailable("Duplicated workflow identity")
        seen_runs.add(run["id"])
        jobs = _enumeration(github.read("/repos/" + REPOSITORY + "/actions/runs/" +
            str(run["id"]) + "/jobs?per_page=100"), "jobs")
        names = [job.get("name") for job in jobs]
        if (len(names) != len(expected_jobs) or any(type(name) is not str for name in names)
                or set(names) != set(expected_jobs) or len(set(names)) != len(names)):
            raise PublicationUnavailable("Exact required job membership is incomplete")
        selected_jobs = []
        for job in sorted(jobs, key=lambda item: item["name"]):
            if (not _positive(job.get("id")) or job["id"] in seen_jobs
                    or job.get("run_id") != run["id"] or job.get("head_sha") != source_sha):
                raise PublicationUnavailable("Individual job identity or candidate source mismatch")
            seen_jobs.add(job["id"])
            if job.get("status") != "completed":
                return None
            if job.get("conclusion") != "success":
                raise PublicationConflict("An exact-candidate job has not succeeded")
            selected_jobs.append({"job_id": job["id"], "name": job["name"], "head_sha": source_sha,
                                  "status": "completed", "conclusion": "success"})
        runs.append({"workflow": name, "run_id": run["id"], "run_attempt": run["run_attempt"],
                     "head_sha": source_sha, "status": "completed", "conclusion": "success",
                     "jobs": selected_jobs})
    evidence = {"repository": REPOSITORY, "candidate_source_sha": source_sha, "runs": runs}
    return {"runs": runs, "evidence_sha256": document_hash(evidence), "recorded_at": _now(clock)}


def _work_paths(repository, work_root, request_id):
    repository = preparation._repository_path(Path(repository))
    root = Path(work_root) if work_root is not None else repository.parent / "guardoath-catalog-preparations"
    root = root.absolute()
    if root.resolve() != root or root.is_relative_to(repository):
        raise PublicationUnavailable("Release evidence must remain outside the source checkout")
    work = root / request_id
    source = work / "source"
    if (work.resolve() != work or source.resolve() != source or not work.is_dir()
            or not source.is_dir()):
        raise PublicationUnavailable("Retained prepared source is unavailable")
    return repository, work, preparation._repository_path(source)


def _verify_candidate(source, intent, prepared):
    sha, base = prepared["candidate_source_sha"], intent["base_source_sha"]
    if (preparation._git_text(source, "remote", "get-url", "origin") not in preparation._ORIGINS
            or preparation._git_text(source, "remote", "get-url", "--push", "--all", "origin")
                not in preparation._ORIGINS
            or preparation._git_text(source, "rev-parse", "HEAD") != sha
            or preparation._git(source, "symbolic-ref", "-q", "HEAD", allowed=(0, 1))[0] != 1
            or preparation._git(source, "status", "--porcelain=v1", "--untracked-files=all")[1]
            or preparation._git_text(source, "rev-list", "--parents", "-n", "1", sha) != sha + " " + base):
        raise PublicationConflict("Retained candidate identity, origin or clean parent changed")
    original, candidate = preparation._tree(source, base), preparation._tree(source, sha)
    if (not {DEPLOY_SCRIPT, "deploy/aws/deployment_evidence.py", "tools/verify_live_dashboard.py"} <= set(candidate)
            or set(original) != set(candidate) or original[preparation.HISTORY] == candidate[preparation.HISTORY]
            or original[preparation.HISTORY][0] != candidate[preparation.HISTORY][0]
            or any(original[name] != candidate[name] for name in original if name != preparation.HISTORY)):
        raise PublicationUnavailable("Candidate is not the exact reviewed history-only change")
    preparation._verify_files(source, candidate)
    history = (source / preparation.HISTORY).read_bytes()
    if len(history) > MAX_HISTORY_BYTES or hashlib.sha256(history).hexdigest() != prepared["history_sha256"]:
        raise PublicationUnavailable("Candidate catalog history differs from signed preparation")
    tree_sha = preparation._git_text(source, "rev-parse", sha + "^{tree}")
    if not _SHA.fullmatch(tree_sha):
        raise PublicationUnavailable("Candidate tree identity unavailable")
    return tree_sha


def _current_source(source, expected):
    if preparation._remote_main(source) != expected:
        raise PublicationConflict("Current remote main differs from this release")


def _deployment_evidence(source, operation, value):
    """Run the exact source's bounded private-input evidence helper locally."""
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("ascii")
    if operation not in ("inspect", "path") or len(raw) > 1024 * 1024:
        raise PublicationUnavailable("Invalid bounded deployment evidence input")
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        result = subprocess.run([sys.executable, "-B", str(source / "deploy/aws/deployment_evidence.py"), operation],
            input=raw, cwd=source, env=preparation._environment(), stdout=output, stderr=errors,
            timeout=15, check=False)
        output.seek(0)
        data = output.read(65537)
    if result.returncode != 0 or len(data) > 65536:
        raise PublicationUnavailable("Deployment evidence precondition unavailable")
    return strict_catalog_json(data)


class GuardedDeployment:
    """Invoke only the verified source's real guarded PowerShell deployment."""

    def __init__(self, *, powershell: Path | None = None, profile="collectiverse-prod"):
        if type(profile) is not str or not _PROFILE.fullmatch(profile):
            raise PublicationUnavailable("Invalid trusted operator deployment profile")
        self.profile = profile
        program = str(powershell) if powershell is not None else shutil.which("pwsh") or shutil.which("powershell")
        self.program = Path(program).resolve() if program else None

    def preflight(self, source: Path, receipt_path: Path):
        """Check deterministic local requirements before consuming a claim."""
        try:
            aws, python = shutil.which("aws"), shutil.which("python")
            if (self.program is None or not self.program.is_file()
                    or not os.access(self.program, os.X_OK) or aws is None or python is None
                    or not Path(aws).is_file() or not os.access(aws, os.X_OK)
                    or not (source / DEPLOY_SCRIPT).is_file()
                    or not (source / "deploy/aws/deployment_evidence.py").is_file()):
                raise ValueError()
            version = preparation._run([python, "-c", "import sys; print(sys.version_info.major, sys.version_info.minor)"],
                                       cwd=source, limit=128, timeout=10)[1]
            if version.strip() != b"3 13":
                raise ValueError()
            result = _deployment_evidence(source, "path", {"path": str(receipt_path), "checkout": str(source)})
            if result != {"receipt_destination_valid": True}:
                raise ValueError()
        except Exception:
            raise PublicationUnavailable("Local deployment prerequisites unavailable; no deployment claim was consumed") from None

    def invoke(self, source: Path, receipt_path: Path, invocation_id: str):
        if (self.program is None or not self.program.is_file() or receipt_path.exists()
                or receipt_path.is_symlink() or receipt_path.resolve() != receipt_path
                or receipt_path.is_relative_to(source) or not _HEX.fullmatch(invocation_id)):
            raise PublicationUnavailable("Trusted deployment program or fresh private receipt path unavailable")
        args = [str(self.program), "-NoProfile", "-NonInteractive", "-File", str(source / DEPLOY_SCRIPT),
                "-Deploy", "-Profile", self.profile, "-Region", REGION, "-FunctionName", FUNCTION,
                "-StackName", STACK, "-ReceiptPath", str(receipt_path), "-ReceiptInvocationId", invocation_id]
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("GUARDOATH_", "GIT_", "PYTHON"))}
        environment.update({"POWERSHELL_TELEMETRY_OPTOUT": "1", "AWS_EC2_METADATA_DISABLED": "true",
                            "GIT_TERMINAL_PROMPT": "0"})
        try:
            # Native output may contain private operator/provider configuration.
            # It never becomes an owner response or a signed publication field.
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                result = subprocess.run(args, cwd=source, env=environment, stdin=subprocess.DEVNULL,
                    stdout=output, stderr=errors, timeout=1800, check=False)
            if result.returncode != 0:
                raise ValueError()
            receipt = preparation._json_file(receipt_path, limit=65536)
            validate_deployment_receipt(receipt)
            if (receipt["invocation_id"] != invocation_id
                    or receipt["source_sha"] != preparation._git_text(source, "rev-parse", "HEAD")):
                raise ValueError()
            return receipt
        except Exception:
            raise PublicationUnavailable("Guarded deployment was not acknowledged; inspect retained evidence without retry") from None


class AwsDeploymentReader:
    """Read current production code/configuration; never update or restore it."""

    def __init__(self, *, profile="collectiverse-prod"):
        if type(profile) is not str or not _PROFILE.fullmatch(profile):
            raise PublicationUnavailable("Invalid operator readback profile")
        self.profile = profile

    def _clients(self):
        import boto3
        from botocore.config import Config
        session = boto3.Session(profile_name=self.profile)
        config = Config(connect_timeout=2, read_timeout=5, retries={"total_max_attempts": 1})
        return {name: session.client(name, region_name=REGION, config=config)
                for name in ("sts", "cloudformation", "lambda")}

    @staticmethod
    def _configuration(source, configuration):
        # Use the exact candidate's same deployment evidence implementation.
        # Secrets travel only through stdin; output is its allowlisted digest
        # projection. No provider response or environment value is persisted.
        return _deployment_evidence(source, "inspect", configuration)

    def observe(self, receipt: dict, source: Path):
        try:
            validate_deployment_receipt(receipt)
            clients = self._clients()
            identity = clients["sts"].get_caller_identity()
            stacks = clients["cloudformation"].describe_stacks(StackName=STACK)["Stacks"]
            if len(stacks) != 1 or identity.get("Account") != ACCOUNT:
                raise ValueError()
            stack = stacks[0]
            outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
            if (stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE")
                    or outputs.get("FunctionName") != FUNCTION
                    or outputs.get("DistributionId") != receipt["distribution_id"]):
                raise ValueError()
            config = clients["lambda"].get_function_configuration(FunctionName=FUNCTION)
            current = self._configuration(source, config)
            if (current.get("code_sha256") != receipt["code_sha256"]
                    or current.get("revision_id") != receipt["verified_revision_id"]
                    or current.get("last_modified") != receipt["last_modified"]
                    or current.get("configuration_sha256") != receipt["configuration_sha256"]
                    or current.get("last_update_status") != "Successful"):
                raise ValueError()
            raw = preparation._run([sys.executable, "-B", "-m", "tools.verify_live_dashboard",
                "--url", "https://guardoath.com", "--expected-sha", receipt["source_sha"]], cwd=source)[1]
            parity = strict_catalog_json(raw)
            if (parity.get("release_source_parity_verified") is not True
                    or parity.get("dashboard_ui_matches_local_source") is not True
                    or parity.get("expected_source_sha") != receipt["source_sha"]
                    or parity.get("live_source_sha") != receipt["source_sha"]
                    or parity.get("verified_asset_count") != receipt["verified_asset_count"]
                    or parity.get("expected_asset_count") != receipt["expected_asset_count"]):
                raise ValueError()
            return {"verified_source_sha": receipt["source_sha"], "verified_code_sha256": current["code_sha256"],
                    "verified_revision_id": current["revision_id"], "configuration_sha256": current["configuration_sha256"],
                    "last_modified": current["last_modified"]}
        except Exception:
            raise PublicationUnavailable("Current deployment and source parity could not be independently verified") from None


def _context(request_id, state, drafts, repository, work_root):
    preparation._enabled()
    if (type(request_id) is not str or not _HEX.fullmatch(request_id)
            or not isinstance(state, DynamoCatalogPublicationState)
            or not isinstance(drafts, AdminCatalogDrafts)
            or drafts.ddb is not state.client or drafts.table != state.table_name):
        raise PublicationUnavailable("Bound signed catalog lifecycle state is required")
    history = state.publication_history(request_id)
    if history["intent"] is None or history["prepared"] is None:
        raise PublicationConflict("Catalog source preparation is not complete")
    if history["completion"] is not None:
        return history, None, None, None
    preparation._verified_reviews(history["intent"], drafts)
    repository, work, source = _work_paths(repository, work_root, request_id)
    tree = _verify_candidate(source, history["intent"], history["prepared"])
    if history["remote"] is not None and history["remote"]["candidate_tree_sha"] != tree:
        raise PublicationUnavailable("Published tree differs from retained candidate")
    return history, repository, work, source


def advance_publication(request_id: str, *, state: DynamoCatalogPublicationState,
                        drafts: AdminCatalogDrafts, repository: Path,
                        work_root: Path | None = None, github=None, deployment=None,
                        readback=None, phase="advance", clock=time.time):
    """Advance this exact ID, stopping at pending CI or an uncertain phase.

    Paths, transports and commands are operator-owned configuration. There is no
    browser-selected branch, repository, executable, job list or provider target.
    Each call may observe pending CI again but never repeats a claimed push or
    deployment. History is returned without reexecuting a completed release.
    """
    if phase not in ("advance", "remote", "ci", "deploy", "complete"):
        raise PublicationUnavailable("Unsupported trusted release phase")
    history, repository, work, source = _context(request_id, state, drafts, repository, work_root)
    if history["completion"] is not None:
        return history
    prepared, intent = history["prepared"], history["intent"]
    sha = prepared["candidate_source_sha"]
    fresh_deployment_receipt = None
    fresh_ci_evidence = False
    control = history["control"]
    status = control["state"] if control is not None else "SOURCE_PREPARED"
    attained = {"remote": "remote", "ci": "ci", "deploy": "deployment", "complete": "completion"}
    if phase in attained and history[attained[phase]] is not None:
        return history
    if status in ("PUBLISHING", "DEPLOYING"):
        raise PublicationConflict("Release phase is already claimed; inspect evidence without retry")
    if status == "SOURCE_PREPARED":
        if phase not in ("advance", "remote"):
            raise PublicationConflict("Candidate has not been remotely published")
        preparation._verify_main(repository, intent["base_source_sha"])
        tree = _verify_candidate(source, intent, prepared)
        claim = state.claim_remote(request_id)
        preparation._enabled()
        _current_source(source, intent["base_source_sha"])
        _verify_candidate(source, intent, prepared)
        # Fixed refspec, no force/follow-tags/recursive publication. Exit failure
        # or a lost reply retains PUBLISHING; observing a ref is not retry authority.
        preparation._git(source, "-c", "push.followTags=false", "push", "--porcelain",
            "--no-follow-tags", "--recurse-submodules=no", "origin", sha + ":" + BRANCH)
        _current_source(source, sha)
        state.complete_remote(request_id, claim, {"candidate_tree_sha": tree,
            "remote_head_sha": sha, "recorded_at": _now(clock, prepared["recorded_at"])})
        history = state.publication_history(request_id)
        status = history["control"]["state"]
        if phase == "remote":
            return history
    if status == "REMOTE_PUBLISHED":
        if phase not in ("advance", "ci"):
            raise PublicationConflict("Exact-candidate CI acceptance is not recorded")
        _current_source(source, sha)
        evidence = collect_ci_evidence(sha, github=github, clock=clock)
        _current_source(source, sha)
        if evidence is None:
            return history
        state.record_ci(request_id, evidence)
        history = state.publication_history(request_id)
        fresh_ci_evidence = True
        status = history["control"]["state"]
        if phase == "ci":
            return history
    if status == "CI_VERIFIED":
        if phase not in ("advance", "deploy"):
            return history
        _current_source(source, sha)
        _verify_candidate(source, intent, prepared)
        if not fresh_ci_evidence:
            current_ci = collect_ci_evidence(sha, github=github, clock=clock)
            if current_ci is None or current_ci["evidence_sha256"] != history["ci"]["evidence_sha256"]:
                raise PublicationConflict("Recorded CI is not the current exact-candidate acceptance")
        deployment = deployment or GuardedDeployment()
        if not isinstance(deployment, GuardedDeployment):
            raise PublicationUnavailable("Trusted guarded deployment adapter required")
        try:
            directory = Path(tempfile.mkdtemp(prefix="deployment-", dir=work))
        except OSError:
            raise PublicationUnavailable("Fresh deployment evidence directory unavailable") from None
        receipt_path = directory / "receipt.json"
        deployment.preflight(source, receipt_path)
        preparation._write_private(directory / "prepared-invocation.json", {
            "request_id": request_id, "candidate_source_sha": sha,
            "ci_sha256": document_hash(history["ci"]), "recorded_at": _now(clock)})
        claim = state.claim_deployment(request_id)
        preparation._enabled()
        preparation._write_private(directory / "invocation.json", {
            "request_id": request_id, "claim": claim, "candidate_source_sha": sha,
            "ci_sha256": document_hash(history["ci"]), "recorded_at": _now(clock)})
        _current_source(source, sha)
        _verify_candidate(source, intent, prepared)
        receipt = deployment.invoke(source, receipt_path, claim["invocation_id"])
        state.complete_deployment(request_id, claim, {"receipt": receipt})
        history = state.publication_history(request_id)
        if history["deployment"]["receipt"] != receipt:
            raise PublicationUnavailable("Persisted deployment differs from the acknowledged invocation")
        fresh_deployment_receipt = receipt
        status = history["control"]["state"]
        if phase == "deploy":
            return history
    if status == "DEPLOYED":
        if phase not in ("advance", "complete"):
            return history
        _current_source(source, sha)
        _verify_candidate(source, intent, prepared)
        if fresh_deployment_receipt is not None:
            # The actual guarded script just performed these observations for
            # this acknowledged invocation. Retain its time; do not immediately
            # repeat AWS and every asset read or invent a newer observation.
            receipt = fresh_deployment_receipt
            observed = {"verified_source_sha": receipt["source_sha"],
                "verified_code_sha256": receipt["code_sha256"],
                "verified_revision_id": receipt["verified_revision_id"],
                "configuration_sha256": receipt["configuration_sha256"],
                "last_modified": receipt["last_modified"]}
            observed_at = receipt["observed_at"]
        else:
            # A separately resumed DEPLOYED phase may now be stale. This is a
            # read-only recovery check, never a repeat deployment or restoration.
            readback = readback or AwsDeploymentReader()
            if not isinstance(readback, AwsDeploymentReader):
                raise PublicationUnavailable("Trusted read-only deployment verifier required")
            observed = readback.observe(history["deployment"]["receipt"], source)
            observed_at = _now(clock, history["deployment"]["recorded_at"])
        _current_source(source, sha)
        state.complete_release(request_id, {**observed, "remote_head_sha": sha,
            "recorded_at": observed_at})
        return state.publication_history(request_id)
    if status != "VERIFIED":
        raise PublicationConflict("Unsupported or incomplete release transition")
    return history


def process_next_publication(*, state: DynamoCatalogPublicationState, drafts: AdminCatalogDrafts,
                             repository: Path, work_root: Path | None = None,
                             github=None, deployment=None, readback=None, clock=time.time):
    """Process at most the single current signed request; never scan or loop."""
    preparation._enabled()
    if (not isinstance(state, DynamoCatalogPublicationState) or not isinstance(drafts, AdminCatalogDrafts)
            or drafts.ddb is not state.client or drafts.table != state.table_name):
        raise PublicationUnavailable("Bound signed catalog lifecycle state is required")
    control = state.read_control()
    if control is None:
        return None
    request_id = control["request_id"]
    if control["state"] == "REQUESTED":
        preparation.prepare_publication(request_id, state=state, drafts=drafts,
            repository=repository, work_root=work_root, clock=clock)
    elif control["state"] != "SOURCE_PREPARED":
        raise PublicationConflict("Current request is already claimed; inspect retained evidence")
    return advance_publication(request_id, state=state, drafts=drafts, repository=repository,
        work_root=work_root, github=github, deployment=deployment, readback=readback, clock=clock)


def operator_prerequisites(powershell=None):
    try:
        preparation._enabled()
        enabled = True
    except PublicationUnavailable:
        enabled = False
    return {"worker_mode": "operator_one_request", "automatic_scheduling": False,
        "request_processing_enabled": enabled, "requirements": list(OPERATOR_REQUIREMENTS),
        "python_313": sys.version_info[:2] == (3, 13), "git_available": shutil.which("git") is not None,
        "aws_cli_available": shutil.which("aws") is not None,
        "powershell_available": (Path(powershell).is_file() if powershell is not None else
                                  bool(shutil.which("pwsh") or shutil.which("powershell")))}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Advance one signed GuardOath catalog release with fixed gates",
                                     epilog=" ".join(OPERATOR_REQUIREMENTS))
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--request-id")
    selection.add_argument("--next", action="store_true", dest="next_request",
                           help="Process at most the one current signed global request")
    parser.add_argument("--prerequisites", action="store_true", help="Report local prerequisites without provider or state access")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--phase", choices=("advance", "remote", "ci", "deploy", "complete"), default="advance")
    parser.add_argument("--audit-table")
    parser.add_argument("--audit-key-file", type=Path)
    parser.add_argument("--aws-profile", default="collectiverse-prod")
    parser.add_argument("--powershell", type=Path)
    args = parser.parse_args(argv)
    if args.prerequisites:
        print(json.dumps(operator_prerequisites(args.powershell), sort_keys=True))
        return
    if (not (args.request_id or args.next_request) or not args.audit_table or not args.audit_key_file
            or (args.next_request and args.phase != "advance")):
        parser.error("Choose --request-id or --next and supply the existing --audit-table and --audit-key-file")
    try:
        preparation._enabled()
        import boto3
        from botocore.config import Config
        key = preparation._audit_key(args.audit_key_file)
        session = boto3.Session(profile_name=args.aws_profile)
        client = session.client("dynamodb", region_name=REGION,
            config=Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 1}))
        state = DynamoCatalogPublicationState(client, args.audit_table, key)
        options = dict(state=state, drafts=AdminCatalogDrafts(client, args.audit_table, key),
            repository=args.repository, work_root=args.work_root,
            github=GitHubReleaseReader(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")),
            deployment=GuardedDeployment(powershell=args.powershell, profile=args.aws_profile),
            readback=AwsDeploymentReader(profile=args.aws_profile))
        result = (process_next_publication(**options) if args.next_request else
                  advance_publication(args.request_id, phase=args.phase, **options))
        if result is None:
            print(json.dumps({"status": "idle", "worker_mode": "operator_one_request", "automatic_scheduling": False}))
            return
        control = result["control"]
        print(json.dumps({"request_id": result["intent"]["request_id"], "candidate_source_sha": result["prepared"]["candidate_source_sha"],
            "status": control["state"].lower() if control else "source_prepared",
            "remote_published": result["remote"] is not None, "ci_verified": result["ci"] is not None,
            "deployed": result["deployment"] is not None, "completed": result["completion"] is not None,
            "production_live_acceptance": False, "worker_mode": "operator_one_request",
            "automatic_scheduling": False}, sort_keys=True))
    except Exception:
        parser.exit(2, "Catalog release is pending, conflicted or unavailable; retained evidence requires inspection. No uncertain side effect was retried.\n")


if __name__ == "__main__":
    main()
