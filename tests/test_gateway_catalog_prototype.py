"""Schema/migration experiments only; synthetic scale IDs are not real detectors."""
from copy import deepcopy
import json

import pytest

from guardoath.gateway_prototype.catalog import (
    CATALOG_VERSION, Control, catalog_for, decode_payload, migration_preview,
    update_preview, validate_payload,
)
from guardoath.models import Stage
from guardoath.tenant_record import CHECK_IDS, TenantRecord


def source_record(**changes):
    return TenantRecord(tenant_id='tenant-a', display_name='Tenant A', revision=7,
        check_secrets='allow', confidential_terms=(), **changes)


def test_catalog_ids_stages_actions_and_version_come_from_source_record_contract():
    catalog = catalog_for({})
    assert tuple(catalog) == CHECK_IDS
    assert len(catalog) == 17
    assert catalog['azure_prompt_shields'].stages == ('input',)
    assert 'redact' in catalog['pii'].actions
    assert all('redact' not in c.actions for name, c in catalog.items() if name != 'pii')
    assert all(c.implementation_version == 'unavailable' for c in catalog.values())
    assert all(c.source_path == 'guardoath/tenant_record.py' for c in catalog.values())


def test_migration_is_idempotent_preserves_off_allow_and_disabled_settings():
    record = source_record(disabled_privacy_entities=('IBAN_CODE',))
    catalog = catalog_for({})
    preview = migration_preview(record, catalog)
    assert preview['tenant_id'] == record.tenant_id
    assert preview['record_revision'] == record.revision
    assert preview['validations']['secrets']['input_action'] == 'allow'
    assert preview['validations']['pii']['input_action'] is None
    assert preview['validations']['pii']['settings']['disabled_privacy_entities'] == ['IBAN_CODE']
    assert preview == migration_preview(preview, catalog)
    assert preview == decode_payload(json.dumps(preview), catalog)
    assert record.check_pii is None and record.revision == 7


def test_migrated_actions_exactly_match_real_record_compiler():
    record = source_record(check_pii='redact', check_text_security='flag')
    preview = migration_preview(record, catalog_for({}))
    actual = record.to_policy()
    for rule in actual.rules:
        for stage in rule.stages:
            assert preview['validations'][rule.checker_id][stage.value + '_action'] == rule.on_finding.value
    assert {key for key, row in preview['validations'].items() if row['input_action'] is not None} == set(record.checks())


def test_stage_routing_of_input_only_control_is_source_verified():
    record = source_record(check_azure_prompt_shields='flag')
    compiled = next(r for r in record.to_policy().rules if r.checker_id == 'azure_prompt_shields')
    assert compiled.stages == (Stage.INPUT,)
    preview = migration_preview(record, catalog_for({}))
    assert preview['validations']['azure_prompt_shields']['output_action'] is None


def test_example_profile_is_not_silently_migrated_into_different_actions():
    with pytest.raises(ValueError, match='migration review'):
        migration_preview(source_record(default_profile='other-profile'), catalog_for({}))


@pytest.mark.parametrize('field,value', [('input_action', 'allow-all'), ('input_action', True),
                                       ('input_action', {}), ('settings', []), ('output_action', 'redact')])
def test_malformed_or_unsupported_actions_fail(field, value):
    catalog = catalog_for({})
    preview = migration_preview(source_record(), catalog)
    preview['validations']['secrets'][field] = value
    with pytest.raises(ValueError):
        validate_payload(preview, catalog)


def test_unknown_control_and_unsupported_stage_are_not_passes():
    catalog = catalog_for({})
    preview = migration_preview(source_record(), catalog)
    preview['validations']['sigma.unpublished'] = {'input_action': 'allow', 'output_action': None, 'settings': {}}
    with pytest.raises(ValueError, match='unknown'):
        validate_payload(preview, catalog)
    preview = migration_preview(source_record(), catalog)
    preview['validations']['azure_prompt_shields']['output_action'] = 'allow'
    with pytest.raises(ValueError, match='stage'):
        validate_payload(preview, catalog)


@pytest.mark.parametrize('raw', ['{"tenant_id":"tenant-a","tenant_id":"tenant-b"}',
                               '{"validations":{"a":{},"a":{}}}'])
def test_duplicate_json_keys_are_rejected(raw):
    with pytest.raises(ValueError, match='duplicate'):
        decode_payload(raw, catalog_for({}))


def test_preview_updates_require_owner_and_revision_and_are_nonmutating():
    catalog = catalog_for({})
    original = migration_preview(source_record(), catalog)
    change = {'secrets': {'input_action': 'block', 'output_action': 'flag', 'settings': {}}}
    updated = update_preview(original, change, catalog, trusted_tenant_id='tenant-a', expected_revision=7)
    assert updated['record_revision'] == 8
    assert updated['validations']['secrets']['input_action'] == 'block'
    assert original['record_revision'] == 7
    assert original['validations']['secrets']['input_action'] == 'allow'
    for owner, revision in [('tenant-b', 7), ('tenant-a', 6), ('tenant-a', True)]:
        with pytest.raises(ValueError, match='conflict'):
            update_preview(original, change, catalog, trusted_tenant_id=owner, expected_revision=revision)


def synthetic_scale(count):
    # Schema stress only; these entries are never published as executable checks.
    catalog = {f'fixture.control.{i}': Control(f'fixture.control.{i}', maturity='synthetic-test-only') for i in range(count)}
    payload = {'tenant_id': 'tenant-a', 'format_version': 1, 'catalog_version': CATALOG_VERSION, 'record_revision': 1,
        'validations': {key: {'input_action': 'block', 'output_action': 'flag', 'settings': {}} for key in catalog}}
    return catalog, payload


@pytest.mark.parametrize('count', [1000, 1500, 2048])
def test_thousand_control_schema_serialization_and_update_within_byte_bound(count):
    catalog, payload = synthetic_scale(count)
    raw = json.dumps(payload, separators=(',', ':'))
    assert len(raw.encode()) < 350_000
    decoded = decode_payload(raw, catalog)
    assert len(decoded['validations']) == count
    updated = update_preview(decoded, {'fixture.control.0': {'input_action': None, 'output_action': 'allow', 'settings': {}}},
        catalog, trusted_tenant_id='tenant-a', expected_revision=1)
    assert updated['record_revision'] == 2
    assert len(updated['validations']) == count


def test_control_and_serialization_limits_are_explicit():
    catalog, payload = synthetic_scale(2049)
    with pytest.raises(ValueError, match='count'):
        validate_payload(payload, catalog)
    catalog, payload = synthetic_scale(1)
    payload['validations']['fixture.control.0']['settings'] = {'description': 'x'*350_000}
    with pytest.raises(ValueError, match='size'):
        validate_payload(payload, catalog)


@pytest.mark.parametrize('field,value', [('format_version', True), ('format_version', 2),
    ('record_revision', True), ('record_revision', -1), ('catalog_version', 'unrecognized'), ('tenant_id', 'bad owner')])
def test_revision_owner_and_catalog_version_are_strict(field, value):
    catalog, payload = synthetic_scale(1)
    payload[field] = value
    with pytest.raises(ValueError):
        validate_payload(payload, catalog)


@pytest.mark.parametrize('control,settings', [
    ('pii', {'unexpected': 'private-canary'}),
    ('pii', {'privacy_min_score': '0.5'}),
    ('pii', {'privacy_min_score': True}),
    ('pii', {'privacy_min_score': -0.01}),
    ('pii', {'privacy_min_score': 1.01}),
    ('secrets', {'confidential_terms': 'private-canary'}),
    ('secrets', {'confidential_terms': [{'rule_id': 'term', 'term': 'xx'}]}),
    ('secrets', {'confidential_terms': [{'rule_id': 'term', 'term': 'private-canary', 'unknown': True}]}),
    ('secrets', {'confidential_terms': [{'rule_id': 'term', 'term': 'private-canary', 'stages': ['event']}]}),
    ('schema', {'input_schema': 'private-canary'}),
    ('schema', {'input_schema': {'type': 17}}),
    ('schema', {'input_schema': {'properties': []}}),
    ('schema', {'input_schema': {'$ref': 'https://example.invalid/private-canary'}}),
    ('completeness', {'required_sections': [{'rule_id': 'r', 'label': 'Summary', 'min_characters': '1'}]}),
    ('matched_outcomes', {'matched_outcomes': {'attributes': ['age', 'Age']}}),
    ('ledger', {'ledger': {'currency': 'bad'}}),
    ('timestamps', {'timestamps': {'max_samples': True}}),
])
@pytest.mark.parametrize('enabled', [False, True])
def test_control_settings_use_source_schema_even_when_disabled(control, settings, enabled):
    catalog = catalog_for({})
    preview = migration_preview(source_record(), catalog)
    preview['validations'][control] = {'input_action': 'allow' if enabled else None,
        'output_action': 'allow' if enabled else None, 'settings': settings}
    with pytest.raises(ValueError) as error:
        validate_payload(preview, catalog)
    assert 'private-canary' not in str(error.value)


@pytest.mark.parametrize('score', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_disabled_privacy_setting_is_rejected(score):
    catalog = catalog_for({})
    preview = migration_preview(source_record(), catalog)
    preview['validations']['pii']['settings']['privacy_min_score'] = score
    with pytest.raises(ValueError):
        validate_payload(preview, catalog)


def test_valid_disabled_settings_are_typed_and_roundtrip_without_becoming_active():
    catalog = catalog_for({})
    source = source_record(input_schema={'type': 'object', 'properties': {'name': {'type': 'string'}}},
        required_sections=(), privacy_min_score=0.75)
    preview = migration_preview(source, catalog)
    assert preview['validations']['schema']['input_action'] is None
    assert preview['validations']['schema']['settings']['input_schema'] == source.input_schema
    assert preview == validate_payload(preview, catalog)
    assert preview == migration_preview(preview, catalog)
    assert source.check_schema is None


def test_enabled_required_control_needs_source_required_settings():
    catalog = catalog_for({})
    preview = migration_preview(source_record(), catalog)
    preview['validations']['ledger']['input_action'] = 'block'
    with pytest.raises(ValueError):
        validate_payload(preview, catalog)


def test_control_cannot_borrow_settings_from_another_checker():
    catalog = catalog_for({})
    preview = migration_preview(source_record(), catalog)
    preview['validations']['pii']['settings']['confidential_terms'] = []
    with pytest.raises(ValueError):
        validate_payload(preview, catalog)


def test_all_seventeen_source_controls_migrate_exactly_with_valid_settings():
    from guardoath.engine import Engine
    from guardoath.tenant_record import SETTINGS_OF
    source = TenantRecord(tenant_id='tenant-a', display_name='Tenant A', revision=8,
        **{'check_'+control: 'flag' for control in CHECK_IDS},
        input_schema={'type': 'object'}, output_schema={'type': 'object'},
        comparisons=[{'rule_id': 'comparison', 'left': '/a', 'right': '/b', 'operator': 'eq'}],
        required_sections=[{'rule_id': 'section', 'label': 'Summary'}],
        matched_outcomes={'attributes': ['group']},
        evidence={'sources': [{'source_id': 'approved', 'text': 'Approved source material for testing.'}]},
        tool_calls={'tools': [{'name': 'read_record', 'arguments_schema': {'type': 'object'}}]},
        ledger={'currency': 'USD'}, timestamps={}, graph={}, integrity={'sha256': 'a'*64})
    Engine([source.to_policy()], {})  # Configuration verification only; no adapter runs.
    preview = migration_preview(source, catalog_for({}))
    assert len(preview['validations']) == len(CHECK_IDS) == 17
    raw = source.model_dump(mode='json')
    for control, selection in preview['validations'].items():
        assert selection['input_action'] == 'flag'
        assert selection['output_action'] == (None if control == 'azure_prompt_shields' else 'flag')
        assert selection['settings'] == {key: raw[key] for key in SETTINGS_OF.get(control, ())}
    assert preview == migration_preview(preview, catalog_for({}))


@pytest.mark.parametrize('control', ['timestamps', 'graph'])
@pytest.mark.parametrize('enabled', [False, True])
def test_empty_structured_settings_normalize_source_defaults(control, enabled):
    source = source_record(**{'check_'+control: 'flag', control: {}})
    catalog = catalog_for({})
    preview = migration_preview(source, catalog)
    preview['validations'][control] = {'input_action': 'flag' if enabled else None,
        'output_action': 'flag' if enabled else None, 'settings': {control: {}}}
    normalized = validate_payload(preview, catalog)
    assert normalized['validations'][control]['settings'][control] == source.model_dump(mode='json')[control]
    assert normalized == migration_preview(normalized, catalog)
    assert preview['validations'][control]['settings'][control] == {}  # No caller mutation.


@pytest.mark.parametrize('action', ['off', 'allow', 'flag', 'block'])
def test_migration_preserves_registered_rule_actions_and_roundtrips(action):
    actions = {'prompt.extract_system': action}
    record = source_record(rule_actions=actions)
    before = record.model_dump(mode='json')
    catalog = catalog_for({})
    preview = migration_preview(record, catalog)
    assert preview['rule_actions'] == actions
    assert preview == migration_preview(preview, catalog)
    assert preview == decode_payload(json.dumps(preview), catalog)
    assert record.model_dump(mode='json') == before
    preview['rule_actions']['prompt.extract_system'] = 'off' if action != 'off' else 'block'
    assert record.rule_actions == actions


def test_old_preview_without_per_rule_settings_normalizes_to_explicit_empty_map():
    catalog = catalog_for({})
    preview = migration_preview(source_record(), catalog)
    del preview['rule_actions']
    original = deepcopy(preview)
    normalized = validate_payload(preview, catalog)
    assert normalized['rule_actions'] == {}
    assert normalized == migration_preview(normalized, catalog)
    assert preview == original


@pytest.mark.parametrize('value', [None, [], 'off', True,
    {'private-rule-canary': 'off'}, {'prompt.extract_system': 'redact'},
    {'prompt.extract_system': True}, {'prompt.extract_system': None},
    {'prompt.extract_system': {}}, {1: 'off'}])
def test_malformed_or_unknown_rule_actions_are_rejected_without_echo(value):
    catalog = catalog_for({})
    preview = migration_preview(source_record(), catalog)
    preview['rule_actions'] = value
    before = deepcopy(preview)
    with pytest.raises(ValueError, match='per-rule settings') as error:
        validate_payload(preview, catalog)
    assert 'private-rule-canary' not in str(error.value)
    assert preview == before


@pytest.mark.parametrize('stage', ['input', 'output'])
@pytest.mark.parametrize('action', ['allow', 'flag', 'block'])
def test_preview_pii_redaction_cannot_be_overridden_by_per_rule_action(stage, action):
    from guardoath.rule_catalog import RULES
    rule_id = next(key for key in RULES if key.startswith('pii.'))
    catalog = catalog_for({})
    preview = migration_preview(source_record(), catalog)
    preview['validations']['pii'][stage + '_action'] = 'redact'
    preview['rule_actions'] = {rule_id: action}
    with pytest.raises(ValueError, match='per-rule settings'):
        validate_payload(preview, catalog)


def test_pii_off_and_unrelated_actions_survive_redaction_and_control_updates():
    from guardoath.rule_catalog import RULES
    rule_id = next(key for key in RULES if key.startswith('pii.'))
    actions = {rule_id: 'off', 'prompt.extract_system': 'block'}
    catalog = catalog_for({})
    preview = migration_preview(source_record(check_pii='redact', rule_actions=actions), catalog)
    change = {'secrets': {'input_action': 'flag', 'output_action': 'block', 'settings': {}}}
    updated = update_preview(preview, change, catalog, trusted_tenant_id='tenant-a', expected_revision=7)
    assert updated['rule_actions'] == preview['rule_actions'] == actions
    assert updated['record_revision'] == 8
    updated['rule_actions'].clear()
    assert preview['rule_actions'] == actions


def test_turning_on_redaction_cannot_invalidate_retained_per_rule_settings_silently():
    from guardoath.rule_catalog import RULES
    rule_id = next(key for key in RULES if key.startswith('pii.'))
    catalog = catalog_for({})
    preview = migration_preview(source_record(rule_actions={rule_id: 'flag'}), catalog)
    change = {'pii': {'input_action': 'redact', 'output_action': 'redact', 'settings': {}}}
    with pytest.raises(ValueError, match='per-rule settings'):
        update_preview(preview, change, catalog, trusted_tenant_id='tenant-a', expected_revision=7)
    assert preview['record_revision'] == 7
    assert preview['validations']['pii']['input_action'] is None
    assert preview['rule_actions'] == {rule_id: 'flag'}


def test_duplicate_rule_action_keys_in_serialized_preview_are_rejected():
    catalog = catalog_for({})
    preview = migration_preview(source_record(rule_actions={'prompt.extract_system': 'off'}), catalog)
    raw = json.dumps(preview).replace('"prompt.extract_system": "off"',
        '"prompt.extract_system": "off", "prompt.extract_system": "allow"')
    with pytest.raises(ValueError, match='duplicate'):
        decode_payload(raw, catalog)
