"""Trusted-host routing for isolated Sigma events; no hosted or tenant authority."""
from collections.abc import Mapping, Sequence
from importlib.resources import files
import json
from types import MappingProxyType

from . import import_rule
from .budgets import evaluation_budget, remaining_seconds
from .evidence_catalog import BATCH_NAMES, MAX_BATCH_BYTES, build_evidence_catalog, _unique
from .events import evaluate_event
from .matcher import UnsupportedSigmaRule

_SUPPORTED = {('windows', 'process_creation'), ('windows', 'ps_script')}


def _source(value):
    if (not isinstance(value, Mapping) or set(value) != {'product', 'category'}
            or any(not isinstance(value[k], str) for k in value)):
        raise UnsupportedSigmaRule('trusted logsource requires product and category only')
    result = value['product'], value['category']
    if result not in _SUPPORTED:
        raise UnsupportedSigmaRule('unsupported trusted logsource')
    return result


class RoutingTable:
    """Prepare source data once; caller supplies authenticated host configuration.

    This object does not authenticate or authorize the caller. Never populate
    trusted_logsource or allowed_rule_ids from untrusted event/request fields.
    Source metadata and imported detections are copied at construction.
    """
    __slots__ = ('_rules', '_sources', '_descriptors')

    def __init__(self, batches):
        evidence = build_evidence_catalog(batches)
        rules, sources, descriptors = {}, {}, {}
        for batch in batches:
            for record in batch['rules']:
                source = record['logsource']
                # Definitions are prerequisites/documentation; other selectors,
                # such as service, need explicit support rather than being lost.
                if set(source) - {'product', 'category', 'definition'}:
                    raise UnsupportedSigmaRule('unsupported rule logsource selector')
                key = _source({name:source[name] for name in ('product','category')})
                rule = import_rule(record,record['source_path'],source_revision=batch.get('source_revision'))
                rules[rule.rule_id] = rule
                sources[rule.rule_id] = key
                descriptors[rule.rule_id] = tuple(sorted(source.items()))
        if len(rules) != len(evidence):
            raise UnsupportedSigmaRule('inconsistent source inventory')
        self._rules = MappingProxyType(rules)
        self._sources = MappingProxyType(sources)
        self._descriptors = MappingProxyType(descriptors)

    @property
    def rule_ids(self):
        return tuple(self._rules)

    def source_for(self, rule_id):
        """Fresh source descriptor; original definition is retained."""
        if not isinstance(rule_id,str) or rule_id not in self._descriptors:
            raise UnsupportedSigmaRule('unknown source rule')
        return dict(self._descriptors[rule_id])

    def evaluate(self, event, *, trusted_logsource, allowed_rule_ids,
                 max_workers=4, timeout_seconds=1.0):
        """One event, one aggregate budget; no empty/partial success on bad routing."""
        with evaluation_budget(max_workers=max_workers,timeout_seconds=timeout_seconds):
            source = _source(trusted_logsource)
            if (not isinstance(allowed_rule_ids,Sequence) or isinstance(allowed_rule_ids,(str,bytes))
                    or not 1 <= len(allowed_rule_ids) <= len(self._rules)):
                raise UnsupportedSigmaRule('nonempty bounded configured rule IDs required')
            selected, seen = [], set()
            for identifier in allowed_rule_ids:
                remaining_seconds()
                if not isinstance(identifier,str) or identifier not in self._rules:
                    raise UnsupportedSigmaRule('unknown configured rule ID')
                if identifier in seen:
                    raise UnsupportedSigmaRule('duplicate configured rule ID')
                seen.add(identifier)
                if self._sources[identifier] == source:
                    selected.append(self._rules[identifier])
            if not selected:
                raise UnsupportedSigmaRule('no configured rules for trusted logsource')
            if len(selected) > 128:
                raise UnsupportedSigmaRule('routed event exceeds 128-rule limit')
            # The existing evaluator inherits this context. Never loop over
            # separate evaluations or restart a budget for a chunk or retry.
            return evaluate_event(selected,event,max_workers=max_workers,timeout_seconds=timeout_seconds)


def load_routing_table():
    """Load the fixed shipped batches; missing assets fail without a fallback."""
    from .evidence_catalog import load_evidence_catalog
    # Verify shipped source snapshots before preparing a usable table.
    load_evidence_catalog()
    root = files('guardoath.sigma_rules')
    batches = []
    for name in BATCH_NAMES:
        raw = root.joinpath(name).read_bytes()
        if len(raw) > MAX_BATCH_BYTES:
            raise UnsupportedSigmaRule('source batch exceeds size limit')
        batches.append(json.loads(raw.decode('utf-8'),object_pairs_hook=_unique))
    return RoutingTable(batches)
