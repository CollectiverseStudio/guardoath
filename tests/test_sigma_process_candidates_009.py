"""C19 immutable source screening and explicit proposed-pool regression evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from guardoath.sigma_rules import LICENSE, UnsupportedSigmaRule, import_rule
from guardoath.sigma_rules import budgets, routing
from guardoath.sigma_rules.evidence_catalog import BATCH_NAMES, build_evidence_catalog, load_evidence_catalog
from guardoath.sigma_rules.events import evaluate_event

ROOT=Path(__file__).resolve().parents[1]/'guardoath/sigma_rules'
DATA=json.loads((ROOT/'candidates/batch_009.json').read_text(encoding='utf-8'))
REVIEW=json.loads((ROOT/'candidates/batch_009_review.json').read_text(encoding='utf-8'))
ROWS=DATA['rules']
BASE=[json.loads((ROOT/n).read_text(encoding='utf-8')) for n in BATCH_NAMES[:8]]
OLD=[r for b in BASE for r in b['rules']]
ALL=BASE+[DATA]
RULES=tuple(import_rule(r,r['source_path'],source_revision=b['source_revision']) for b in ALL for r in b['rules'])
BY_ID={r.rule_id:r for r in RULES}
TABLE=routing.RoutingTable(ALL)
DEFAULT=routing.load_routing_table()
PRIOR=routing.RoutingTable(BASE)
PROCESS={'product':'windows','category':'process_creation'}
SCRIPT={'product':'windows','category':'ps_script'}
EXTRA=REVIEW['additional_cases']


def findings(event,expected):
    before=deepcopy(event)
    assert {r.rule_id for r in RULES if r.matches(event)}==set(expected)
    actual=TABLE.evaluate(event,trusted_logsource=PROCESS,allowed_rule_ids=TABLE.rule_ids)
    assert {r['rule_id'] for r in actual}==set(expected)
    assert all(r==BY_ID[r['rule_id']].finding() for r in actual)
    assert TABLE.evaluate(event,trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids)==[]
    assert event==before


def test_frozen_review_and_separate_default_adoption_retain_exact_source():
    assert DATA['integration_status']=='staged-not-default'
    assert DATA['reviewed_predecessor_count']==len(OLD)==178
    assert len(DEFAULT.rule_ids)==228
    assert len(ROWS)==len({r['id'] for r in ROWS})==len({r['source_path'] for r in ROWS})==25
    assert {r['id'] for r in ROWS}.issubset(DEFAULT.rule_ids)
    assert len(RULES)==len(BY_ID)==len(build_evidence_catalog(ALL))==203
    assert len(load_evidence_catalog())==228 and len(BATCH_NAMES)==10
    assert DATA['license']==LICENSE
    assert DATA['source_revision']==REVIEW['source_revision']=='05d1031f537e830b76da53bae2400c5814c89165'
    assert REVIEW['runtime_registered'] is False
    assert len(EXTRA)==len({c['case_id'] for c in EXTRA})==176


@pytest.mark.parametrize('row',ROWS,ids=lambda r:r['id'])
def test_full_original_yaml_metadata_detection_and_findings_are_retained(row):
    raw=(ROOT/'candidates/batch_009'/Path(row['source_path']).name).read_text(encoding='utf-8').encode()
    assert hashlib.sha256(raw).hexdigest()==row['source_sha256']
    assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==row['source_git_blob']
    source=json.loads(json.dumps(yaml.safe_load(raw),default=str))
    extra={'source_path','source_sha256','source_git_blob','positive_event','negative_event'}
    assert {k:v for k,v in row.items() if k not in extra}==source
    assert TABLE.source_for(row['id'])==source['logsource']==PROCESS
    assert BY_ID[row['id']].finding()=={'rule_id':row['id'],'title':row['title'],'author':row['author'],
        'license':LICENSE,'source_url':f"https://github.com/SigmaHQ/sigma/blob/{DATA['source_revision']}/{row['source_path']}"}


@pytest.mark.parametrize('row',ROWS,ids=lambda r:r['id'])
def test_primary_positive_lookalike_missing_and_case_semantics(row):
    rule=BY_ID[row['id']]
    assert rule.matches(row['positive_event']) and not rule.matches(row['negative_event'])
    assert rule.matches({k:v.swapcase() for k,v in row['positive_event'].items()})
    assert not rule.matches({})
    assert not rule.matches({k.lower():v for k,v in row['positive_event'].items()})
    assert not rule.matches({'ScriptBlockText':' '.join(row['positive_event'].values())})
    for field in row['positive_event']:
        event=deepcopy(row['positive_event']);del event[field]
        assert not rule.matches(event)


@pytest.mark.parametrize('row',ROWS,ids=lambda r:r['id'])
@pytest.mark.parametrize('value',[None,False,0,[],{},['fixture']])
def test_malformed_field_values_do_not_match(row,value):
    assert not BY_ID[row['id']].matches({k:value for k in row['positive_event']})


@pytest.mark.parametrize('row',ROWS,ids=lambda r:r['id'])
@pytest.mark.parametrize('event',[None,[],False,42,'fixture'])
def test_nonmapping_containers_raise(row,event):
    with pytest.raises(UnsupportedSigmaRule,match='mapping'):BY_ID[row['id']].matches(event)


@pytest.mark.parametrize('row',ROWS,ids=lambda r:r['id'])
@pytest.mark.parametrize('key',['positive_event','negative_event'])
def test_complete_proposed_pool_findings_and_source_isolation(row,key):
    expected=REVIEW['candidate_outcomes'][row['id']][key]
    assert expected==([row['id']] if key=='positive_event' else [])
    findings(row[key],expected)


@pytest.mark.parametrize('case',EXTRA,ids=lambda c:c['case_id'])
def test_each_alternative_and_conjunction_near_miss_keeps_explicit_outcome(case):
    target=case['rule_id'] in case['expected_rule_ids']
    assert target is (case['kind']=='alternative')
    assert BY_ID[case['rule_id']].matches(case['event']) is target
    findings(case['event'],case['expected_rule_ids'])
    findings({k:v.swapcase() for k,v in case['event'].items()},case['expected_rule_ids'])


@pytest.mark.parametrize('row',OLD,ids=lambda r:r['id'])
@pytest.mark.parametrize('key',['positive_event','negative_event'])
def test_all_178_predecessor_fixture_findings_are_preserved(row,key):
    source={k:row['logsource'][k] for k in ('product','category')}
    expected=PRIOR.evaluate(row[key],trusted_logsource=source,allowed_rule_ids=PRIOR.rule_ids)
    assert REVIEW['prior_fixture_new_matches'].get(row['id']+'.'+key,[])==[]
    assert TABLE.evaluate(row[key],trusted_logsource=source,allowed_rule_ids=TABLE.rule_ids)==expected


@pytest.mark.parametrize('row',ROWS,ids=lambda r:r['id'])
def test_default_adoption_selects_exact_reviewed_id(row):
    plan=DEFAULT.plan(trusted_logsource=PROCESS,allowed_rule_ids=[row['id']])
    assert plan.selected_rule_ids==(row['id'],) and plan.excluded_rule_ids==()


def test_hash_aliases_retain_both_upstream_signals_instead_of_merging_ids():
    overlaps=[c for c in EXTRA if len(c['expected_rule_ids'])>1]
    assert len(overlaps)==2
    broad='24e3e58a-646b-4b50-adef-02ef935b9fc8'
    assert {frozenset(c['expected_rule_ids']) for c in overlaps}=={
        frozenset([broad,'36d88494-1d43-4dc0-b3fa-35c8fea0ca9d']),
        frozenset([broad,'2704ab9e-afe2-4854-a3b1-0c0706d03578'])}
    # The literals (including upstream short/atypical hashes) are never padded,
    # rewritten or converted into an authenticity/identity claim.
    assert all(c['source_field']=='Hashes|contains' for c in overlaps)


def test_203_pool_remains_source_bounded_and_uses_one_inherited_evaluation(monkeypatch):
    process=TABLE.plan(trusted_logsource=PROCESS,allowed_rule_ids=TABLE.rule_ids)
    script=TABLE.plan(trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids)
    assert len(process.selected_rule_ids)==78 and len(process.excluded_rule_ids)==125
    assert len(script.selected_rule_ids)==125 and len(script.excluded_rule_ids)==78
    assert not set(process.selected_rule_ids)&set(script.selected_rule_ids)
    with pytest.raises(UnsupportedSigmaRule,match='128'):evaluate_event(RULES,{})
    real=routing.evaluate_event;calls=[]
    def spy(selected,event,**args):
        assert budgets._CURRENT.get() is not None
        calls.append(tuple(r.rule_id for r in selected));return real(selected,event,**args)
    monkeypatch.setattr(routing,'evaluate_event',spy)
    assert TABLE.evaluate({},trusted_logsource=PROCESS,allowed_rule_ids=TABLE.rule_ids)==[]
    assert calls==[process.selected_rule_ids]


@pytest.mark.parametrize('row',ROWS,ids=lambda r:r['id'])
@pytest.mark.parametrize('modifier',['windash','base64','expand'])
def test_unsupported_unused_branch_is_rejected_not_approximated(row,modifier):
    altered=deepcopy(row);altered['detection']['unused']={'CommandLine|'+modifier:'fixture'}
    with pytest.raises(UnsupportedSigmaRule):import_rule(altered,altered['source_path'])
