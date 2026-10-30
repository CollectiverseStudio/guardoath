"""One record per tenant, one field per check: no value = the check does not run."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Stage
from guardoath.tenant_record import (CHECK_FIELDS, CHECK_IDS, EDITABLE_FIELDS, EXTERNAL_CHECK_IDS, REQUIRED_SETTINGS,
                                     SETTINGS_OF, TENANT_POLICY_VERSION, TenantRecord, demo_defaults, demo_key, demo_records,
                                     key_hash, key_matches, new_key, tenant_id_from_key, usage_month)
from guardoath.tenant_store import MemoryTenantStore, TenantConflict, TenantNotFound, UsageLimitReached

LOCAL_CHECKS = [c for c in CHECK_IDS if c not in EXTERNAL_CHECK_IDS]


def record(**fields):
    return TenantRecord(**{"tenant_id": "acme", "display_name": "Acme", "check_secrets": "flag", **fields})


def demo(n=1, **changes):
    base = demo_records()[n - 1]
    return TenantRecord.model_validate({**base.model_dump(), **changes})


def run(policy, content, stage=Stage.INPUT, checkers=None):
    engine = Engine([policy], checkers if checkers is not None else default_checkers())
    return asyncio.run(engine.check(CheckRequest(application_id=policy.application_id,
                                                 policy_version=TENANT_POLICY_VERSION, content=content), stage))


def test_every_check_is_a_field_on_the_record():
    assert CHECK_FIELDS == tuple("check_" + c for c in CHECK_IDS)
    assert len(CHECK_IDS) == len(set(CHECK_IDS)) == 16
    for name in CHECK_FIELDS:
        assert TenantRecord.model_fields[name].default is None


def test_every_real_checker_has_a_field():
    pytest.importorskip("tldextract")
    pytest.importorskip("phonenumbers")
    real = set(default_checkers(enable_pii=True)) - {"safety", "bias"}
    assert real <= set(CHECK_IDS)
    assert set(CHECK_IDS) - real == EXTERNAL_CHECK_IDS


def test_a_check_without_a_value_does_not_run():
    policy = record(check_text_security=None).to_policy()
    assert [r.checker_id for r in policy.rules] == ["secrets"]


@pytest.mark.parametrize("action", ["block", "flag", "allow"])
def test_a_check_value_is_its_action_on_both_stages(action):
    policy = record(check_secrets=action).to_policy()
    assert len(policy.rules) == 1
    rule = policy.rules[0]
    assert rule.checker_id == "secrets" and rule.on_finding == Action(action)
    assert rule.stages == (Stage.INPUT, Stage.OUTPUT)
    assert policy.application_id == "acme" and policy.version == TENANT_POLICY_VERSION


def test_rules_follow_check_order():
    policy = record(check_text_security="flag", check_pii="block").to_policy()
    assert [r.checker_id for r in policy.rules] == ["pii", "secrets", "text_security"]


def test_redact_is_allowed_on_pii():
    assert record(check_pii="redact").to_policy().rules[0].on_finding == Action.REDACT


@pytest.mark.parametrize("check_id", [c for c in CHECK_IDS if c != "pii"])
def test_redact_is_rejected_on_every_other_check(check_id):
    with pytest.raises(ValidationError, match="Only the pii check can redact"):
        record(**{"check_" + check_id: "redact"})


@pytest.mark.parametrize("value", ["BLOCK", "deny", "", 1, True])
def test_unknown_check_values_are_rejected(value):
    with pytest.raises(ValidationError):
        record(check_secrets=value)


def test_at_least_one_check_must_run():
    with pytest.raises(ValidationError, match="at least one check"):
        record(check_secrets=None)


@pytest.mark.parametrize("check_id", sorted(REQUIRED_SETTINGS))
def test_checks_that_need_settings_cannot_run_without_them(check_id):
    with pytest.raises(ValidationError, match=f"The {check_id} check needs its settings"):
        record(**{"check_" + check_id: "flag"})


def test_settings_of_a_check_that_is_off_are_kept_but_not_used():
    stored = record(ledger={"currency": "USD"}, comparisons=[{"rule_id": "order", "left": "/a", "right": "/b",
                                                             "operator": "lt"}], detect_secret_assignments=True,
                    check_secrets=None, check_text_security="flag")
    policy = stored.to_policy()
    assert stored.ledger.currency == "USD" and len(stored.comparisons) == 1 and stored.detect_secret_assignments
    assert policy.ledger is None and policy.comparisons == () and policy.detect_secret_assignments is False
    Engine([policy], default_checkers())


def test_turning_a_check_back_on_restores_its_settings():
    store = MemoryTenantStore((record(ledger={"currency": "EUR", "decimal_places": 0}),))
    on = store.update_checks("acme", {"check_ledger": "block"}, 1)
    assert on.to_policy().ledger.currency == "EUR"
    off = store.update_checks("acme", {"check_ledger": None}, 2)
    assert off.to_policy().ledger is None and off.ledger.currency == "EUR"
    again = store.update_checks("acme", {"check_ledger": "flag"}, 3)
    assert again.to_policy().ledger.decimal_places == 0


def test_invalid_settings_are_rejected_even_while_their_check_is_off():
    with pytest.raises(ValidationError):
        record(disabled_privacy_entities=["NOT_A_REAL_ENTITY"])
    with pytest.raises(ValidationError):
        record(privacy_patterns=False, disabled_privacy_entities=["URL"])
    with pytest.raises(ValidationError):
        record(ledger={"currency": "usd"})


def test_external_checks_run_only_after_local_checks_pass():
    policy = record(check_content_safety="block").to_policy()
    assert policy.external_checks == "after_local_pass"
    assert record().to_policy().external_checks == "disabled"


def test_prompt_shields_runs_on_input_only():
    rule = record(check_azure_prompt_shields="flag").to_policy().rules[-1]
    assert rule.checker_id == "azure_prompt_shields" and rule.stages == (Stage.INPUT,)


def test_settings_map_covers_every_setting_field():
    for check_id in SETTINGS_OF:
        assert check_id in CHECK_IDS
    for names in SETTINGS_OF.values():
        for name in names:
            assert name in TenantRecord.model_fields and name in EDITABLE_FIELDS


def test_account_fields_are_not_editable():
    for name in ("tenant_id", "status", "demo", "key_hashes", "monthly_allowance", "overage", "usage",
                 "billing_mode", "payer_id", "revision", "updated_at"):
        assert name not in EDITABLE_FIELDS


@pytest.mark.parametrize("tenant_id", ["a", "Acme", "acme_corp", "1acme", "a" * 41, "acme corp"])
def test_tenant_ids_are_restricted(tenant_id):
    with pytest.raises(ValidationError):
        record(tenant_id=tenant_id)


def test_record_round_trips_through_json():
    original = demo()
    assert TenantRecord.model_validate_json(original.model_dump_json()) == original


# Demo users


def test_demo_users_are_demo1_to_demo10():
    users = demo_records()
    assert [u.tenant_id for u in users] == [f"demo{n}" for n in range(1, 11)]
    for user in users:
        assert user.demo and user.overage == "block" and user.key_hashes == () and user.monthly_allowance == 2000
        assert user.checks() == {"pii": Action.BLOCK, "encoded_pii": Action.BLOCK, "secrets": Action.BLOCK,
                                 "text_security": Action.BLOCK}
        assert user.mode == "enforce"


def test_demo_users_have_settings_for_every_check_that_needs_them():
    user = demo()
    for check_id, names in REQUIRED_SETTINGS.items():
        assert any(getattr(user, name) for name in names), check_id


@pytest.mark.parametrize("check_id", [c for c in LOCAL_CHECKS if c not in ("pii", "encoded_pii")])
def test_any_demo_check_can_be_turned_on(check_id):
    for action in ("block", "flag", "allow"):
        policy = demo(**{"check_" + check_id: action, "check_pii": None, "check_encoded_pii": None}).to_policy()
        assert (check_id, Action(action)) in [(r.checker_id, r.on_finding) for r in policy.rules]
        Engine([policy], default_checkers())


def test_every_local_demo_check_can_run_at_once():
    pytest.importorskip("tldextract")
    pytest.importorskip("phonenumbers")
    policy = demo(**{"check_" + c: "flag" for c in LOCAL_CHECKS}).to_policy()
    assert [r.checker_id for r in policy.rules] == LOCAL_CHECKS
    Engine([policy], default_checkers(enable_pii=True))


@pytest.mark.parametrize("check_id", sorted(EXTERNAL_CHECK_IDS))
def test_demo_users_cannot_run_paid_outside_checks(check_id):
    with pytest.raises(ValidationError, match="paid outside provider"):
        demo(**{"check_" + check_id: "flag"})


def test_demo_users_cannot_go_over_their_limit():
    with pytest.raises(ValidationError, match="hard monthly limit"):
        demo(overage="allow_and_record")


DEMO_SECRET = "s" * 32


def test_demo_keys_are_derived_named_and_stored_only_as_hashes():
    users = demo_records(key_secret=DEMO_SECRET)
    keys = [demo_key(u.tenant_id, DEMO_SECRET) for u in users]
    assert len(set(keys)) == 10
    for user, token in zip(users, keys):
        assert token.startswith(f"gok_{user.tenant_id}_") and tenant_id_from_key(token) == user.tenant_id
        assert key_matches(user, token) and token not in user.model_dump_json()
    assert demo_key("demo1", DEMO_SECRET) == keys[0]
    assert demo_key("demo1", "t" * 32) != keys[0]
    assert not key_matches(users[0], keys[1])


@pytest.mark.parametrize("secret", ["", "short", "x" * 31, None, 5])
def test_demo_key_secret_must_be_long_enough(secret):
    with pytest.raises(ValueError, match="at least 32"):
        demo_key("demo1", secret)


def test_demo_users_have_no_keys_without_a_secret():
    assert all(u.key_hashes == () for u in demo_records())


def test_demo_defaults_block_an_email_and_pass_a_clean_question():
    pytest.importorskip("tldextract")
    pytest.importorskip("phonenumbers")
    policy = demo().to_policy()
    checkers = default_checkers(enable_pii=True)
    clean = run(policy, "Can you explain your return policy?", checkers=checkers)
    assert clean.evaluation_status == "complete" and clean.effective_action == Action.ALLOW
    held = run(policy, "Please email jane@example.com about my order.", checkers=checkers)
    assert "pii.email_address" in {f.rule_id for f in held.findings}
    assert held.recommended_action == Action.BLOCK and held.effective_action == Action.BLOCK


def test_demo_reset_restores_every_editable_field():
    store = MemoryTenantStore(demo_records())
    changed = store.update_checks("demo3", {"check_ledger": "flag", "check_pii": None, "mode": "observe",
                                            "timeout_ms": 50, "detect_secret_assignments": False}, 1)
    reset = store.update_checks("demo3", demo_defaults(), changed.revision)
    fresh = demo(3)
    for name in EDITABLE_FIELDS:
        assert getattr(reset, name) == getattr(fresh, name), name


def test_demo_users_are_separate_records():
    store = MemoryTenantStore(demo_records())
    store.update_checks("demo1", {"check_text_security": None}, 1)
    assert store.get("demo1").check_text_security is None
    assert store.get("demo2").check_text_security == Action.BLOCK


# Keys


def test_new_key_names_its_tenant_and_only_the_hash_is_kept():
    token, digest = new_key("acme")
    assert token.startswith("gok_acme_") and len(token) >= 9 + 43
    assert tenant_id_from_key(token) == "acme"
    stored = record(key_hashes=(digest,))
    assert key_matches(stored, token)
    assert token not in stored.model_dump_json()


def test_other_keys_do_not_match():
    token, digest = new_key("acme")
    other, _ = new_key("acme")
    stored = record(key_hashes=(digest,))
    assert not key_matches(stored, other)
    assert not key_matches(record(), token)


def test_two_keys_allow_rotation():
    first, first_hash = new_key("acme")
    second, second_hash = new_key("acme")
    stored = record(key_hashes=(first_hash, second_hash))
    assert key_matches(stored, first) and key_matches(stored, second)
    with pytest.raises(ValidationError):
        record(key_hashes=(first_hash, second_hash, key_hash("third")))


@pytest.mark.parametrize("token", ["", "gok_", "gok_acme", "gok_acme_short", "Bearer gok_acme_" + "a" * 43,
                                   "gok_Acme_" + "a" * 43, "gok_" + "a" * 41 + "_" + "b" * 43,
                                   "sk_acme_" + "a" * 43, "gok_acme_" + "a" * 300, None, 5])
def test_malformed_keys_name_no_tenant(token):
    assert tenant_id_from_key(token) is None


def test_new_key_rejects_bad_tenant_ids():
    with pytest.raises(ValueError):
        new_key("Acme_Corp")


# Store


def test_check_changes_require_the_current_revision():
    store = MemoryTenantStore((record(),))
    first = store.update_checks("acme", {"check_text_security": "block"}, 1)
    assert first.revision == 2 and first.updated_at
    with pytest.raises(TenantConflict):
        store.update_checks("acme", {"check_pii": "flag"}, 1)
    assert store.get("acme").check_pii is None


@pytest.mark.parametrize("field", ["usage", "key_hashes", "demo", "monthly_allowance", "overage", "status",
                                   "tenant_id", "revision", "billing_mode"])
def test_check_changes_cannot_touch_account_fields(field):
    store = MemoryTenantStore((record(),))
    with pytest.raises(ValueError, match="Only check fields and check settings"):
        store.update_checks("acme", {field: None}, 1)


def test_invalid_check_changes_leave_the_record_unchanged():
    store = MemoryTenantStore((record(),))
    with pytest.raises(ValidationError):
        store.update_checks("acme", {"check_secrets": "redact"}, 1)
    assert store.get("acme").revision == 1 and store.get("acme").check_secrets == Action.FLAG


def test_check_changes_keep_usage_and_keys():
    _, digest = new_key("acme")
    store = MemoryTenantStore((record(key_hashes=(digest,), monthly_allowance=5),))
    store.count_check("acme", "2026-10")
    updated = store.update_checks("acme", {"check_pii": "block"}, 1)
    assert updated.used("2026-10") == 1 and updated.key_hashes == (digest,)


def test_missing_tenants_are_reported():
    store = MemoryTenantStore()
    assert store.get("nobody") is None
    with pytest.raises(TenantNotFound):
        store.update_checks("nobody", {}, 1)
    with pytest.raises(TenantNotFound):
        store.count_check("nobody", "2026-10")


def test_creating_the_same_tenant_twice_fails():
    store = MemoryTenantStore((record(),))
    with pytest.raises(TenantConflict):
        store.create(record())


def test_customers_are_counted_past_their_allowance():
    store = MemoryTenantStore((record(monthly_allowance=2),))
    assert [store.count_check("acme", "2026-10") for _ in range(4)] == [(1, False), (2, False), (3, True), (4, True)]
    assert store.get("acme").used("2026-10") == 4


def test_demo_users_stop_at_their_limit():
    store = MemoryTenantStore(demo_records())
    for _ in range(2000):
        store.count_check("demo1", "2026-10")
    with pytest.raises(UsageLimitReached):
        store.count_check("demo1", "2026-10")
    assert store.get("demo1").used("2026-10") == 2000
    assert store.count_check("demo2", "2026-10") == (1, False)


def test_each_month_is_counted_separately():
    store = MemoryTenantStore((record(monthly_allowance=1),))
    store.count_check("acme", "2026-10")
    assert store.count_check("acme", "2026-11") == (1, False)
    assert store.get("acme").usage == {"2026-10": 1, "2026-11": 1}


def test_released_checks_are_given_back_and_never_go_below_zero():
    store = MemoryTenantStore((record(monthly_allowance=1),))
    store.count_check("acme", "2026-10")
    store.release_check("acme", "2026-10")
    store.release_check("acme", "2026-10")
    assert store.get("acme").used("2026-10") == 0


def test_a_hard_limit_holds_under_concurrent_checks():
    limited = demo(1)
    store = MemoryTenantStore((TenantRecord.model_validate({**limited.model_dump(), "monthly_allowance": 20}),))

    def attempt(_):
        try:
            store.count_check("demo1", "2026-10")
            return True
        except UsageLimitReached:
            return False

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(attempt, range(100)))
    assert results.count(True) == 20 and store.get("demo1").used("2026-10") == 20


def test_usage_month_is_utc():
    from datetime import datetime, timedelta, timezone
    late = datetime(2026, 10, 31, 23, 30, tzinfo=timezone(timedelta(hours=-4)))
    assert usage_month(late) == "2026-11"


# Default profile


def test_the_default_profile_starts_as_the_records_own_checks():
    assert demo(1).default_profile == TENANT_POLICY_VERSION == "tenant"
    assert "default_profile" in EDITABLE_FIELDS


@pytest.mark.parametrize("value", ["", "Tenant", "pii v1", "-pii", "a" * 101, "pii_v1"])
def test_default_profile_names_are_restricted(value):
    with pytest.raises(ValidationError):
        TenantRecord.model_validate({**demo(1).model_dump(), "default_profile": value})


def test_a_default_profile_change_is_a_check_change():
    store = MemoryTenantStore(demo_records())
    updated = store.update_checks("demo1", {"default_profile": "pii-v1"}, 1)
    assert updated.default_profile == "pii-v1" and updated.revision == 2
    assert store.get("demo2").default_profile == "tenant"
