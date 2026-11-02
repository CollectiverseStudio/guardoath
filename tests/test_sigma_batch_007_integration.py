"""Promote exact reviewed candidates without expanding an event's work budget."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guardoath.sigma_rules import UnsupportedSigmaRule, import_rule
from guardoath.sigma_rules import budgets, routing
from guardoath.sigma_rules.evidence_catalog import BATCH_NAMES, load_evidence_catalog
from guardoath.sigma_rules.events import evaluate_event

ROOT = Path(__file__).resolve().parents[1]/'guardoath/sigma_rules'
HISTORICAL = [json.loads((ROOT/f'upstream_batch_{n:03d}.json').read_text(encoding='utf-8')) for n in range(1,7)]
BASELINE = tuple(import_rule(row,row['source_path'],source_revision=batch['source_revision'])
                  for batch in HISTORICAL for row in batch['rules'])
OLD_ROWS = [row for batch in HISTORICAL for row in batch['rules']]
DATA = json.loads((ROOT/'upstream_batch_007.json').read_text(encoding='utf-8'))
CANDIDATE = json.loads((ROOT/'candidates/batch_007.json').read_text(encoding='utf-8'))
TABLE = routing.load_routing_table()
SCRIPT = {'product':'windows','category':'ps_script'}
PROCESS = {'product':'windows','category':'process_creation'}


def test_adoption_retains_every_candidate_row_snapshot_and_historical_baseline():
    assert len(BASELINE) == 128  # Actual prior release, never the enlarged event pool.
    assert DATA['rules'] == CANDIDATE['rules'] and len(DATA['rules']) == 25
    assert DATA['integration_status'] == 'offline-default-source-only'
    assert CANDIDATE['integration_status'] == 'staged-not-default'
    assert DATA['candidate_origin_commit'] == '95eca0339f2313690babb83c8d236064d1ee140f'
    assert BATCH_NAMES[-1] == 'upstream_batch_007.json' and len(BATCH_NAMES) == 7
    assert len(TABLE.rule_ids) == len(load_evidence_catalog()) == 153
    assert len(set(TABLE.rule_ids)) == 153
    for row in DATA['rules']:
        name = Path(row['source_path']).name
        assert (ROOT/'upstream/batch_007'/name).read_bytes() == (ROOT/'candidates/batch_007'/name).read_bytes()


@pytest.mark.parametrize('row',OLD_ROWS,ids=lambda r:r['id'])
@pytest.mark.parametrize('key',['positive_event','negative_event'])
def test_all_prior_fixture_findings_are_exactly_preserved_after_default_adoption(row,key):
    # Compare releases, not chunks of a current event: the old immutable pool is
    # 128, while the new evaluator receives one host-selected source group only.
    event = deepcopy(row[key]); before = deepcopy(event)
    expected = evaluate_event(BASELINE,event)
    source = {k:row['logsource'][k] for k in ('product','category')}
    assert TABLE.evaluate(event,trusted_logsource=source,allowed_rule_ids=TABLE.rule_ids) == expected
    assert event == before


@pytest.mark.parametrize('source,count',[(SCRIPT,125),(PROCESS,28)])
def test_default_153_inventory_uses_one_bounded_source_call(monkeypatch,source,count):
    real = routing.evaluate_event; calls = []
    def spy(selected,event,**kwargs):
        assert budgets._CURRENT.get() is not None
        calls.append(tuple(rule.rule_id for rule in selected))
        return real(selected,event,**kwargs)
    monkeypatch.setattr(routing,'evaluate_event',spy)
    plan = TABLE.plan(trusted_logsource=source,allowed_rule_ids=TABLE.rule_ids)
    assert len(plan.selected_rule_ids) == count and len(plan.excluded_rule_ids) == 153-count
    assert TABLE.evaluate({},trusted_logsource=source,allowed_rule_ids=TABLE.rule_ids) == []
    assert calls == [plan.selected_rule_ids]


def test_real_mixed_payload_cannot_select_both_sources_or_bypass_event_limit():
    event = {'ScriptBlockText':'Get-ADReplAccount -All -Server lab ',
             'Image':r'C:\Windows\Microsoft.NET\Framework\v4.0.30319\aspnet_compiler.exe',
             'CommandLine':r'aspnet_compiler.exe C:\Users\Public\fixture',
             'product':'windows','category':'event-controlled'}
    script = TABLE.evaluate(event,trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids)
    process = TABLE.evaluate(event,trusted_logsource=PROCESS,allowed_rule_ids=TABLE.rule_ids)
    assert {r['rule_id'] for r in script} == {'846c7a87-8e14-4569-9d49-ecfd4276a01c','060c3ef1-fd0a-4091-bf46-e7d625f60b73'}
    assert {r['rule_id'] for r in process} == {'a01b8329-5953-4f73-ae2d-aa01e1f35f00','9f50fe98-fe5c-4a2d-86c7-fad7f63ed622'}
    with pytest.raises(UnsupportedSigmaRule,match='128'):
        evaluate_event(tuple(TABLE._rules.values()),event)
