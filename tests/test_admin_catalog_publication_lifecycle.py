"""Real signed lifecycle, immutable recovery and cross-client CAS boundaries."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Barrier

import pytest

from admin_test_support import AdminTestDynamo
from test_admin_catalog_publication_state import (
    ACTOR, OTHER, KEY, TABLE, NOW, fields, prepared_fields, make_state, rows,
)
from test_catalog_source_release import SOURCE, PROPOSAL, export_plan
from guardoath import state_contracts
from guardoath.admin_catalog_publication_state import (
    CATALOG_ID, EMPTY_CONTROL_HASH, DynamoCatalogPublicationState,
    PublicationConflict, PublicationUnavailable, document_hash,
)
from guardoath.admin_catalog_publication_lifecycle import (
    CONTRACT, REPOSITORY, DEPLOYMENT_FORMAT, REQUIRED_WORKFLOWS,
    MAX_DOCUMENT_BYTES, CatalogPublicationLifecycle,
)


@pytest.fixture
def writer(monkeypatch):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_STATE", True)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE", True)


def remote_fields():
    return {"candidate_tree_sha": "f" * 40, "remote_head_sha": "d" * 40, "recorded_at": NOW + 20}


def ci_fields(candidate="d" * 40):
    runs, job_id = [], 1000
    for index, name in enumerate(sorted(REQUIRED_WORKFLOWS)):
        jobs = []
        for job in REQUIRED_WORKFLOWS[name]:
            job_id += 1
            jobs.append({"job_id": job_id, "name": job, "head_sha": candidate,
                         "status": "completed", "conclusion": "success"})
        runs.append({"workflow": name, "run_id": 100 + index, "run_attempt": 1,
                     "head_sha": candidate, "status": "completed", "conclusion": "success", "jobs": jobs})
    return {"runs": runs, "evidence_sha256": document_hash({
        "repository": REPOSITORY, "candidate_source_sha": candidate, "runs": runs}),
        "recorded_at": NOW + 30}


def deployment_receipt(claim, **changes):
    return {
        "format": DEPLOYMENT_FORMAT, "invocation_id": claim["invocation_id"],
        "source_sha": claim["candidate_source_sha"],
        "code_sha256": base64.b64encode(b"c" * 32).decode(),
        "backup_code_sha256": base64.b64encode(b"b" * 32).decode(),
        "backup_source_sha": claim["base_source_sha"],
        "original_revision_id": "original-revision",
        "upload_revision_id": "acknowledged-revision", "verified_revision_id": "completed-revision",
        "last_modified": "2026-11-25T10:00:00.000+0000", "configuration_sha256": "9" * 64,
        "account_id": "839000214942", "region": "us-east-1", "function_name": "guardoath-site",
        "stack_name": "guardoath-site", "distribution_id": "E123456789ABC",
        "invalidation_id": "I123456789ABC", "observed_at": NOW + 40,
        "automatic_rollback_owned": False, "last_update_status": "Successful",
        "release_source_parity_verified": True, "dashboard_ui_matches_local_source": True,
        "expected_source_sha": claim["candidate_source_sha"], "live_source_sha": claim["candidate_source_sha"],
        "verified_asset_count": 13, "expected_asset_count": 13, "production_live_acceptance": False,
        **changes,
    }


def completion_fields(receipt):
    return {"remote_head_sha": receipt["source_sha"], "verified_source_sha": receipt["source_sha"],
            "verified_code_sha256": receipt["code_sha256"], "verified_revision_id": receipt["verified_revision_id"],
            "configuration_sha256": receipt["configuration_sha256"], "last_modified": receipt["last_modified"],
            "recorded_at": NOW + 50}


def advance(state, stop="completed"):
    intent = state.reserve(fields(), EMPTY_CONTROL_HASH)
    preparation = state.claim(intent["request_id"])
    state.complete_preparation(intent["request_id"], preparation, prepared_fields(intent))
    request_id = intent["request_id"]
    if stop == "prepared":
        return request_id
    remote_claim = state.claim_remote(request_id)
    if stop == "publishing":
        return request_id
    state.complete_remote(request_id, remote_claim, remote_fields())
    if stop == "remote":
        return request_id
    state.record_ci(request_id, ci_fields())
    if stop == "ci":
        return request_id
    deployment_claim = state.claim_deployment(request_id)
    if stop == "deploying":
        return request_id
    report = deployment_receipt(deployment_claim)
    state.complete_deployment(request_id, deployment_claim, {"receipt": report})
    if stop == "deployed":
        return request_id
    state.complete_release(request_id, completion_fields(report))
    return request_id


def next_fields(*, actor=ACTOR, reference="another-reviewed-release-123"):
    source = deepcopy(SOURCE)
    source["registry_version"] = "4"
    source["domains"][0]["controls"][0]["rules"][0]["title"] = PROPOSAL["title"]
    proposal = {**PROPOSAL, "expected_registry_version": "4", "proposed_registry_version": "5",
                "title": "Reviewed next email description"}
    return {**fields(actor=actor, reference=reference, plan=export_plan(source, proposal)),
            "base_source_sha": "d" * 40, "created_at": NOW + 60}


def test_source_default_lifecycle_reopens_and_atomically_admits_next_reviewed_generation(tmp_path):
    assert state_contracts.new_catalog_publication_state_enabled() is True
    assert state_contracts.new_catalog_publication_lifecycle_state_enabled() is True
    assert state_contracts.new_subscription_state_enabled() is False
    assert state_contracts.new_cancellation_state_enabled() is False
    path = tmp_path / "signed.sqlite"
    state, db = make_state(AdminTestDynamo(path))
    request_id = advance(state)
    history = state.publication_history(request_id)
    assert history["control"]["state"] == "VERIFIED"
    assert history["completion"]["production_live_acceptance"] is False
    assert history["deployment"]["receipt"]["automatic_rollback_owned"] is False
    assert history["deployment"]["receipt"]["upload_revision_id"] != history["deployment"]["receipt"]["verified_revision_id"]
    assert history["prepared"]["remote_published"] is False
    assert len(history["ci"]["runs"]) == 7
    assert sum(len(run["jobs"]) for run in history["ci"]["runs"]) == 25
    available = state.request_control()
    assert available["available"] is True
    assert available["expected_control_hash"] == document_hash(available["control"])
    assert available["expected_control_hash"] != EMPTY_CONTROL_HASH
    assert available["control"]["state"] == "SOURCE_PREPARED"
    db.close()
    reopened, db = make_state(AdminTestDynamo(path))
    assert reopened.publication_history(request_id) == history
    second = reopened.reserve(next_fields(), available["expected_control_hash"])
    assert reopened.read_control()["request_id"] == second["request_id"]
    assert reopened.request_control()["available"] is False
    assert reopened.publication_history(request_id) == history
    assert reopened.history(request_id) == (history["intent"], history["prepared"])
    before = rows(db)
    assert reopened.reserve(fields(), EMPTY_CONTROL_HASH) == history["intent"]
    assert rows(db) == before
    assert reopened.publication_history(second["request_id"])["prepared"] is None
    db.close()


@pytest.mark.parametrize("stage", ["prepared", "publishing", "remote", "ci", "deploying", "deployed"])
def test_no_nonterminal_state_releases_or_expires_the_global_control(writer, stage):
    state, db = make_state()
    request_id = advance(state, stage)
    status = state.request_control()
    assert status["available"] is False and status["completion"] is None
    before = rows(db)
    later = {**next_fields(), "created_at": 253402300799}
    with pytest.raises(PublicationConflict):
        state.reserve(later, status["expected_control_hash"])
    assert rows(db) == before
    assert state.publication_history(request_id)["completion"] is None


@pytest.mark.parametrize("change", [
    {"base_source_sha": "a" * 40}, {"created_at": NOW + 5},
])
def test_next_generation_cannot_use_wrong_source_or_precede_completed_evidence(writer, change):
    state, db = make_state()
    advance(state)
    before = rows(db)
    with pytest.raises((PublicationConflict, PublicationUnavailable)):
        state.reserve({**next_fields(), **change}, state.request_control()["expected_control_hash"])
    assert rows(db) == before


@pytest.mark.parametrize("kind,stage,method", [
    ("PUBLISHING", "prepared", "claim_remote"), ("DEPLOYING", "ci", "claim_deployment"),
])
def test_reopened_workers_race_for_exactly_one_acknowledged_claim(tmp_path, writer, kind, stage, method):
    barrier = Barrier(2)
    class RacingDynamo(AdminTestDynamo):
        race = False
        def transact_write_items(self, **request):
            control = json.loads(request["TransactItems"][1]["Put"]["Item"]["catalog_publication"]["S"])
            if self.race and control.get("state") == kind:
                barrier.wait(timeout=10)
            return super().transact_write_items(**request)
    path = tmp_path / "race.sqlite"
    first_db, second_db = RacingDynamo(path), RacingDynamo(path)
    first, _ = make_state(first_db)
    second, _ = make_state(second_db)
    request_id = advance(first, stage)
    first_db.race = second_db.race = True
    def attempt(state):
        try:
            return getattr(state, method)(request_id)
        except (PublicationConflict, PublicationUnavailable):
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (first, second)))
    assert sum(result is not None for result in results) == 1
    assert first.publication_history(request_id)["control"]["state"] == kind
    first_db.close()
    second_db.close()


def test_two_owners_race_after_completion_without_orphaning_a_generation(tmp_path, writer):
    barrier = Barrier(2)
    class RacingDynamo(AdminTestDynamo):
        race = False
        def transact_write_items(self, **request):
            if self.race and len(request["TransactItems"]) == 3:
                control = json.loads(request["TransactItems"][1]["Put"]["Item"]["catalog_publication"]["S"])
                if control.get("state") == "REQUESTED":
                    barrier.wait(timeout=10)
            return super().transact_write_items(**request)
    path = tmp_path / "next-generation.sqlite"
    first_db, second_db = RacingDynamo(path), RacingDynamo(path)
    first, _ = make_state(first_db)
    second, _ = make_state(second_db)
    original = advance(first)
    terminal = first.publication_history(original)
    expected = first.request_control()["expected_control_hash"]
    first_db.race = second_db.race = True
    candidates = [next_fields(reference="owner-one-next-release-123"),
                  next_fields(actor=OTHER, reference="owner-two-next-release-123")]
    def attempt(pair):
        state, candidate = pair
        try:
            return state.reserve(candidate, expected)
        except (PublicationConflict, PublicationUnavailable):
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ((first, candidates[0]), (second, candidates[1]))))
    assert sum(result is not None for result in results) == 1
    winner = next(result for result in results if result is not None)
    loser = next(candidate for candidate in candidates if candidate["request_id"] != winner["request_id"])
    assert first.read_control()["request_id"] == winner["request_id"]
    assert second.history(loser["request_id"]) == (None, None)
    assert first.publication_history(original) == terminal
    assert second.publication_history(original) == terminal
    first_db.close()
    second_db.close()


def test_next_reservation_transaction_rechecks_the_exact_terminal_receipt(writer):
    class ProofRaceDynamo(AdminTestDynamo):
        replace_terminal = False
        def transact_write_items(self, **request):
            if self.replace_terminal:
                self.replace_terminal = False
                condition = request["TransactItems"][0]["ConditionCheck"]
                row = self.get_item(TableName=condition["TableName"], Key=condition["Key"])["Item"]
                row["catalog_publication"]["S"] += " "
                self.seed(TABLE, row)
            return super().transact_write_items(**request)
    db = ProofRaceDynamo()
    state, _ = make_state(db)
    original = advance(state)
    control = state.read_control()
    candidate = next_fields()
    db.replace_terminal = True
    with pytest.raises((PublicationConflict, PublicationUnavailable)):
        state.reserve(candidate, document_hash(control))
    assert state.read_control() == control
    assert state._read("intent", candidate["request_id"]) is None
    with pytest.raises(PublicationUnavailable):
        state.publication_history(original)


def test_verified_terminal_refuses_a_valid_but_stale_catalog_review(writer):
    state, db = make_state()
    advance(state)
    status = state.request_control()
    stale = {**fields(reference="valid-stale-catalog-review-123"), "base_source_sha": "d" * 40,
             "created_at": NOW + 60}
    before = rows(db)
    with pytest.raises(PublicationConflict):
        state.reserve(stale, status["expected_control_hash"])
    assert rows(db) == before


@pytest.mark.parametrize("stage,method,expected", [
    ("prepared", "claim_remote", "PUBLISHING"), ("ci", "claim_deployment", "DEPLOYING"),
])
def test_unknown_claim_outcome_is_retained_without_readback_authority(writer, stage, method, expected):
    state, db = make_state()
    request_id = advance(state, stage)
    db.lose_transaction_response = True
    with pytest.raises(PublicationUnavailable):
        getattr(state, method)(request_id)
    db.lose_transaction_response = False
    assert state.publication_history(request_id)["control"]["state"] == expected
    before = rows(db)
    with pytest.raises(PublicationConflict):
        getattr(state, method)(request_id)
    assert rows(db) == before and state.request_control()["available"] is False


@pytest.mark.parametrize("stage,kind", [("publishing", "remote"), ("remote", "ci"),
                                     ("deploying", "deployment"), ("deployed", "completion")])
def test_unknown_receipt_acknowledgement_recovers_immutable_facts_only(writer, stage, kind):
    state, db = make_state()
    request_id = advance(state, stage)
    prior = state.publication_history(request_id)
    claim = prior["control"]
    if kind == "remote":
        action = lambda: state.complete_remote(request_id, claim, remote_fields())
    elif kind == "ci":
        action = lambda: state.record_ci(request_id, ci_fields())
    elif kind == "deployment":
        action = lambda: state.complete_deployment(request_id, claim, {"receipt": deployment_receipt(claim)})
    else:
        action = lambda: state.complete_release(request_id, completion_fields(prior["deployment"]["receipt"]))
    db.lose_transaction_response = True
    with pytest.raises(PublicationUnavailable):
        action()
    db.lose_transaction_response = False
    saved = state.publication_history(request_id)[kind]
    assert saved is not None
    before = rows(db)
    assert action() == saved
    assert rows(db) == before
    assert state.request_control()["available"] is (kind == "completion")


@pytest.mark.parametrize("phase", ["remote", "ci", "deployment", "completion"])
def test_receipt_and_control_are_atomic_under_write_outage(writer, phase):
    stage = {"remote": "publishing", "ci": "remote", "deployment": "deploying", "completion": "deployed"}[phase]
    state, db = make_state()
    request_id = advance(state, stage)
    history = state.publication_history(request_id)
    before = rows(db)
    db.fail_audit = True
    with pytest.raises(PublicationUnavailable):
        if phase == "remote":
            state.complete_remote(request_id, history["control"], remote_fields())
        elif phase == "ci":
            state.record_ci(request_id, ci_fields())
        elif phase == "deployment":
            state.complete_deployment(request_id, history["control"], {"receipt": deployment_receipt(history["control"])})
        else:
            state.complete_release(request_id, completion_fields(history["deployment"]["receipt"]))
    db.fail_audit = False
    assert rows(db) == before and state.publication_history(request_id) == history


@pytest.mark.parametrize("mutation", [
    lambda runs: runs.pop(),
    lambda runs: runs[0].update(head_sha="e" * 40),
    lambda runs: runs[0].update(conclusion="failure"),
    lambda runs: runs[0].update(run_id=True),
    lambda runs: runs[0].update(run_attempt=0),
    lambda runs: runs[1].update(run_id=runs[0]["run_id"]),
    lambda runs: runs[0]["jobs"][0].update(head_sha="e" * 40),
    lambda runs: runs[0]["jobs"][0].update(conclusion="skipped"),
    lambda runs: runs[0]["jobs"][0].update(status="in_progress"),
    lambda runs: runs[0]["jobs"][0].update(job_id=True),
    lambda runs: runs[1]["jobs"][0].update(job_id=runs[0]["jobs"][0]["job_id"]),
    lambda runs: runs[0]["jobs"].append(deepcopy(runs[0]["jobs"][0])),
    lambda runs: runs.reverse(),
])
def test_exact_candidate_complete_allowlisted_ci_is_required(writer, mutation):
    state, db = make_state()
    request_id = advance(state, "remote")
    inputs = ci_fields()
    mutation(inputs["runs"])
    inputs["evidence_sha256"] = document_hash({
        "repository": REPOSITORY, "candidate_source_sha": "d" * 40, "runs": inputs["runs"]})
    before = rows(db)
    with pytest.raises(PublicationUnavailable):
        state.record_ci(request_id, inputs)
    assert rows(db) == before


@pytest.mark.parametrize("change", [
    {"invocation_id": "0" * 64}, {"source_sha": "e" * 40}, {"live_source_sha": "e" * 40},
    {"code_sha256": "not-a-code-digest"}, {"configuration_sha256": False},
    {"automatic_rollback_owned": True}, {"automatic_rollback_owned": 0},
    {"last_update_status": "InProgress"}, {"production_live_acceptance": True},
    {"dashboard_ui_matches_local_source": False}, {"release_source_parity_verified": False},
    {"verified_asset_count": 12}, {"expected_asset_count": True},
    {"account_id": "000000000000"}, {"region": "us-west-2"},
    {"function_name": "another-function"}, {"extra": "untrusted"},
])
def test_guarded_deployment_receipt_cannot_expand_authority_or_claims(writer, change):
    state, db = make_state()
    request_id = advance(state, "deploying")
    claim = state.publication_history(request_id)["control"]
    before = rows(db)
    with pytest.raises(PublicationUnavailable):
        state.complete_deployment(request_id, claim, {"receipt": deployment_receipt(claim, **change)})
    assert rows(db) == before and state.request_control()["available"] is False


@pytest.mark.parametrize("change", [
    {"remote_head_sha": "a" * 40}, {"verified_source_sha": "a" * 40},
    {"verified_code_sha256": base64.b64encode(b"x" * 32).decode()},
    {"verified_revision_id": "unowned-revision"}, {"configuration_sha256": "1" * 64},
    {"last_modified": "2026-11-26T00:00:00.000+0000"}, {"recorded_at": NOW + 1},
])
def test_terminal_release_requires_exact_current_deployment_bindings(writer, change):
    state, db = make_state()
    request_id = advance(state, "deployed")
    report = state.publication_history(request_id)["deployment"]["receipt"]
    before = rows(db)
    with pytest.raises(PublicationUnavailable):
        state.complete_release(request_id, {**completion_fields(report), **change})
    assert rows(db) == before and state.request_control()["available"] is False


@pytest.mark.parametrize("kind,change", [
    ("remote", {"prepared_hash": "1" * 64}),
    ("ci", {"previous_control_hash": "1" * 64}),
    ("deployment", {"candidate_tree_sha": "1" * 40}),
    ("completion", {"receipt_hashes": {"remote": "1" * 64, "ci": "2" * 64, "deployment": "3" * 64}}),
])
def test_even_correctly_signed_orphaned_receipts_cannot_release_control(writer, kind, change):
    state, db = make_state()
    request_id = advance(state)
    lifecycle = CatalogPublicationLifecycle(state)
    history = state.publication_history(request_id)
    changed = lifecycle._seal({**history[kind], **change})
    db.seed(TABLE, lifecycle._put(changed)["Item"])
    with pytest.raises(PublicationUnavailable):
        state.publication_history(request_id)
    with pytest.raises(PublicationUnavailable):
        state.request_control()


@pytest.mark.parametrize("corrupt", ["signature", "whitespace", "duplicate", "oversize"])
def test_noncanonical_or_unauthenticated_history_fails_closed(writer, corrupt):
    state, db = make_state()
    request_id = advance(state)
    lifecycle = CatalogPublicationLifecycle(state)
    receipt = state.publication_history(request_id)["completion"]
    item = lifecycle._put(receipt)["Item"]
    raw = item["catalog_publication"]["S"]
    if corrupt == "signature":
        raw = raw.replace(receipt["signature"], "0" * 64)
    elif corrupt == "whitespace":
        raw = " " + raw
    elif corrupt == "duplicate":
        raw = raw[:-1] + ',"version":1}'
    else:
        raw = " " * (MAX_DOCUMENT_BYTES + 1)
    item["catalog_publication"]["S"] = raw
    db.seed(TABLE, item)
    with pytest.raises(PublicationUnavailable):
        state.request_control()


def test_baseline_reads_complete_history_but_each_writer_gate_is_independent(monkeypatch):
    state, db = make_state()
    with monkeypatch.context() as enabled:
        enabled.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_STATE", True)
        enabled.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE", True)
        request_id = advance(state)
    history = state.publication_history(request_id)
    before = rows(db)
    for old, new in ((False, False), (True, False), (False, True)):
        monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_STATE", old)
        monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE", new)
        assert state.publication_history(request_id) == history
        for action in (
            lambda: state.reserve(next_fields(), state.request_control()["expected_control_hash"]),
            lambda: state.claim_remote(request_id), lambda: state.record_ci(request_id, ci_fields()),
            lambda: state.claim_deployment(request_id),
            lambda: state.complete_release(request_id, completion_fields(history["deployment"]["receipt"])),
        ):
            with pytest.raises(PublicationUnavailable):
                action()
    assert rows(db) == before


@pytest.mark.parametrize("invalid", [None, 0, 1, "true", [], {}])
def test_lifecycle_source_gate_cannot_be_nonboolean(monkeypatch, invalid):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE", invalid)
    with pytest.raises(ValueError):
        state_contracts.new_catalog_publication_lifecycle_state_enabled()
    with pytest.raises(ValueError):
        state_contracts.release_metadata("a" * 40)


def test_lifecycle_source_gate_cannot_be_enabled_by_environment(monkeypatch):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_STATE", False)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE", False)
    for name in ("ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE",
                 "GUARDOATH_ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE",
                 "GUARDOATH_ADMIN_TEST_WRITER_PHASE"):
        monkeypatch.setenv(name, "1")
    assert state_contracts.new_catalog_publication_lifecycle_state_enabled() is False
    assert state_contracts.release_metadata("a" * 40)["introduced_state_contracts"] == []
