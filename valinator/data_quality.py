"""Bounded local data contracts; no financial, legal, provenance or execution claims."""
import base64
import binascii
from collections import deque
from datetime import datetime, timezone
import hashlib
import hmac
import re
from typing import Any, Literal

from pydantic import Field, ValidationError

from valinator.formats import FORMAT_CHECKER
from valinator.json_data import parse_json
from valinator.models import Contract, CheckerResult, Finding

ID = r'^[A-Za-z0-9_.-]{1,80}$'


def result(checker, status, code=None, findings=()):
    return CheckerResult(checker_id=checker, checker_version='1', status=status, error_code=code, findings=findings)


def finding(rule, category, message):
    return Finding(rule_id=rule, category=category, severity='high', explanation=message)


class LedgerEntry(Contract):
    entry_id: str = Field(pattern=ID, strict=True)
    side: Literal['debit', 'credit']
    amount: str = Field(min_length=1, max_length=17, strict=True)


class LedgerDocument(Contract):
    currency: str = Field(pattern=r'^[A-Z]{3}$', min_length=3, max_length=3, strict=True)
    entries: tuple[LedgerEntry, ...] = Field(min_length=2, max_length=200)


async def ledger_check(request, policy, stage):
    if policy.ledger is None or request.content_format != 'json':
        return result('ledger', 'skipped', 'ledger_json_required')
    try:
        document = LedgerDocument.model_validate(parse_json(request.content))
        config = policy.ledger
        if len(document.entries) > config.max_entries:
            return result('ledger', 'error', 'ledger_entry_limit')
        if len({entry.entry_id for entry in document.entries}) != len(document.entries):
            return result('ledger', 'error', 'ledger_duplicate_entry')
        if {entry.side for entry in document.entries} != {'debit', 'credit'}:
            return result('ledger', 'error', 'ledger_sides_required')
        scale = config.decimal_places
        pattern = r'(0|[1-9][0-9]{0,11})' + (r'\.([0-9]{' + str(scale) + r'})' if scale else '')
        totals = {'debit':0, 'credit':0}
        for entry in document.entries:
            match = re.fullmatch(pattern, entry.amount, flags=re.ASCII)
            if not match:
                return result('ledger', 'error', 'ledger_invalid_amount')
            # Bounded digit strings -> exact integer minor units. No floating
            # point, Decimal-context arithmetic, rounding or row coercion.
            minor_units = int(match[1]) * 10**scale + (int(match[2]) if scale else 0)
            totals[entry.side] += minor_units
    except (ValueError, RecursionError, ValidationError):
        return result('ledger', 'error', 'ledger_invalid_document')
    flags = []
    if document.currency != config.currency:
        flags.append(finding('business.ledger_currency', 'business', 'The submitted ledger unit does not match the configured currency code. No conversion was performed.'))
    if totals['debit'] != totals['credit']:
        flags.append(finding('business.ledger_unbalanced', 'business', 'Submitted debit and credit line items do not balance at the configured decimal scale. This is arithmetic validation, not fraud detection or account verification.'))
    return result('ledger', 'flagged' if flags else 'passed', findings=tuple(flags))


class TimeSample(Contract):
    timestamp: str = Field(min_length=20, max_length=35, strict=True)
    value: Any


class TimeDocument(Contract):
    samples: tuple[TimeSample, ...] = Field(min_length=2, max_length=500)


STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})')


def instant(text):
    if not STAMP.fullmatch(text) or text.endswith('-00:00') or not FORMAT_CHECKER.conforms(text, 'date-time'):
        raise ValueError('Unsupported timestamp')
    return datetime.fromisoformat(text.upper().replace('Z', '+00:00')).astimezone(timezone.utc)


async def timestamps_check(request, policy, stage):
    if policy.timestamps is None or request.content_format != 'json':
        return result('timestamps', 'skipped', 'timestamps_json_required')
    try:
        document = TimeDocument.model_validate(parse_json(request.content))
        if len(document.samples) > policy.timestamps.max_samples:
            return result('timestamps', 'error', 'timestamps_sample_limit')
        # Do not round arbitrary large integers into floats or allow bool values.
        if any(type(row.value) not in (int, float) for row in document.samples):
            raise ValueError('Invalid sample value')
        points = [instant(sample.timestamp) for sample in document.samples]
    except (ValueError, RecursionError, ValidationError, OverflowError):
        return result('timestamps', 'error', 'timestamps_invalid_document')
    valid = all(a <= b if policy.timestamps.allow_equal else a < b for a,b in zip(points,points[1:]))
    return result('timestamps', 'passed' if valid else 'flagged', findings=() if valid else (
        finding('structure.timestamp_order', 'structure', 'Submitted timestamps are not in the configured chronological order after offset normalization. Samples were not sorted, smoothed or changed; freshness and source authenticity were not assessed.'),))


class GraphDocument(Contract):
    nodes: tuple[str, ...] = Field(max_length=200)
    edges: tuple[tuple[str, str], ...] = Field(max_length=1_000)


async def graph_check(request, policy, stage):
    if policy.graph is None or request.content_format != 'json':
        return result('graph', 'skipped', 'graph_json_required')
    try:
        document = GraphDocument.model_validate(parse_json(request.content))
        if len(document.nodes) > policy.graph.max_nodes or len(document.edges) > policy.graph.max_edges:
            return result('graph', 'error', 'graph_size_limit')
        if any(not re.fullmatch(ID, node, re.ASCII) for node in document.nodes) or any(not re.fullmatch(ID, node, re.ASCII) for edge in document.edges for node in edge):
            raise ValueError('Invalid graph ID')
        if len(set(document.nodes)) != len(document.nodes):
            return result('graph', 'error', 'graph_duplicate_node')
    except (ValueError, RecursionError, ValidationError):
        return result('graph', 'error', 'graph_invalid_document')
    nodes, edges = set(document.nodes), set(document.edges)
    flags = []
    if len(edges) != len(document.edges):
        flags.append(finding('structure.graph_duplicate_edge','structure','The submitted directed graph repeats an edge. Relationship meaning and authorization are not assessed.'))
    if any(a not in nodes or b not in nodes for a,b in edges):
        flags.append(finding('structure.graph_unknown_node','structure','A submitted edge refers to a node outside the declared graph. No external graph database was queried.'))
    else:
        outgoing = {node:[] for node in nodes}
        degree = dict.fromkeys(nodes,0)
        for a,b in edges:
            outgoing[a].append(b); degree[b] += 1
        ready = deque(node for node in nodes if degree[node] == 0)
        visited = 0
        while ready:
            node = ready.popleft(); visited += 1
            for child in outgoing[node]:
                degree[child] -= 1
                if degree[child] == 0:ready.append(child)
        if visited != len(nodes):
            flags.append(finding('structure.graph_cycle','structure','The submitted directed graph contains a cycle, including possible self-loops. This is a structural check, not legal, dependency-version or execution-safety validation.'))
    return result('graph','flagged' if flags else 'passed',findings=tuple(flags))


async def integrity_check(request, policy, stage):
    if policy.integrity is None:
        return result('integrity','skipped','integrity_configuration_required')
    try:
        raw = request.content.encode('utf-8') if policy.integrity.encoding == 'utf8' else base64.b64decode(request.content,validate=True)
    except (ValueError, UnicodeError, binascii.Error):
        return result('integrity','error','integrity_invalid_encoding')
    if len(raw) > policy.integrity.max_bytes:
        return result('integrity','error','integrity_byte_limit')
    matched = hmac.compare_digest(hashlib.sha256(raw).hexdigest(),policy.integrity.sha256)
    return result('integrity','passed' if matched else 'flagged',findings=() if matched else (
        finding('structure.integrity_digest_mismatch','structure','The submitted bytes do not match the trusted policy SHA-256 digest. A matching digest would not establish source authenticity, data safety or absence of poisoning.'),))


for checker in [ledger_check,timestamps_check,graph_check,integrity_check]:
    checker.checker_version = '1'
