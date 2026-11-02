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
