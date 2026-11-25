"""Prepare one signed catalog request outside the request-serving Lambda.

This worker creates an inspected local candidate commit in a retained detached
worktree. It never moves main, pushes, dispatches CI or deploys. Only the
acknowledged global-control claim permits the source write; interrupted or
uncertain attempts stay occupied for inspection, without automatic retry.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

from guardoath.admin_catalog_drafts import AdminCatalogDrafts
from guardoath.admin_catalog_publication_state import (
    DynamoCatalogPublicationState, PublicationConflict, PublicationUnavailable,
    document_hash,
)
from guardoath.catalog_metadata import MAX_HISTORY_BYTES, strict_catalog_json

HISTORY = "guardoath/catalog_metadata_releases.json"
_ORIGINS = frozenset({"https://github.com/CollectiverseStudio/guardoath.git",
                      "git@github.com:CollectiverseStudio/guardoath.git"})
_SHA = re.compile(r"^[a-f0-9]{40}$")
_REQUEST = re.compile(r"^[a-f0-9]{64}$")
_MAX_REPORT = 16 * 1024 * 1024
_ACCEPTANCE = "tests/catalog_source_roundtrip_runner.py"

# Fixed trusted program, never assembled from a request. Every import must come
# from the selected exact source tree. The parent independently verified the
# signed records; this process reproduces their complete source plan afresh.
_SOURCE_PROBE = r'''
import json
from pathlib import Path
import sys
import hashlib
import guardoath
from guardoath import admin_catalog, admin_catalog_manifest, admin_catalog_publication
from guardoath import catalog_metadata, catalog_source_release, control_registry, rule_catalog
root = Path.cwd().resolve()
for module in (guardoath, admin_catalog, admin_catalog_manifest, admin_catalog_publication,
               catalog_metadata, catalog_source_release, control_registry, rule_catalog):
    if not Path(module.__file__).resolve().is_relative_to(root / "guardoath"):
        raise ValueError("Source import provenance mismatch")
with Path(sys.argv[1]).open("rb") as source:
    raw = source.read(65537)
if len(raw) > 65536:
    raise ValueError("Oversized verified review")
evidence = catalog_metadata.strict_catalog_json(raw)
manifest = admin_catalog_manifest.reviewed_metadata_manifest(admin_catalog.published_catalog,
    draft=evidence["draft"], review=evidence["review"])
plan = admin_catalog_publication.source_plan_from_manifest(manifest)
with catalog_metadata.FILE.open("rb") as source:
    history_bytes = source.read(catalog_metadata.MAX_HISTORY_BYTES + 1)
if len(history_bytes) > catalog_metadata.MAX_HISTORY_BYTES:
    raise ValueError("Oversized source history")
history = catalog_metadata.validate_release_history(catalog_metadata.strict_catalog_json(history_bytes))
prepare = (catalog_source_release.candidate_rollback_release
           if evidence["operation"] == "reverse_latest" else catalog_source_release.candidate_release)
candidate = prepare(plan, admin_catalog.published_catalog, history)
print(json.dumps({"plan": plan, "candidate_history": candidate,
    "history_sha256": hashlib.sha256(history_bytes).hexdigest()},
    ensure_ascii=True, sort_keys=True, allow_nan=False))
'''


def _enabled() -> None:
    try:
        from guardoath.state_contracts import new_catalog_publication_state_enabled
        if new_catalog_publication_state_enabled() is not True:
            raise ValueError()
    except Exception:
        raise PublicationUnavailable("Catalog source preparation is not enabled") from None


def _environment() -> dict[str, str]:
    # Child acceptance/source programs need no cloud credentials or live runtime
    # settings. Git authentication remains in the explicitly configured trusted
    # operator's credential helper; no secret is copied into argv or output.
    result = {key: value for key, value in os.environ.items()
              if not key.startswith(("GUARDOATH_", "AWS_", "GIT_", "PYTHON"))}
    result.update({"PYTHONDONTWRITEBYTECODE": "1", "AWS_EC2_METADATA_DISABLED": "true",
                   "GIT_TERMINAL_PROMPT": "0"})
    return result


def _run(arguments: list[str], *, cwd: Path, limit: int = _MAX_REPORT,
         timeout: int = 90, allowed: tuple[int, ...] = (0,)) -> tuple[int, bytes]:
    try:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            process = subprocess.run(arguments, cwd=cwd, env=_environment(),
                stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                timeout=timeout, check=False)
            if process.returncode not in allowed:
                raise ValueError()
            output.seek(0)
            data = output.read(limit + 1)
            if len(data) > limit:
                raise ValueError()
            return process.returncode, data
    except Exception:
        # Native process errors may contain provider details or local settings.
        # Keep them out of both an owner response and the worker's normal stdout.
        raise PublicationUnavailable("Catalog source command could not be verified") from None


def _git(repository: Path, *arguments: str, allowed=(0,), limit=_MAX_REPORT):
    executable = shutil.which("git")
    if executable is None:
        raise PublicationUnavailable("Git is unavailable")
    return _run([executable, "-c", "core.hooksPath=" + os.devnull,
                 "-c", "core.fsmonitor=false", "-c", "core.longpaths=true",
                 "-c", "commit.gpgsign=false",
                 "-C", str(repository), *arguments], cwd=repository,
                allowed=allowed, limit=limit)


def _git_text(repository: Path, *arguments: str) -> str:
    try:
        return _git(repository, *arguments, limit=65536)[1].decode("utf-8").strip()
    except UnicodeError:
        raise PublicationUnavailable("Invalid source metadata") from None


def _remote_main(repository: Path) -> str:
    """The sole remote network boundary; no fetch, push or credential disclosure."""
    value = _git_text(repository, "ls-remote", "--exit-code", "origin", "refs/heads/main")
    pieces = value.split("\t")
    if len(pieces) != 2 or not _SHA.fullmatch(pieces[0]) or pieces[1] != "refs/heads/main":
        raise PublicationUnavailable("Authoritative main source unavailable")
    return pieces[0]


def _repository_path(path: Path) -> Path:
    supplied = Path(path).absolute()
    resolved = supplied.resolve()
    if supplied != resolved or not resolved.is_dir():
        raise PublicationUnavailable("An explicit nonsymlink source checkout is required")
    if Path(_git_text(resolved, "rev-parse", "--show-toplevel")) != resolved:
        raise PublicationUnavailable("Source checkout root mismatch")
    return resolved


def _verify_main(repository: Path, source_sha: str) -> None:
    if (_git_text(repository, "symbolic-ref", "--short", "HEAD") != "main"
            or _git_text(repository, "rev-parse", "HEAD") != source_sha
            or _git_text(repository, "remote", "get-url", "origin") not in _ORIGINS
            or _git(repository, "status", "--porcelain=v1", "--untracked-files=all")[1]):
        raise PublicationConflict("A clean exact-base GuardOath main checkout is required")
    if _remote_main(repository) != source_sha:
        raise PublicationConflict("Authoritative main changed; a new owner request is required")


def _tree(repository: Path, source_sha: str) -> dict[str, tuple[str, str]]:
    raw = _git(repository, "ls-tree", "-rz", "--full-tree", source_sha)[1]
    result = {}
    try:
        for entry in raw.split(b"\0"):
            if not entry:
                continue
            metadata, path_bytes = entry.split(b"\t", 1)
            mode, kind, digest = metadata.decode("ascii").split(" ")
            name = path_bytes.decode("utf-8")
            path = Path(name)
            if (mode not in ("100644", "100755") or kind != "blob"
                    or not _SHA.fullmatch(digest) or path.is_absolute()
                    or ".." in path.parts or name in result):
                raise ValueError()
            result[name] = (mode, digest)
        if not result or HISTORY not in result or _ACCEPTANCE not in result:
            raise ValueError()
        return result
    except Exception:
        raise PublicationUnavailable("Exact source tree could not be verified") from None


def _verify_files(repository: Path, tree: dict, *, expected_history: bytes | None = None):
    """Compare actual bytes, even if Git index flags would hide a modification."""
    total = 0
    for name, (_mode, expected) in tree.items():
        path = repository / name
        try:
            if path.is_symlink() or not path.is_file() or path.resolve() != path:
                raise ValueError()
            length = path.stat().st_size
            total += length
            if length > 32 * 1024 * 1024 or total > 512 * 1024 * 1024:
                raise ValueError()
            if name == HISTORY and expected_history is not None:
                with path.open("rb") as source:
                    if source.read(len(expected_history) + 1) != expected_history:
                        raise ValueError()
                continue
            # SHA-1 is this repository's Git object format, not the publication
            # authorization algorithm (signed state uses HMAC/SHA-256).
            digest = hashlib.sha1(b"blob " + str(length).encode("ascii") + b"\0",
                                  usedforsecurity=False)
            read = 0
            with path.open("rb") as source:
                while chunk := source.read(65536):
                    read += len(chunk)
                    if read > length:
                        raise ValueError()
                    digest.update(chunk)
            if read != length or digest.hexdigest() != expected:
                raise ValueError()
        except Exception:
            raise PublicationUnavailable("Source files differ from their exact Git tree") from None


def _json_file(path: Path, limit: int = _MAX_REPORT):
    try:
        if path.is_symlink():
            raise ValueError()
        with path.open("rb") as source:
            raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ValueError()
        return strict_catalog_json(raw)
    except Exception:
        raise PublicationUnavailable("Catalog preparation evidence unavailable") from None


def _write_private(path: Path, value: dict) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False).encode("ascii")
        if len(encoded) > 65536:
            raise ValueError()
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        raise PublicationUnavailable("Private source evidence could not be retained") from None


def _verified_reviews(intent: dict, drafts: AdminCatalogDrafts) -> tuple[dict, dict]:
    try:
        review = drafts.read_review(intent["actor_sub"], intent["review_id"])
        draft = drafts.read(intent["actor_sub"], intent["draft_id"])
        if (review is None or draft is None
                or document_hash(review) != intent["review_sha256"]
                or document_hash(draft) != intent["draft_sha256"]
                or review["draft_id"] != draft["draft_id"]):
            raise ValueError()
        return draft, review
    except Exception:
        raise PublicationUnavailable("Persisted review and request binding unavailable") from None


def _inspect_app(source: Path, work: Path, name: str) -> dict:
    report = work / (name + ".json")
    args = [sys.executable, "-B", str(source / _ACCEPTANCE),
            "--database", str(work / "acceptance.sqlite3"), "--report", str(report)]
    if name == "baseline":
        args.append("--initialize-store")
    _run(args, cwd=source)
    return _json_file(report)


def _verify_app_transition(baseline: dict, candidate: dict, intent: dict, history: dict) -> None:
    try:
        if (candidate["registry_version"] != intent["candidate_registry_version"]
                or baseline["registry_version"] != intent["registry_version"]
                or candidate["history"] != history
                or candidate["tenant_rows"] != baseline["tenant_rows"]
                or candidate["outcomes"] != baseline["outcomes"]
                or len(candidate["outcomes"]) != 12):
            raise ValueError()
        changed = intent["plan"]["changed_metadata"]
        rules = deepcopy(candidate["rules"])
        for field, delta in changed.items():
            if (rules[intent["target"]["rule_id"]][field] != delta["after"]
                    or baseline["rules"][intent["target"]["rule_id"]][field] != delta["before"]):
                raise ValueError()
            rules[intent["target"]["rule_id"]][field] = delta["before"]
        if rules != baseline["rules"]:
            raise ValueError()
        for name in ("admin", "customer"):
            normalized = deepcopy(candidate[name])
            normalized["registry_version"] = baseline[name]["registry_version"]
            groups = (normalized["categories"] if name == "admin" else normalized["domains"])
            rules = [rule for category in groups
                     for check in category["checks" if name == "admin" else "controls"]
                     for rule in check["rules"] if rule["rule_id"] == intent["target"]["rule_id"]]
            if len(rules) != 1:
                raise ValueError()
            for field, delta in changed.items():
                if rules[0][field] != delta["after"]:
                    raise ValueError()
                rules[0][field] = delta["before"]
            if normalized != baseline[name]:
                raise ValueError()
    except Exception:
        raise PublicationUnavailable("Candidate app, Engine or tenant parity failed") from None


def prepare_publication(request_id: str, *, state: DynamoCatalogPublicationState,
                        drafts: AdminCatalogDrafts, repository: Path,
                        work_root: Path | None = None, clock=time.time) -> dict:
    """One acknowledged worker attempt, with local evidence retained on failure.

    Operator inputs choose the checkout and private work directory. The request
    supplies only verified catalog fields, never executable arguments or paths.
    An already recorded result is historical evidence, not another worker claim.
    """
    _enabled()
    if (type(request_id) is not str or not _REQUEST.fullmatch(request_id)
            or not isinstance(state, DynamoCatalogPublicationState)
            or not isinstance(drafts, AdminCatalogDrafts)
            or drafts.ddb is not state.client or drafts.table != state.table_name):
        raise PublicationUnavailable("Bound signed catalog state is required")
    intent, prepared = state.history(request_id)
    if intent is None:
        raise PublicationConflict("Catalog request not found")
    if prepared is not None:
        return prepared
    control = state.read_control()
    if (control is None or control["request_id"] != request_id
            or control["intent_hash"] != document_hash(intent) or control["state"] != "REQUESTED"):
        raise PublicationConflict("Catalog preparation already claimed; inspect its evidence")
    draft, review = _verified_reviews(intent, drafts)
    repository = _repository_path(repository)
    _verify_main(repository, intent["base_source_sha"])
    base_tree = _tree(repository, intent["base_source_sha"])
    _verify_files(repository, base_tree)
    work_root = Path(work_root) if work_root is not None else repository.parent / "guardoath-catalog-preparations"
    work_root = work_root.absolute()
    if work_root.resolve() != work_root or work_root.is_relative_to(repository):
        raise PublicationUnavailable("Private work root must be outside the source checkout")
    try:
        work_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        work = work_root / request_id
        work.mkdir(mode=0o700)
    except OSError:
        raise PublicationConflict("A preparation workspace already exists or is unavailable") from None
    source = work / "source"
    _git(repository, "worktree", "add", "--detach", str(source), intent["base_source_sha"])
    _verify_files(source, base_tree)
    if _git(source, "status", "--porcelain=v1", "--untracked-files=all")[1]:
        raise PublicationUnavailable("Fresh source worktree contains unexpected files")
    evidence = work / "review.json"
    _write_private(evidence, {"draft": draft, "review": review, "operation": intent["operation"]})
    probe = _run([sys.executable, "-B", "-c", _SOURCE_PROBE, str(evidence)], cwd=source)[1]
    try:
        projection = strict_catalog_json(probe)
        if (set(projection) != {"plan", "candidate_history", "history_sha256"}
                or projection["plan"] != intent["plan"]
                or document_hash(projection["plan"]) != intent["plan_sha256"]):
            raise ValueError()
        next_text = json.dumps(projection["candidate_history"], indent=2,
                               ensure_ascii=True, sort_keys=True, allow_nan=False) + "\n"
        # The existing real CLI writes through text-mode NamedTemporaryFile.
        # Match its native newline bytes exactly, including Windows CRLF; do
        # not normalize an already written candidate or relax source parity.
        next_bytes = next_text.replace("\n", os.linesep).encode("ascii")
        if len(next_bytes) > MAX_HISTORY_BYTES:
            raise ValueError()
        with (source / HISTORY).open("rb") as current:
            if hashlib.sha256(current.read(MAX_HISTORY_BYTES + 1)).hexdigest() != projection["history_sha256"]:
                raise ValueError()
    except Exception:
        raise PublicationUnavailable("Source projection differs from signed publication intent") from None
    plan_file = work / "plan.json"
    _write_private(plan_file, intent["plan"])
    baseline = _inspect_app(source, work, "baseline")
    _verify_files(source, base_tree)
    _verify_main(repository, intent["base_source_sha"])
    _verified_reviews(intent, drafts)
    # A read of PREPARING cannot substitute for this successful exact CAS reply.
    claim = state.claim(request_id)
    _enabled()
    _verify_files(source, base_tree)
    if _git(source, "status", "--porcelain=v1", "--untracked-files=all")[1]:
        raise PublicationUnavailable("Preparation source changed while claiming")
    arguments = [sys.executable, "-B", "-m", "guardoath.catalog_source_release",
                 "--plan", str(plan_file), "--write", "--confirm-registry-version",
                 intent["candidate_registry_version"]]
    if intent["operation"] == "reverse_latest":
        arguments.append("--revert-last")
    _run(arguments, cwd=source)
    _verify_files(source, base_tree, expected_history=next_bytes)
    if _git_text(source, "diff", "--no-ext-diff", "--name-only", "HEAD", "--") != HISTORY:
        raise PublicationUnavailable("Candidate includes an unexpected source change")
    if _git(source, "status", "--porcelain=v1", "--untracked-files=all", "-z")[1] != (
            " M " + HISTORY + "\0").encode("utf-8"):
        raise PublicationUnavailable("Candidate includes unexpected files before app acceptance")
    candidate = _inspect_app(source, work, "candidate")
    _verify_app_transition(baseline, candidate, intent, projection["candidate_history"])
    _verify_files(source, base_tree, expected_history=next_bytes)
    _verify_main(repository, intent["base_source_sha"])
    _enabled()
    if _git(source, "status", "--porcelain=v1", "--untracked-files=all", "-z")[1] != (
            " M " + HISTORY + "\0").encode("utf-8"):
        raise PublicationUnavailable("Candidate includes unexpected staged or untracked files")
    _git(source, "add", "--", HISTORY)
    _git(source, "-c", "user.name=GuardOath catalog worker",
         "-c", "user.email=catalog-worker@guardoath.invalid", "commit", "-m",
         "Prepare reviewed catalog metadata " + request_id)
    commit = _git_text(source, "rev-parse", "HEAD")
    if not _SHA.fullmatch(commit):
        raise PublicationUnavailable("Candidate source identity unavailable")
    candidate_tree = _tree(source, commit)
    expected_tree = dict(base_tree)
    expected_tree[HISTORY] = (base_tree[HISTORY][0], hashlib.sha1(
        b"blob " + str(len(next_bytes)).encode("ascii") + b"\0" + next_bytes,
        usedforsecurity=False).hexdigest())
    detached, _ = _git(source, "symbolic-ref", "-q", "HEAD", allowed=(0, 1))
    if (detached != 1 or candidate_tree != expected_tree
            or _git_text(source, "rev-list", "--parents", "-n", "1", commit) !=
               commit + " " + intent["base_source_sha"]
            or _git(source, "status", "--porcelain=v1", "--untracked-files=all")[1]):
        raise PublicationUnavailable("Candidate commit parent or source tree differs from intent")
    _verify_files(source, candidate_tree)
    now = clock()
    if type(now) not in (int, float) or not intent["created_at"] <= now <= 253402300799:
        raise PublicationUnavailable("Preparation evidence clock unavailable")
    return state.complete_preparation(request_id, claim, {
        "base_source_sha": intent["base_source_sha"], "candidate_source_sha": commit,
        "history_sha256": hashlib.sha256(next_bytes).hexdigest(),
        "candidate_metadata_sha256": intent["candidate_metadata_sha256"],
        "candidate_registry_version": intent["candidate_registry_version"], "recorded_at": int(now),
    })


def _audit_key(path: Path) -> bytes:
    try:
        # chmod's Unix mode bits do not verify a Windows DACL. The standalone
        # secret-file CLI is deliberately unsupported there; callers may still
        # use the portable worker with separately verified injected state.
        if (os.name != "posix" or not hasattr(os, "O_NOFOLLOW")
                or not hasattr(os, "O_NONBLOCK")):
            raise ValueError()
        # Open without following links or blocking on a replaced FIFO, then
        # inspect that exact descriptor instead of racing a preliminary stat.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise ValueError()
            raw = source.read(130)
        if len(raw) > 129:
            raise ValueError()
        value = raw.strip()
        if not re.fullmatch(rb"(?:[a-fA-F0-9]{2}){32,64}", value):
            raise ValueError()
        return bytes.fromhex(value.decode("ascii"))
    except Exception:
        raise PublicationUnavailable("A private audit signing-key file is required") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare a signed GuardOath catalog request; never push or deploy")
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--audit-table", required=True)
    parser.add_argument("--audit-key-file", type=Path, required=True,
                        help="Private hexadecimal audit-key file; standalone key-file CLI requires POSIX permissions")
    parser.add_argument("--region", required=True)
    args = parser.parse_args(argv)
    try:
        _enabled()
        import boto3
        from botocore.config import Config
        key = _audit_key(args.audit_key_file)
        client = boto3.client("dynamodb", region_name=args.region,
            config=Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 1}))
        state = DynamoCatalogPublicationState(client, args.audit_table, key)
        result = prepare_publication(args.request_id, state=state,
            drafts=AdminCatalogDrafts(client, args.audit_table, key), repository=args.repository,
            work_root=args.work_root)
    except Exception:
        parser.exit(2, "Catalog preparation unavailable or conflicted; retained evidence requires inspection. No remote publication or deployment is established.\n")
    print(json.dumps({key: result[key] for key in ("request_id", "status", "candidate_source_sha",
        "candidate_registry_version", "remote_published", "ci_verified", "deployed")}, sort_keys=True))


if __name__ == "__main__":
    main()
