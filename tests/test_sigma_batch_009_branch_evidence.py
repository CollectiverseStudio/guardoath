"""C21 exact literal branch contracts without changing imported definitions."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from guardoath.sigma_rules import import_rule, UnsupportedSigmaRule
from guardoath.sigma_rules.evidence_catalog import BATCH_NAMES
from guardoath.sigma_rules.routing import RoutingTable
from guardoath.sigma_rules import budgets, routing

ROOT=Path(__file__).resolve().parents[1]/'guardoath/sigma_rules'
DATA=json.loads((ROOT/'candidates/branch_009_evidence.json').read_text(encoding='utf-8'))
BATCHES=[json.loads((ROOT/n).read_text(encoding='utf-8')) for n in BATCH_NAMES]
ROWS={r['id']:r for b in BATCHES for r in b['rules']}
RULES={r['id']:import_rule(r,r['source_path'],source_revision=b['source_revision']) for b in BATCHES for r in b['rules']}
TABLE=RoutingTable(BATCHES)
PROCESS={'product':'windows','category':'process_creation'}
SCRIPT={'product':'windows','category':'ps_script'}
GROUPS=DATA['groups']
CASES=[(g,c) for g in GROUPS for c in g['cases']]


def test_exact_gap_inventory_and_immutable_source_provenance():
    assert DATA['runtime_registered'] is False and DATA['reviewed_definition_count']==203
    assert len(ROWS)==len(TABLE.rule_ids)==228
    assert {g['gap_id'] for g in GROUPS}=={'C21-SC01','C21-SC02','C21-SC03','C21-HY01','C21-HY02'}
    assert len(CASES)==38
    assert {g['rule_id'] for g in GROUPS}=={'56c217c3-2de2-479b-990f-5c109ba8458f','aaafa146-074c-11eb-adc1-0242ac120002'}
    for g in GROUPS:
        row=ROWS[g['rule_id']]
        for field in ('source_path','source_sha256','source_git_blob'):assert g[field]==row[field]
        raw=(ROOT/'upstream/batch_009'/Path(row['source_path']).name).read_text(encoding='utf-8').encode('utf-8')
        assert hashlib.sha256(raw).hexdigest()==g['source_sha256']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==g['source_git_blob']
        assert DATA['source_revision']==BATCHES[8]['source_revision']=='05d1031f537e830b76da53bae2400c5814c89165'
        assert len({c['case_id'] for c in g['cases']})==len(g['cases'])


@pytest.mark.parametrize('g,c',CASES,ids=[g['gap_id']+'-'+c['case_id'] for g,c in CASES])
def test_full_228_findings_source_provenance_and_case_insensitivity(g,c):
    event=deepcopy(c['event']);row=deepcopy(ROWS[g['rule_id']])
    assert RULES[g['rule_id']].matches(event) is c['target_matches']
    assert (g['rule_id'] in c['expected_rule_ids']) is c['target_matches']
    assert len(set(c['expected_rule_ids']))==len(c['expected_rule_ids'])
    assert {r.rule_id for r in RULES.values() if r.matches(event)}==set(c['expected_rule_ids'])
    actual=TABLE.evaluate(event,trusted_logsource=PROCESS,allowed_rule_ids=TABLE.rule_ids)
    assert {f['rule_id'] for f in actual}==set(c['expected_rule_ids'])
    assert all(f==RULES[f['rule_id']].finding() for f in actual)
    assert TABLE.evaluate(event,trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids)==[]
    swapped={k:v.swapcase() for k,v in event.items()}
    assert {f['rule_id'] for f in TABLE.evaluate(swapped,trusted_logsource=PROCESS,allowed_rule_ids=TABLE.rule_ids)}==set(c['expected_rule_ids'])
    assert event==c['event'] and row==ROWS[g['rule_id']]


MALFORMED=[(g,key) for g in GROUPS for key in next(c['event'] for c in g['cases'] if c['case_id']=='positive')]
@pytest.mark.parametrize('g,field',MALFORMED,ids=[g['gap_id']+'-'+key for g,key in MALFORMED])
@pytest.mark.parametrize('value',[None,False,7,[],{},['bad']])
def test_every_branch_essential_field_is_fail_closed(g,field,value):
    event=deepcopy(next(c['event'] for c in g['cases'] if c['case_id']=='positive'))
    event[field]=value
    assert not RULES[g['rule_id']].matches(event)
    if isinstance(value,(list,dict)):
        with pytest.raises(UnsupportedSigmaRule):TABLE.evaluate(event,trusted_logsource=PROCESS,allowed_rule_ids=TABLE.rule_ids)
    else:
        assert g['rule_id'] not in {f['rule_id'] for f in TABLE.evaluate(event,trusted_logsource=PROCESS,allowed_rule_ids=TABLE.rule_ids)}


def test_schedule_cross_product_and_hydra_counterfactuals_are_literal_not_tokenized():
    for g,mode in zip(GROUPS[:3],('/SC DAILY /ST','/SC ONIDLE','/SC HOURLY')):
        by={c['case_id']:c for c in g['cases']};e=by['positive']['event']
        assert e['ParentImage'].endswith('\\pwsh.exe') and mode in e['CommandLine']
        assert by['suffix-remains-contained']['target_matches'] is True
        assert by['suffix-remains-contained']['event']['CommandLine'].endswith('_EXTRA')
    for g in GROUPS[3:]:
        by={c['case_id']:c for c in g['cases']}
        assert '^PASS^' in by['missing-switch']['event']['CommandLine']
        assert by['positive']['target_matches'] is True
        assert all(not c['target_matches'] for c in g['cases'] if c['case_id']!='positive')


def test_one_inherited_budget_and_source_group_call_is_preserved(monkeypatch):
    real=routing.evaluate_event;calls=[]
    def spy(selected,event,**kwargs):
        assert budgets._CURRENT.get() is not None
        calls.append(tuple(r.rule_id for r in selected))
        return real(selected,event,**kwargs)
    monkeypatch.setattr(routing,'evaluate_event',spy)
    plan=TABLE.plan(trusted_logsource=PROCESS,allowed_rule_ids=TABLE.rule_ids)
    assert len(plan.selected_rule_ids)==103
    assert len(TABLE.plan(trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids).selected_rule_ids)==125
    TABLE.evaluate({},trusted_logsource=PROCESS,allowed_rule_ids=TABLE.rule_ids)
    assert calls==[plan.selected_rule_ids]
