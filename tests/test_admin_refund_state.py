"""Real signed refund records, transactional races and bounded recovery history."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY
from guardoath import state_contracts
from guardoath.admin_cancellation_provider import canonical, digest
from guardoath.admin_refund_state import (
    CONTRACT, DynamoRefundState, RefundStateConflict, RefundStateUnavailable,
    intent_hash, outcome_hash, target_id,
)
from guardoath.dynamodb_store import to_item
from guardoath.tenant_record import TenantRecord

ACTOR = "12345678-1234-1234-1234-123456789012"
TENANT = "usr-refund"
NOW = 1795500000
KEY = ADMIN_BROWSER_AUDIT_KEY


@pytest.fixture(autouse=True)
def writer(monkeypatch):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_REFUND_STATE", True)


def snapshot():
    return {"account_id": "acct_owner", "livemode": False, "customer_id": "cus_refund",
        "subscription_id": "sub_refund", "invoice_id": "in_paid", "charge_id": "ch_settled",
        "payment_intent_id": "pi_paid", "amount_minor": 499, "currency": "usd",
        "expected_price_id": "price_plan"}


def fields(*, action_id="a" * 64, value=None):
    value = snapshot() if value is None else value
    return {**value, "action_id": action_id, "target_id": target_id(value), "actor_sub": ACTOR,
        "tenant_id": TENANT, "expected_revision": 3, "request_hash": "b" * 64,
        "provider_hash": digest(value), "reason": "Owner approved full settled invoice refund",
        "created_at": NOW}


def proof(intent, status="succeeded", **changes):
    return {**{k: intent[k] for k in snapshot()}, "action_id": intent["action_id"],
            "refund_id": "re_full", "status": status, "refund_created": NOW, **changes}


def make(db=None, *, seed=True):
    db = db or AdminTestDynamo()
    if seed:
        record = TenantRecord(tenant_id=TENANT, display_name="Synthetic refund account",
            revision=3, demo=False, billing_mode="stripe", payer_id="cus_refund",
            stripe_subscription_id="sub_refund", subscription_status="active", monthly_allowance=4000,
            overage="block", key_hashes=("b" * 64,), input_check_pii="block", output_check_pii="block",
            input_rule_actions={"pii.email_address": "off"}, output_rule_actions={"pii.email_address": "off"},
            usage={"2026-11": 231})
        db.seed("tenant_table", to_item(record))
    return DynamoRefundState(db, "audit_table", KEY), db


def stored_tenant(db):
    return db.get_item(TableName="tenant_table", Key={"tenant_id": {"S": TENANT}})["Item"]


def reserved(state, value=None):
    return state.reserve(fields() if value is None else value, expected_control_hash=digest(None))


def dispatched(state, db, control):
    return state.dispatching(control, tenant_table="tenant_table", tenant_id=TENANT,
        expected_revision=3, original_document=stored_tenant(db)["record"]["S"])


def seed_value(state, db, value):
    db.seed("audit_table", state._put_request(state._seal(value))["Item"])


def test_unknown_pending_verified_chain_reopens_with_writer_disabled_and_preserves_tenant(tmp_path, monkeypatch):
    path = tmp_path / "refund-history.sqlite3"
    state, db = make(AdminTestDynamo(path))
    before = stored_tenant(db)
    control, intent = reserved(state)
    assert control["generation"] == 1 and not control["dispatch_started"]
    dispatched(state, db, control)
    first = state.outcome(intent, "indeterminate", NOW)
    first_item = copy.deepcopy(state._put_request(first)["Item"])
    pending = state.observe(intent, "pending", NOW + 1, proof(intent, "pending"))
    final = state.observe(intent, "verified", NOW + 2, proof(intent))
    assert [first["sequence"], pending["sequence"], final["sequence"]] == [1, 2, 3]
    assert pending["previous_hash"] == outcome_hash(first)
    assert final["previous_hash"] == outcome_hash(pending)
    assert final["intent_hash"] == intent_hash(intent)
    assert db.get_item(TableName="audit_table", Key={"event_id": first_item["event_id"]})["Item"] == first_item
    assert stored_tenant(db) == before
    assert len(db.audit_items()) == 5
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_REFUND_STATE", False)
    reopened = AdminTestDynamo(path)
    try:
        reader = DynamoRefundState(reopened, "audit_table", KEY)
        assert reader.history(intent["action_id"]) == (intent, final)
        held = reader.read("control", intent["target_id"])
        assert held["state"] == "VERIFIED" and held["outcome_sequence"] == 3
        assert held["dispatch_started"] is True
        assert stored_tenant(reopened) == before
    finally:
        reopened.close()
        db.close()


@pytest.mark.parametrize("status,provider_status", [("verified", "succeeded"), ("failed", "failed"), ("failed", "canceled")])
def test_final_outcome_keeps_permanent_claim_and_cannot_change(status, provider_status):
    state, db = make()
    control, intent = reserved(state)
    dispatched(state, db, control)
    result = state.outcome(intent, status, NOW, proof(intent, provider_status))
    count = db.transactions
    assert state.observe(intent, status, NOW + 86400, proof(intent, provider_status)) == result
    assert db.transactions == count
    other_status = "failed" if status == "verified" else "verified"
    with pytest.raises(RefundStateConflict):
        state.observe(intent, other_status, NOW + 1, proof(intent, "failed" if status == "verified" else "succeeded"))
    with pytest.raises(RefundStateConflict):
        state.reserve(fields(action_id="c" * 64), expected_control_hash=digest(state.read("control", intent["target_id"])))
    assert state.history(intent["action_id"])[1] == result


def test_pending_reads_do_not_append_and_preserve_actual_recorded_status_and_time():
    state, db = make()
    control, intent = reserved(state)
    dispatched(state, db, control)
    pending = state.outcome(intent, "pending", NOW, proof(intent, "pending"))
    count = db.transactions
    same = state.observe(intent, "pending", NOW + 1, proof(intent, "requires_action"))
    assert same == pending and same["proof"]["status"] == "pending" and same["recorded_at"] == NOW
    assert db.transactions == count
    with pytest.raises(RefundStateConflict):
        state.observe(intent, "pending", NOW + 1, proof(intent, "pending", refund_id="re_other"))
    with pytest.raises(RefundStateConflict):
        state.observe(intent, "indeterminate", NOW + 1, None)
    final = state.observe(intent, "verified", NOW + 2, proof(intent))
    assert final["sequence"] == 2


def test_unknown_target_remains_held_after_days_and_reader_only_history_does_not_write():
    state, db = make()
    control, intent = reserved(state)
    dispatched(state, db, control)
    unknown = state.outcome(intent, "indeterminate", NOW)
    count = db.transactions
    assert state.outcome(intent, "indeterminate", NOW + 86400 * 30) == unknown
    assert state.history(intent["action_id"]) == (intent, unknown)
    assert db.transactions == count
    with pytest.raises(RefundStateConflict):
        state.reserve({**fields(action_id="c" * 64), "created_at": NOW + 86400 * 30},
                      expected_control_hash=digest(state.read("control", intent["target_id"])))


@pytest.mark.parametrize("terminal", ["verified", "pending", "failed"])
def test_reserved_without_dispatch_cannot_claim_provider_outcome_or_later_promotion(terminal):
    state, db = make()
    control, intent = reserved(state)
    raw = {"verified": "succeeded", "pending": "pending", "failed": "failed"}[terminal]
    with pytest.raises(RefundStateUnavailable):
        state.outcome(intent, terminal, NOW, proof(intent, raw))
    unknown = state.outcome(intent, "indeterminate", NOW)
    with pytest.raises(RefundStateUnavailable):
        state.observe(intent, terminal, NOW + 1, proof(intent, raw))
    assert state.history(intent["action_id"])[1] == unknown
    assert state.read("control", control["target_id"])["dispatch_started"] is False


@pytest.mark.parametrize("lost_stage", ["reserve", "dispatch", "outcome", "observation"])
def test_lost_committed_response_is_recovered_from_signed_state_without_new_claim(lost_stage):
    state, db = make()
    if lost_stage == "reserve":
        db.lose_transaction_response = True
        with pytest.raises(RefundStateUnavailable): reserved(state)
        intent, latest = state.history("a" * 64)
        assert intent is not None and latest is None
        assert state.read("control", intent["target_id"])["state"] == "RESERVED"
    else:
        control, intent = reserved(state)
        if lost_stage == "dispatch":
            db.lose_transaction_response = True
            with pytest.raises(RefundStateUnavailable): dispatched(state, db, control)
            assert state.read("control", intent["target_id"])["state"] == "DISPATCHING"
            with pytest.raises(RefundStateUnavailable): dispatched(state, db, control)
            assert state.history(intent["action_id"])[1] is None
        else:
            dispatched(state, db, control)
            if lost_stage == "observation":
                state.outcome(intent, "indeterminate", NOW)
            db.lose_transaction_response = True
            call = state.outcome if lost_stage == "outcome" else state.observe
            with pytest.raises(RefundStateUnavailable): call(intent, "verified", NOW + 1, proof(intent))
            actual = state.history(intent["action_id"])[1]
            assert actual["status"] == "verified"
            count = db.transactions
            assert state.observe(intent, "verified", NOW + 2, proof(intent)) == actual
            assert db.transactions == count


@pytest.mark.parametrize("mutation", ["revision", "document", "tenant", "table"])
def test_dispatch_checks_exact_tenant_revision_and_document(mutation):
    state, db = make()
    control, intent = reserved(state)
    original = stored_tenant(db)
    args = {"tenant_table": "tenant_table", "tenant_id": TENANT, "expected_revision": 3,
            "original_document": original["record"]["S"]}
    if mutation == "revision":
        changed = copy.deepcopy(original)
        changed["revision"]["N"] = "4"
        db.seed("tenant_table", changed)
    elif mutation == "document":
        changed = copy.deepcopy(original)
        changed["record"]["S"] += " "
        db.seed("tenant_table", changed)
    elif mutation == "tenant": args["tenant_id"] = "usr-other"
    else: args["tenant_table"] = "audit_table"
    before = stored_tenant(db)
    with pytest.raises(RefundStateUnavailable): state.dispatching(control, **args)
    assert state.read("control", intent["target_id"]) == control
    assert stored_tenant(db) == before


def test_independent_clients_racing_same_charge_receive_only_one_claim(tmp_path):
    path = tmp_path / "charge-race.sqlite3"
    one, db1 = make(AdminTestDynamo(path))
    two, db2 = make(AdminTestDynamo(path), seed=False)
    barrier = Barrier(2)
    def run(state, value):
        barrier.wait(timeout=5)
        try:
            state.reserve(value, expected_control_hash=digest(None))
            return "reserved"
        except RefundStateConflict:
            return "conflict"
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(run, one, fields()), pool.submit(run, two, fields(action_id="c" * 64))]
            assert sorted(job.result(timeout=10) for job in jobs) == ["conflict", "reserved"]
        assert len(db1.audit_items()) == 2
    finally:
        db1.close(); db2.close()


def test_action_collision_on_different_charge_rolls_back_losing_target(tmp_path):
    path = tmp_path / "action-race.sqlite3"
    one, db1 = make(AdminTestDynamo(path))
    two, db2 = make(AdminTestDynamo(path), seed=False)
    second = fields(value={**snapshot(), "charge_id": "ch_second", "invoice_id": "in_second"})
    barrier = Barrier(2)
    def run(state, value):
        barrier.wait(timeout=5)
        try:
            state.reserve(value, expected_control_hash=digest(None))
            return value["target_id"]
        except RefundStateConflict:
            return None
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(run, one, fields()), pool.submit(run, two, second)]
            results = [job.result(timeout=10) for job in jobs]
        assert sum(value is not None for value in results) == 1
        losing_target = fields()["target_id"] if results[0] is None else second["target_id"]
        assert one.read("control", losing_target) is None
        assert len(db1.audit_items()) == 2
    finally:
        db1.close(); db2.close()


def test_concurrent_dispatch_and_resolution_each_have_one_successful_cas(tmp_path):
    path = tmp_path / "dispatch-race.sqlite3"
    one, db1 = make(AdminTestDynamo(path))
    two, db2 = make(AdminTestDynamo(path), seed=False)
    control, intent = reserved(one)
    barrier = Barrier(2)
    def dispatch(state, db):
        barrier.wait(timeout=5)
        try:
            dispatched(state, db, control)
            return "granted"
        except RefundStateUnavailable:
            return "refused"
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(dispatch, one, db1), pool.submit(dispatch, two, db2)]
            assert sorted(job.result(timeout=10) for job in jobs) == ["granted", "refused"]
        one.outcome(intent, "indeterminate", NOW)
        barrier = Barrier(2)
        def resolve(state, status, raw):
            barrier.wait(timeout=5)
            try:
                return state.observe(intent, status, NOW + 1, proof(intent, raw))["status"]
            except (RefundStateConflict, RefundStateUnavailable):
                return "refused"
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(resolve, one, "verified", "succeeded"),
                    pool.submit(resolve, two, "failed", "failed")]
            results = [job.result(timeout=10) for job in jobs]
        assert results.count("refused") == 1
        assert len(db1.audit_items()) == 4
        assert one.history(intent["action_id"])[1]["status"] in ("verified", "failed")
    finally:
        db1.close(); db2.close()


@pytest.mark.parametrize("name,value", [
    ("account_id", "acct_other"), ("livemode", True), ("customer_id", "cus_other"),
    ("subscription_id", "sub_other"), ("invoice_id", "in_other"), ("charge_id", "ch_other"),
    ("payment_intent_id", "pi_other"), ("amount_minor", 1), ("amount_minor", True),
    ("currency", "eur"), ("expected_price_id", "price_other"), ("action_id", "c" * 64),
    ("refund_id", "ch_wrong"), ("refund_created", NOW - 1), ("refund_created", NOW + 1),
    ("refund_created", True), ("status", "pending"), ("extra", "private data"),
])
def test_provider_evidence_must_match_exact_signed_intent_before_recording(name, value):
    state, db = make()
    control, intent = reserved(state)
    dispatched(state, db, control)
    before = db.audit_items()
    with pytest.raises(RefundStateUnavailable):
        state.outcome(intent, "verified", NOW, {**proof(intent), name: value})
    assert db.audit_items() == before and state.history(intent["action_id"])[1] is None


@pytest.mark.parametrize("name,value", [
    ("action_id", "bad"), ("target_id", "c" * 64), ("provider_hash", "c" * 64),
    ("actor_sub", "a"), ("tenant_id", "../elsewhere"), ("expected_revision", True),
    ("expected_revision", 0), ("created_at", True), ("created_at", 0), ("reason", "short"),
    ("amount_minor", True), ("currency", "USD"), ("livemode", 0), ("extra", "value"),
    ("kind", "outcome"), ("generation", 2),
])
def test_bad_intent_is_refused_before_any_state_read_or_write(monkeypatch, name, value):
    state, db = make()
    reads = []
    monkeypatch.setattr(db, "get_item", lambda **kwargs: reads.append(kwargs) or {})
    with pytest.raises(RefundStateUnavailable): reserved(state, {**fields(), name: value})
    assert reads == [] and db.audit_items() == []


@pytest.mark.parametrize("corruption", ["signature", "canonical", "extra_attribute", "wrong_key", "wrong_domain", "missing_control"])
def test_corrupted_or_misbound_persisted_records_fail_closed(corruption):
    state, db = make()
    control, intent = reserved(state)
    item = state._put_request(intent)["Item"]
    if corruption == "signature":
        body = json.loads(item["financial_operation"]["S"])
        body["amount_minor"] = 1
        item["financial_operation"]["S"] = canonical(body)
    elif corruption == "canonical": item["financial_operation"]["S"] += " "
    elif corruption == "extra_attribute": item["expires"] = {"N": str(NOW + 1)}
    elif corruption == "wrong_key":
        state = DynamoRefundState(db, "audit_table", b"different-audit-signing-key-0123456789")
    elif corruption == "wrong_domain":
        item["financial_operation"]["S"] = item["financial_operation"]["S"].replace(CONTRACT, "stripe-period-end-cancellation-v1")
    else:
        db.connection.execute("DELETE FROM items WHERE table_name=? AND item_key=?",
                              ("audit_table", state.row_id("control", control["target_id"])))
    db.seed("audit_table", item)
    with pytest.raises(RefundStateUnavailable): state.history(intent["action_id"])


@pytest.mark.parametrize("corruption", ["missing_first", "missing_middle", "wrong_previous_hash", "backward_clock", "regression", "wrong_refund", "pointer_hash"])
def test_recovery_requires_the_whole_independently_verified_chain(corruption):
    state, db = make()
    control, intent = reserved(state)
    dispatched(state, db, control)
    first = state.outcome(intent, "indeterminate", NOW)
    middle = state.observe(intent, "pending", NOW + 1, proof(intent, "pending"))
    final = state.observe(intent, "verified", NOW + 2, proof(intent))
    held = state.read("control", intent["target_id"])
    if corruption in ("missing_first", "missing_middle"):
        victim = first if corruption == "missing_first" else middle
        db.connection.execute("DELETE FROM items WHERE table_name=? AND item_key=?",
            ("audit_table", state.row_id(victim["kind"], state._identifier(victim))))
    else:
        if corruption == "wrong_previous_hash": final = {**final, "previous_hash": "c" * 64}
        elif corruption == "backward_clock": final = {**final, "recorded_at": NOW}
        elif corruption == "regression":
            pending_proof = proof(intent, "pending")
            final = {**final, "status": "pending", "proof": pending_proof, "proof_hash": digest(pending_proof)}
            held = {**held, "state": "PENDING"}
        elif corruption == "wrong_refund":
            other = proof(intent, refund_id="re_other")
            final = {**final, "proof": other, "proof_hash": digest(other)}
        held = {**held, "outcome_hash": "c" * 64 if corruption == "pointer_hash" else outcome_hash(final)}
        seed_value(state, db, final)
        seed_value(state, db, held)
    with pytest.raises(RefundStateUnavailable): state.history(intent["action_id"])


def test_failed_observation_transaction_cannot_leave_orphan_receipt(monkeypatch):
    state, db = make()
    control, intent = reserved(state)
    dispatched(state, db, control)
    first = state.outcome(intent, "indeterminate", NOW)
    before = db.audit_items()
    original = db.put_item
    def failing_outcome(**kwargs):
        if ":observation:" in kwargs["Item"]["event_id"]["S"]:
            raise RuntimeError("synthetic second-write failure")
        return original(**kwargs)
    monkeypatch.setattr(db, "put_item", failing_outcome)
    with pytest.raises(RefundStateUnavailable): state.observe(intent, "verified", NOW + 1, proof(intent))
    assert db.audit_items() == before and state.history(intent["action_id"])[1] == first


def test_authentic_old_control_replay_cannot_hide_later_terminal_observation():
    state, db = make()
    control, intent = reserved(state)
    dispatched(state, db, control)
    state.outcome(intent, "indeterminate", NOW)
    old_control = state.read("control", intent["target_id"])
    state.observe(intent, "verified", NOW + 1, proof(intent))
    seed_value(state, db, old_control)
    with pytest.raises(RefundStateUnavailable): state.history(intent["action_id"])


def test_orphan_observation_cannot_masquerade_as_action_not_found():
    state, db = make()
    control, intent = reserved(state)
    dispatched(state, db, control)
    state.outcome(intent, "indeterminate", NOW)
    state.observe(intent, "verified", NOW + 1, proof(intent))
    for kind in ("intent", "outcome"):
        db.connection.execute("DELETE FROM items WHERE table_name=? AND item_key=?",
                              ("audit_table", state.row_id(kind, intent["action_id"])))
    with pytest.raises(RefundStateUnavailable): state.history(intent["action_id"])


def test_strict_status_and_backward_clock_cannot_append_invalid_history():
    state, db = make()
    control, intent = reserved(state)
    dispatched(state, db, control)
    for status, now, evidence in (("verified", NOW, None), ("indeterminate", NOW, proof(intent)),
                                   ("verified", NOW - 1, proof(intent)), ("verified", True, proof(intent)),
                                   ([], NOW, proof(intent))):
        with pytest.raises(RefundStateUnavailable): state.outcome(intent, status, now, evidence)
    assert state.history(intent["action_id"])[1] is None
    assert len(db.audit_items()) == 2
