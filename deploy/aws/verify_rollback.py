"""Inspect source and state compatibility without extracting/executing Lambda ZIPs.

The backup digest is bound to AWS get-function's CodeSha256. A source commit
alone cannot prove that a previously deployed image understands new records.
This tool never changes a tenant, archive, provider or deployment setting.
"""
import argparse
import base64
import hashlib
import hmac
import json
from pathlib import Path
import re
import zipfile

MANIFEST = "guardoath/_release_source.json"
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_MANIFEST_BYTES = 8192


def _unique(pairs):
    result = {}
    for key,value in pairs:
        if key in result: raise ValueError("Duplicate release metadata key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("Nonfinite release metadata value")


def _contracts(value):
    if (not isinstance(value,list) or len(value) > 32
            or any(not isinstance(name,str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}",name) for name in value)
            or len(value) != len(set(value))):
        raise ValueError("Invalid state contract list")
    return frozenset(value)


def archive_digest(path):
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_ARCHIVE_BYTES:
        raise ValueError("Invalid Lambda archive size")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda:source.read(1024 * 1024),b""):
            digest.update(chunk)
    return base64.b64encode(digest.digest()).decode("ascii")


def assert_backup_digest(backup, expected_code_sha256):
    try:
        decoded = base64.b64decode(expected_code_sha256,validate=True)
    except (TypeError,ValueError) as exc:
        raise ValueError("Invalid deployed CodeSha256") from exc
    if (len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != expected_code_sha256
            or not hmac.compare_digest(archive_digest(backup),expected_code_sha256)):
        raise ValueError("Rollback archive does not match deployed CodeSha256")
    return expected_code_sha256


def read_contract(path, *, allow_legacy=False):
    archive_digest(path)  # Also bounds the container before parsing its directory.
    try:
        with zipfile.ZipFile(path) as archive:
            matching = [info for info in archive.infolist() if info.filename == MANIFEST]
            if not matching and allow_legacy:
                return {"source_sha":None,"supported":frozenset(),"introduced":frozenset(),"legacy":True}
            if len(matching) != 1 or not 1 <= matching[0].file_size <= MAX_MANIFEST_BYTES:
                raise ValueError("Missing, duplicate or oversized release metadata")
            with archive.open(matching[0]) as source:
                raw = source.read(MAX_MANIFEST_BYTES + 1)
            if len(raw) > MAX_MANIFEST_BYTES:
                raise ValueError("Oversized release metadata")
    except (zipfile.BadZipFile,RuntimeError,OSError) as exc:
        raise ValueError("Unreadable Lambda release archive") from exc
    try:
        item = json.loads(raw,object_pairs_hook=_unique,parse_constant=_reject_constant)
    except (ValueError,UnicodeError,RecursionError) as exc:
        raise ValueError("Invalid release metadata JSON") from exc
    if (not isinstance(item,dict) or not isinstance(item.get("source_sha"),str)
            or not re.fullmatch(r"[a-f0-9]{40}",item["source_sha"])):
        raise ValueError("Invalid release source SHA")
    if set(item) == {"source_sha"} and allow_legacy:
        return {"source_sha":item["source_sha"],"supported":frozenset(),"introduced":frozenset(),"legacy":True}
    if (set(item) != {"source_sha","release_contract_version","supported_state_contracts","introduced_state_contracts"}
            or type(item.get("release_contract_version")) is not int or item["release_contract_version"] != 1):
        raise ValueError("Unsupported or incomplete release contract")
    supported,introduced = _contracts(item["supported_state_contracts"]),_contracts(item["introduced_state_contracts"])
    if not introduced <= supported:
        raise ValueError("Release writes an unsupported state contract")
    return {"source_sha":item["source_sha"],"supported":supported,"introduced":introduced,"legacy":False}


def verify_rollback(candidate, backup, *, expected_sha, backup_code_sha256):
    if not isinstance(expected_sha,str) or not re.fullmatch(r"[a-f0-9]{40}",expected_sha):
        raise ValueError("Invalid accepted source SHA")
    assert_backup_digest(backup,backup_code_sha256)
    new = read_contract(candidate)
    old = read_contract(backup,allow_legacy=True)
    if new["source_sha"] != expected_sha:
        raise ValueError("Candidate source differs from accepted exact SHA")
    if not new["introduced"] <= old["supported"]:
        raise ValueError("Rollback image cannot preserve new persistent state; deploy an accepted compatibility baseline first")
    if not old["supported"] <= new["supported"]:
        raise ValueError("Candidate drops deployed state contract support")
    return {"status":"compatible","candidate_source_sha":new["source_sha"],"rollback_source_sha":old["source_sha"],
            "rollback_code_sha256":backup_code_sha256,"state_contracts":sorted(new["supported"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup",required=True,type=Path)
    parser.add_argument("--backup-code-sha256",required=True)
    parser.add_argument("--candidate",type=Path)
    parser.add_argument("--expected-sha")
    parser.add_argument("--verify-backup-only",action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.verify_backup_only:
            if args.candidate is not None or args.expected_sha is not None:
                raise ValueError("Backup-only verification cannot accept candidate claims")
            result = {"status":"backup_digest_verified","rollback_code_sha256":assert_backup_digest(args.backup,args.backup_code_sha256)}
        else:
            if args.candidate is None or args.expected_sha is None:
                raise ValueError("Candidate archive and accepted source SHA are required")
            result = verify_rollback(args.candidate,args.backup,expected_sha=args.expected_sha,backup_code_sha256=args.backup_code_sha256)
    except (ValueError,OSError) as exc:
        print("Rollback verification refused: " + str(exc))
        return 1
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
