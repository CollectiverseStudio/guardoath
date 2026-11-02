"""Trusted routing stays separate from untrusted event data and tenant authority."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from guardoath.sigma_rules import LICENSE, SigmaBudgetExceeded, SigmaEvaluationError, UnsupportedSigmaRule, import_rule
from guardoath.sigma_rules import bounded_regex, budgets, routing
from guardoath.sigma_rules.evidence_catalog import BATCH_NAMES, EvidenceCatalogError

ROOT=Path(__file__).resolve().parents[1]/'guardoath/sigma_rules'
BATCHES=[json.loads((ROOT/name).read_text(encoding='utf-8')) for name in BATCH_NAMES]
RECORDS=[(b,r) for b in BATCHES for r in b['rules']]
TABLE=routing.load_routing_table()
SCRIPT={'product':'windows','category':'ps_script'}
PROCESS={'product':'windows','category':'process_creation'}


def source(row):return {key:row['logsource'][key] for key in ('product','category')}


@pytest.mark.parametrize('batch,row',RECORDS,ids=[r['id'] for _,r in RECORDS])
def test_all_real_positive_fixtures_keep_pinned_finding_and_source(batch,row):
    findings=TABLE.evaluate(row['positive_event'],trusted_logsource=source(row),allowed_rule_ids=[row['id']])
    assert findings==[import_rule(row,row['source_path'],source_revision=batch.get('source_revision')).finding()]
    assert TABLE.source_for(row['id'])==row['logsource']


@pytest.mark.parametrize('batch,row',RECORDS,ids=[r['id'] for _,r in RECORDS])
def test_all_real_lookalikes_and_missing_fields_are_nonmatches(batch,row):
    for event in (row['negative_event'],{}, {key:None for key in row['positive_event']}):
        assert TABLE.evaluate(event,trusted_logsource=source(row),allowed_rule_ids=[row['id']])==[]


@pytest.mark.parametrize('batch,row',RECORDS,ids=[r['id'] for _,r in RECORDS])
def test_wrong_trusted_source_excludes_rule_instead_of_guessing_from_fields(batch,row):
    wrong=PROCESS if source(row)==SCRIPT else SCRIPT
    with pytest.raises(UnsupportedSigmaRule,match='no configured'):
        TABLE.evaluate(row['positive_event'],trusted_logsource=wrong,allowed_rule_ids=[row['id']])


def test_real_all_rule_route_preserves_overlapping_signals_and_source_isolation():
    findings=TABLE.evaluate({'ScriptBlockText':'Get-ADReplAccount -All -Server lab '},
        trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids)
    assert {r['rule_id'] for r in findings}=={'846c7a87-8e14-4569-9d49-ecfd4276a01c','060c3ef1-fd0a-4091-bf46-e7d625f60b73'}
    assert TABLE.evaluate({'ScriptBlockText':'Get-ADReplAccount -All -Server lab '},
        trusted_logsource=PROCESS,allowed_rule_ids=TABLE.rule_ids)==[]


def test_event_fields_cannot_choose_logsource_or_expand_host_allowed_ids():
    row=next(r for _,r in RECORDS if source(r)==SCRIPT)
    process_ids=[r['id'] for _,r in RECORDS if source(r)==PROCESS]
    event={**row['positive_event'],'product':'windows','category':'ps_script','allowed_rule_ids':row['id']}
    assert TABLE.evaluate(event,trusted_logsource=PROCESS,allowed_rule_ids=process_ids)==[]
    with pytest.raises(UnsupportedSigmaRule,match='no configured'):
        TABLE.evaluate(event,trusted_logsource=SCRIPT,allowed_rule_ids=process_ids)


@pytest.mark.parametrize('value',[None,[],{},'ps_script',{'product':'windows'},
    {'product':'linux','category':'ps_script'},{'product':'Windows','category':'ps_script'},
    {'product':'windows','category':'ps_script','service':'PowerShell'},
    {'product':True,'category':'ps_script'}])
def test_invalid_or_unsupported_host_source_is_explicit_error(value):
    with pytest.raises(UnsupportedSigmaRule):
        TABLE.evaluate({},trusted_logsource=value,allowed_rule_ids=TABLE.rule_ids)


@pytest.mark.parametrize('ids',[None,[],{},'id',[None],['unknown'],[TABLE.rule_ids[0]]*2])
def test_bad_configured_ids_never_become_clean_result(ids):
    with pytest.raises(UnsupportedSigmaRule):TABLE.evaluate({},trusted_logsource=PROCESS,allowed_rule_ids=ids)


def test_unknown_id_is_rejected_even_if_other_ids_have_different_source():
    with pytest.raises(UnsupportedSigmaRule,match='unknown'):
        TABLE.evaluate({},trusted_logsource=SCRIPT,allowed_rule_ids=[TABLE.rule_ids[0],'private-unknown-canary'])


@pytest.mark.parametrize('event',[None,[],False,'text',{1:'text'},{'X':[]},{'X':{}},
    {'X':float('nan')},{'X':'a'*32001},{str(i):'x' for i in range(257)}])
def test_event_shape_bounds_are_preserved(event):
    with pytest.raises(UnsupportedSigmaRule):TABLE.evaluate(event,trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids)


def synthetic_batch(count, *, regex=False, process_count=0):
    # Synthetic budget/shape fixtures only; never source inventory or imported-rule counts.
    rows=[]
    for i in range(count):
        category='process_creation' if i<process_count else 'ps_script'
        rows.append({'id':f'fixture.route.{i}','title':'Synthetic routing fixture',
            'author':'GuardOath test fixture','source_path':f'rules/windows/test/fixture_{i}.yml',
            'logsource':{'product':'windows','category':category},
            'detection':{'selection':{'ScriptBlockText|re' if regex else 'ScriptBlockText|contains':'alpha'},'condition':'selection'},
            'positive_event':{'ScriptBlockText':'alpha'},'negative_event':{'ScriptBlockText':'safe'}})
    return {'source_repository':'https://github.com/SigmaHQ/sigma','license':LICENSE,'rules':rows}


def test_128_routed_rules_work_and_129_are_explicitly_rejected():
    table=routing.RoutingTable([synthetic_batch(129)])
    assert len(table.evaluate({'ScriptBlockText':'alpha'},trusted_logsource=SCRIPT,allowed_rule_ids=table.rule_ids[:128]))==128
    with pytest.raises(UnsupportedSigmaRule,match='128'):
        table.evaluate({},trusted_logsource=SCRIPT,allowed_rule_ids=table.rule_ids)


def test_route_limit_applies_to_selected_source_without_silent_id_truncation():
    table=routing.RoutingTable([synthetic_batch(129,process_count=1)])
    assert len(table.evaluate({'ScriptBlockText':'alpha'},trusted_logsource=SCRIPT,allowed_rule_ids=table.rule_ids))==128
    assert len(table.evaluate({'ScriptBlockText':'alpha'},trusted_logsource=PROCESS,allowed_rule_ids=table.rule_ids))==1


def test_entire_selected_set_uses_one_evaluation_and_one_inherited_budget(monkeypatch):
    real=routing.evaluate_event;calls=[]
    def spy(rules,event,**kwargs):
        calls.append(tuple(r.rule_id for r in rules));assert budgets._CURRENT.get() is not None
        return real(rules,event,**kwargs)
    monkeypatch.setattr(routing,'evaluate_event',spy)
    TABLE.evaluate({},trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids)
    assert len(calls)==1 and len(calls[0])==125


def fake_worker(monkeypatch,*,elapsed=0,answer='1',error=False):
    clock=[0.0];calls=[]
    monkeypatch.setattr(budgets,'monotonic',lambda:clock[0])
    def run(args,**kwargs):
        calls.append(kwargs);clock[0]+=elapsed
        if error:raise OSError('private-worker-canary')
        return subprocess.CompletedProcess(args,0,answer)
    monkeypatch.setattr(bounded_regex.subprocess,'run',run)
    return clock,calls


def test_worker_budget_is_shared_across_routed_rules_and_no_partial_findings(monkeypatch):
    table=routing.RoutingTable([synthetic_batch(3,regex=True)])
    _,calls=fake_worker(monkeypatch)
    with pytest.raises(SigmaBudgetExceeded,match='worker budget'):
        table.evaluate({'ScriptBlockText':'alpha'},trusted_logsource=SCRIPT,allowed_rule_ids=table.rule_ids,max_workers=2)
    assert len(calls)==2


def test_deadline_is_shared_and_late_success_is_not_released(monkeypatch):
    table=routing.RoutingTable([synthetic_batch(2,regex=True)])
    _,calls=fake_worker(monkeypatch,elapsed=.3)
    with pytest.raises(SigmaBudgetExceeded,match='deadline'):
        table.evaluate({'ScriptBlockText':'alpha'},trusted_logsource=SCRIPT,allowed_rule_ids=table.rule_ids,timeout_seconds=.5)
    assert len(calls)==2
    assert [x['timeout'] for x in calls]==pytest.approx([.5,.2])


def test_outer_budget_cannot_be_reset_by_host_settings_and_fresh_events_recover(monkeypatch):
    table=routing.RoutingTable([synthetic_batch(2,regex=True)]);_,calls=fake_worker(monkeypatch)
    with budgets.evaluation_budget(max_workers=1,timeout_seconds=.25):
        with pytest.raises(SigmaBudgetExceeded):
            table.evaluate({'ScriptBlockText':'alpha'},trusted_logsource=SCRIPT,allowed_rule_ids=table.rule_ids,max_workers=16,timeout_seconds=5)
    assert len(calls)==1 and calls[0]['timeout']==pytest.approx(.25)
    assert len(table.evaluate({'ScriptBlockText':'alpha'},trusted_logsource=SCRIPT,allowed_rule_ids=table.rule_ids))==2


def test_later_worker_failure_cannot_return_earlier_literal_finding(monkeypatch):
    batch=synthetic_batch(2);batch['rules'][1]['detection']={'selection':{'ScriptBlockText|re':'alpha'},'condition':'selection'}
    table=routing.RoutingTable([batch]);fake_worker(monkeypatch,error=True)
    with pytest.raises(SigmaEvaluationError) as error:
        table.evaluate({'ScriptBlockText':'alpha'},trusted_logsource=SCRIPT,allowed_rule_ids=table.rule_ids)
    assert 'private-worker-canary' not in str(error.value)


def test_table_copies_source_metadata_and_detections_without_mutating_inputs():
    batches=deepcopy(BATCHES);before=deepcopy(batches);table=routing.RoutingTable(batches)
    assert batches==before
    row=batches[1]['rules'][0];original=deepcopy(row)
    row['logsource']['category']='process_creation';row['detection'].clear()
    descriptor=table.source_for(row['id']);descriptor['category']='chat'
    assert table.source_for(row['id'])==original['logsource']
    assert table.evaluate(original['positive_event'],trusted_logsource=SCRIPT,allowed_rule_ids=[row['id']])


def test_unimplemented_source_selector_is_not_silently_dropped():
    batch=synthetic_batch(1);batch['rules'][0]['logsource']['service']='PowerShell'
    with pytest.raises(UnsupportedSigmaRule,match='selector'):routing.RoutingTable([batch])


def test_duplicate_catalog_and_missing_packaged_assets_fail_before_use(monkeypatch):
    batch=synthetic_batch(2);batch['rules'][1]['id']=batch['rules'][0]['id']
    with pytest.raises(EvidenceCatalogError,match='duplicate'):routing.RoutingTable([batch])
    from guardoath.sigma_rules import evidence_catalog
    monkeypatch.setattr(evidence_catalog,'load_evidence_catalog',lambda:(_ for _ in ()).throw(FileNotFoundError('missing')))
    with pytest.raises(FileNotFoundError):routing.load_routing_table()

@pytest.mark.parametrize('settings',[{'max_workers':0},{'max_workers':True},
    {'timeout_seconds':0},{'timeout_seconds':float('nan')},{'timeout_seconds':6}])
def test_invalid_host_budget_settings_are_not_silently_replaced(settings):
    with pytest.raises(ValueError):
        TABLE.evaluate({},trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids,**settings)


def test_loader_rejects_duplicate_json_on_its_own_asset_read(tmp_path,monkeypatch):
    (tmp_path/BATCH_NAMES[0]).write_text('{"license":"first","license":"second"}',encoding='utf-8')
    monkeypatch.setattr(routing,'files',lambda name:tmp_path)
    with pytest.raises(EvidenceCatalogError,match='duplicate'):
        routing.load_routing_table()
