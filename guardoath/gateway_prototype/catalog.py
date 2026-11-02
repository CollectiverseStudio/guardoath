"""Descriptive catalog/migration experiment; never a runtime policy authority."""
from copy import deepcopy
from dataclasses import dataclass
import json
import re

from guardoath.tenant_record import CHECK_IDS, INPUT_ONLY_CHECK_IDS, SETTINGS_OF, TENANT_ID_PATTERN, TenantRecord

CATALOG_VERSION = 'prototype-1'
MAX_CONTROL_COUNT = 2048
MAX_RECORD_BYTES = 350_000  # Headroom below DynamoDB's item ceiling; not a storage integration.
_ID = re.compile(r'^[A-Za-z0-9_.-]{1,80}$')


@dataclass(frozen=True)
class Control:
    control_id: str
    stages: tuple[str, ...] = ('input', 'output')
    actions: tuple[str, ...] = ('block', 'flag', 'allow')
    source_path: str = 'guardoath/tenant_record.py'
    implementation_version: str = 'unavailable'
    maturity: str = 'source-backed'


def catalog_for(checkers):
    """IDs/stages/actions come from the actual persisted TenantRecord schema."""
    return {control_id: Control(control_id,
        stages=('input',) if control_id in INPUT_ONLY_CHECK_IDS else ('input', 'output'),
        actions=('block', 'flag', 'allow', 'redact') if control_id == 'pii' else ('block', 'flag', 'allow'),
        implementation_version=str(getattr(checkers.get(control_id), 'checker_version', 'unavailable')))
        for control_id in CHECK_IDS}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate configuration key')
        result[key] = value
    return result


def decode_payload(raw, catalog):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_RECORD_BYTES:
        raise ValueError('configuration exceeds supported size')
    return validate_payload(json.loads(raw, object_pairs_hook=_unique_object), catalog)


def validate_payload(payload, catalog):
    if not isinstance(payload, dict) or set(payload) != {'tenant_id', 'format_version', 'catalog_version', 'record_revision', 'validations'}:
        raise ValueError('invalid canonical envelope')
    if not isinstance(payload['tenant_id'], str) or not re.fullmatch(TENANT_ID_PATTERN, payload['tenant_id']):
        raise ValueError('invalid preview owner')
    if type(payload['format_version']) is not int or payload['format_version'] != 1 or payload['catalog_version'] != CATALOG_VERSION:
        raise ValueError('unsupported configuration version')
    if type(payload['record_revision']) is not int or payload['record_revision'] < 0:
        raise ValueError('invalid source revision')
    controls = payload['validations']
    if not isinstance(controls, dict) or not 1 <= len(controls) <= MAX_CONTROL_COUNT:
        raise ValueError('invalid control count')
    for control_id, selection in controls.items():
        if not isinstance(control_id, str) or not _ID.fullmatch(control_id) or control_id not in catalog:
            raise ValueError('unknown validation control')
        if not isinstance(selection, dict) or set(selection) != {'input_action', 'output_action', 'settings'}:
            raise ValueError('invalid control settings envelope')
        descriptor = catalog[control_id]
        for stage in ('input', 'output'):
            action = selection[stage + '_action']
            if action is not None and (not isinstance(action, str) or action not in descriptor.actions or stage not in descriptor.stages):
                raise ValueError('unsupported stage or action')
        if not isinstance(selection['settings'], dict):
            raise ValueError('settings must be an object')
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(encoded.encode('utf-8')) > MAX_RECORD_BYTES:
        raise ValueError('configuration exceeds supported size')
    return json.loads(encoded)


def migration_preview(record_or_payload, catalog):
    """Pure/idempotent preview, not a persisted record or alternate executable policy."""
    if isinstance(record_or_payload, dict):
        return validate_payload(deepcopy(record_or_payload), catalog)
    if not isinstance(record_or_payload, TenantRecord):
        raise ValueError('validated tenant record or canonical preview required')
    record = TenantRecord.model_validate(record_or_payload.model_dump())
    if record.default_profile != 'tenant':
        raise ValueError('example-profile tenant requires explicit migration review')
    raw = record.model_dump(mode='json')
    controls = {}
    for control_id in CHECK_IDS:
        action = getattr(record, 'check_' + control_id)
        controls[control_id] = {
            'input_action': action.value if action is not None else None,
            'output_action': action.value if action is not None and control_id not in INPUT_ONLY_CHECK_IDS else None,
            'settings': {name: raw[name] for name in SETTINGS_OF.get(control_id, ())},
        }
    return validate_payload({'tenant_id': record.tenant_id, 'format_version': 1, 'catalog_version': CATALOG_VERSION,
        'record_revision': record.revision, 'validations': controls}, catalog)


def update_preview(payload, changes, catalog, *, trusted_tenant_id, expected_revision):
    """Pure optimistic-concurrency/owner contract; writes no tenant store."""
    current = validate_payload(payload, catalog)
    if (current['tenant_id'] != trusted_tenant_id or type(expected_revision) is not int
            or current['record_revision'] != expected_revision):
        raise ValueError('preview owner or revision conflict')
    if not isinstance(changes, dict) or not changes or set(changes) - set(current['validations']):
        raise ValueError('invalid preview changes')
    current['validations'].update(deepcopy(changes))
    current['record_revision'] += 1
    return validate_payload(current, catalog)
