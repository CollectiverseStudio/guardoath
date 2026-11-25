"""Synthetic external observations through the actual signed release state.

This helper is test-only. It performs no GitHub or AWS operation. API/browser
acceptance uses the resulting real immutable records to verify the projection
and recovery of a complete lifecycle without adding a test HTTP endpoint.
"""
import base64
import hashlib
import json
from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from guardoath.admin_catalog_publication_lifecycle import DEPLOYMENT_FORMAT, REQUIRED_WORKFLOWS
from guardoath.admin_catalog_publication_state import document_hash


def ci_fields(candidate, recorded_at):
    runs, job_id = [], 5000
    for index, workflow in enumerate(sorted(REQUIRED_WORKFLOWS)):
        jobs = []
        for name in REQUIRED_WORKFLOWS[workflow]:
            job_id += 1
            jobs.append({"job_id": job_id, "name": name, "head_sha": candidate,
                         "status": "completed", "conclusion": "success"})
        runs.append({"workflow": workflow, "run_id": 1000 + index, "run_attempt": 1,
            "head_sha": candidate, "status": "completed", "conclusion": "success", "jobs": jobs})
    return {"runs": runs, "recorded_at": recorded_at, "evidence_sha256": document_hash({
        "repository": "CollectiverseStudio/guardoath", "candidate_source_sha": candidate, "runs": runs})}


def deployment_report(claim, recorded_at):
    digest = lambda value: base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()
    candidate = claim["candidate_source_sha"]
    return {"format": DEPLOYMENT_FORMAT, "invocation_id": claim["invocation_id"],
        "source_sha": candidate, "code_sha256": digest(candidate),
        "backup_code_sha256": digest(claim["base_source_sha"]), "backup_source_sha": claim["base_source_sha"],
        "original_revision_id": "original-revision", "upload_revision_id": "upload-revision",
        "verified_revision_id": "upload-revision", "last_modified": "2026-11-25T01:02:03Z",
        "configuration_sha256": "7" * 64, "account_id": "839000214942", "region": "us-east-1",
        "function_name": "guardoath-site", "stack_name": "guardoath-site",
        "distribution_id": "TESTDISTRIBUTION", "invalidation_id": "TESTINVALIDATION",
        "observed_at": recorded_at, "automatic_rollback_owned": True,
        "last_update_status": "Successful", "release_source_parity_verified": True,
        "dashboard_ui_matches_local_source": True, "expected_source_sha": candidate,
        "live_source_sha": candidate, "verified_asset_count": 17, "expected_asset_count": 17,
        "production_live_acceptance": False}


def publication_stages(state, request_id, *, candidate="b" * 40):
    """Yield each actual persisted stage with synthetic provider observations."""
    intent = state.read_request(request_id)
    stamp = intent["created_at"] + 1
    preparing = state.claim(request_id)
    yield "preparing"
    state.complete_preparation(request_id, preparing, {"base_source_sha": intent["base_source_sha"],
        "candidate_source_sha": candidate, "history_sha256": "c" * 64,
        "candidate_metadata_sha256": intent["candidate_metadata_sha256"],
        "candidate_registry_version": intent["candidate_registry_version"], "recorded_at": stamp})
    yield "source_prepared"
    publishing = state.claim_remote(request_id)
    yield "publishing"
    state.complete_remote(request_id, publishing, {"candidate_tree_sha": "d" * 40,
        "remote_head_sha": candidate, "recorded_at": stamp + 1})
    yield "remote_published"
    state.record_ci(request_id, ci_fields(candidate, stamp + 2))
    yield "ci_verified"
    deploying = state.claim_deployment(request_id)
    yield "deploying"
    report = deployment_report(deploying, stamp + 3)
    state.complete_deployment(request_id, deploying, {"receipt": report})
    yield "deployed"
    state.complete_release(request_id, {"remote_head_sha": candidate,
        "verified_source_sha": candidate, "verified_code_sha256": report["code_sha256"],
        "verified_revision_id": report["verified_revision_id"],
        "configuration_sha256": report["configuration_sha256"],
        "last_modified": report["last_modified"], "recorded_at": stamp + 4})
    yield "completed"


if __name__ == "__main__":
    from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY
    from guardoath import state_contracts
    from guardoath.admin_catalog_publication_state import DynamoCatalogPublicationState
    state_contracts.ENABLE_NEW_CATALOG_PUBLICATION_STATE = True
    state_contracts.ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE = True
    database = AdminTestDynamo(sys.argv[1])
    try:
        state = DynamoCatalogPublicationState(database, "audit_table", ADMIN_BROWSER_AUDIT_KEY)
        stages = list(publication_stages(state, sys.argv[2]))
        print(json.dumps({"stages": stages, "history": state.publication_history(sys.argv[2])}))
    finally:
        database.close()
