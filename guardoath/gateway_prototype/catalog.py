"""Descriptive catalog/migration experiment; never a runtime policy authority."""
from copy import deepcopy
from dataclasses import dataclass
import json
import re

from guardoath.engine import Engine
from guardoath.models import Action, Policy
from guardoath.tenant_record import (CHECK_IDS, REQUIRED_SETTINGS,
                                    SETTINGS_OF, TENANT_ID_PATTERN, TenantRecord)

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
        stages=('input', 'output'),
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


def _validated_settings(control_id, selection, owner):
    settings = selection['settings']
    allowed = SETTINGS_OF.get(control_id, ())
    if set(settings) - set(allowed):
        raise ValueError('unknown settings for validation control')
    if control_id not in CHECK_IDS:
        # Synthetic schema-scale descriptors do not publish a settings schema.
        return {}
    required = REQUIRED_SETTINGS.get(control_id, ())
    enabled = any(selection[stage + '_action'] is not None for stage in ('input', 'output'))
    # This ephemeral policy checks configuration only. No checker is executed,
    # and it is never used as runtime selection/action authority.
    rules = [{'checker_id': control_id, 'stages': ['input', 'output']}]
    try:
        policy = Policy.model_validate_json(json.dumps({
            'application_id': owner, 'version': 'catalog-settings-validation',
            'rules': rules, **settings,
        }, allow_nan=False), strict=True)
        configured = any(getattr(policy, name) for name in required)
        if enabled and required and not configured:
            raise ValueError('required settings absent')
        if required and not configured:
            policy = policy.model_copy(update={'rules': ()})
        Engine([policy], {})  # Source schema/reference/format and consistency checks.
    except Exception:
        raise ValueError('invalid source-derived validation settings') from None
    normalized = policy.model_dump(mode='json')
    return {name: normalized[name] for name in settings}


def _validated_rule_actions(actions, controls, owner):
    """Reuse persisted schema/registry and PII-redaction constraints, without writes."""
    pii = controls.get('pii')
    redacts = isinstance(pii, dict) and any(
        pii.get(stage + '_action') == 'redact' for stage in ('input', 'output'))
    try:
        record = TenantRecord.model_validate({
            'tenant_id': owner, 'display_name': 'Migration preview validation',
            'input_check_secrets': Action.ALLOW,
            'input_check_pii': Action.REDACT if redacts else None,
            'input_rule_actions': actions,
        }, strict=True)
    except Exception:
        raise ValueError('invalid source-derived per-rule settings') from None
    return dict(record.input_rule_actions)


def validate_payload(payload, catalog):
    required = {'tenant_id', 'format_version', 'catalog_version', 'record_revision', 'validations'}
    if (not isinstance(payload, dict) or not required.issubset(payload)
            or set(payload) - required - {'rule_actions'}):
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
    # Validate before JSON serialization can coerce non-string object keys.
    rule_actions = _validated_rule_actions(payload.get('rule_actions', {}), controls, payload['tenant_id'])
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError('configuration must contain finite JSON data') from None
    if len(encoded.encode('utf-8')) > MAX_RECORD_BYTES:
        raise ValueError('configuration exceeds supported size')
    # Validate/normalize a bounded copy; caller-owned migration data is immutable.
    normalized = json.loads(encoded)
    normalized['rule_actions'] = rule_actions
    controls = normalized['validations']
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
        selection['settings'] = _validated_settings(control_id, selection, payload['tenant_id'])
    if len(json.dumps(normalized, separators=(',', ':'), allow_nan=False).encode('utf-8')) > MAX_RECORD_BYTES:
        raise ValueError('normalized configuration exceeds supported size')
    return normalized


def migration_preview(record_or_payload, catalog):
    """Pure/idempotent preview, not a persisted record or alternate executable policy."""
    if isinstance(record_or_payload, dict):
        return validate_payload(deepcopy(record_or_payload), catalog)
    if not isinstance(record_or_payload, TenantRecord):
        raise ValueError('validated tenant record or canonical preview required')
    # A customer record that still names a retired example profile is refused when it is read
    # (TenantRecord.retired_profile_field), so it reaches review instead of a silent migration.
    record = TenantRecord.model_validate(record_or_payload.model_dump())
    raw = record.model_dump(mode='json')
    controls = {}
    for control_id in CHECK_IDS:
        actions = {stage: record.action(control_id, stage) for stage in ('input', 'output')}
        controls[control_id] = {
            'input_action': actions['input'].value if actions['input'] is not None else None,
            'output_action': actions['output'].value if actions['output'] is not None else None,
            'settings': {name: raw[name] for name in SETTINGS_OF.get(control_id, ())},
        }
    # The preview carries one rule map. Records whose input and output rule settings differ (possible since
    # 2026-11-04) are refused rather than previewed with one stage's settings silently dropped.
    if raw['input_rule_actions'] != raw['output_rule_actions']:
        raise ValueError('per-stage rule settings differ; the preview format carries one rule map')
    return validate_payload({'tenant_id': record.tenant_id, 'format_version': 1, 'catalog_version': CATALOG_VERSION,
        'record_revision': record.revision, 'validations': controls, 'rule_actions': raw['input_rule_actions']}, catalog)


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
