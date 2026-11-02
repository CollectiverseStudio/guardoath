"""Routing previews are descriptive, bounded, and never cached admission."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import subprocess

import pytest

from guardoath.sigma_rules import ImportedRule, LICENSE, SigmaBudgetExceeded, UnsupportedSigmaRule
from guardoath.sigma_rules import bounded_regex, budgets, routing
from guardoath.sigma_rules.evidence_catalog import BATCH_NAMES

ROOT=Path(__file__).resolve().parents[1]/'guardoath/sigma_rules'
BATCHES=[json.loads((ROOT/name).read_text(encoding='utf-8')) for name in BATCH_NAMES]
ROWS=[r for batch in BATCHES for r in batch['rules']]
TABLE=routing.load_routing_table()
SCRIPT={'product':'windows','category':'ps_script'}
PROCESS={'product':'windows','category':'process_creation'}


@pytest.mark.parametrize('row',ROWS,ids=lambda r:r['id'])
def test_all_real_rule_previews_agree_with_original_positive_and_lookalike(row):
    source={k:row['logsource'][k] for k in ('product','category')}
    plan=TABLE.plan(trusted_logsource=source,allowed_rule_ids=[row['id']])
    assert plan.selected_rule_ids==(row['id'],) and plan.excluded_rule_ids==()
    assert plan.as_dict()['logsource']==source
    actual=TABLE.evaluate(row['positive_event'],trusted_logsource=source,allowed_rule_ids=[row['id']])
    assert tuple(r['rule_id'] for r in actual)==plan.selected_rule_ids
    assert TABLE.evaluate(row['negative_event'],trusted_logsource=source,allowed_rule_ids=[row['id']])==[]


@pytest.mark.parametrize('source,selected_count,excluded_count',[(SCRIPT,125,3),(PROCESS,3,125)])
def test_exact_source_partition_preserves_host_order_and_full_definitions(source,selected_count,excluded_count):
    allowed=list(reversed(TABLE.rule_ids));before=deepcopy(allowed)
    plan=TABLE.plan(trusted_logsource=source,allowed_rule_ids=allowed)
    assert allowed==before
    assert len(plan.selected_rule_ids)==selected_count and len(plan.excluded_rule_ids)==excluded_count
    selected=tuple(i for i in allowed if all(TABLE.source_for(i)[k]==v for k,v in source.items()))
    assert plan.selected_rule_ids==selected
    assert plan.excluded_rule_ids==tuple(i for i in allowed if i not in selected)
    assert not set(plan.selected_rule_ids)&set(plan.excluded_rule_ids)


def test_plan_executes_no_matcher_worker_or_evaluator(monkeypatch):
    monkeypatch.setattr(ImportedRule,'matches',lambda *a:pytest.fail('matcher executed'))
    monkeypatch.setattr(bounded_regex.subprocess,'run',lambda *a,**k:pytest.fail('worker launched'))
    monkeypatch.setattr(routing,'evaluate_event',lambda *a,**k:pytest.fail('event evaluated'))
    result=TABLE.plan(trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids)
    assert len(result.selected_rule_ids)==125
    assert result.as_dict()['evaluation_performed'] is False


def test_plan_and_export_are_immutable_and_never_accepted_as_authority():
    plan=TABLE.plan(trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids)
    with pytest.raises(FrozenInstanceError):plan.product='linux'
    exported=plan.as_dict();expected=deepcopy(exported)
    exported['selected_rule_ids'].clear();exported['logsource']['category']='chat'
    assert plan.as_dict()==expected
    assert json.loads(json.dumps(plan.as_dict()))==expected
    with pytest.raises(TypeError):TABLE.evaluate({},plan=plan)
    with pytest.raises(TypeError):TABLE.plan(event={'product':'windows','category':'process_creation'},trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids)
    # A previous successful preview does not admit a changed/invalid host config.
    with pytest.raises(UnsupportedSigmaRule):
        TABLE.evaluate({},trusted_logsource=SCRIPT,allowed_rule_ids=['unknown'])


BAD=[(None,TABLE.rule_ids),({'product':'linux','category':'ps_script'},TABLE.rule_ids),
    ({'product':'windows','category':'PS_SCRIPT'},TABLE.rule_ids),
    ({**SCRIPT,'service':'PowerShell'},TABLE.rule_ids),(SCRIPT,[]),(SCRIPT,None),
    (SCRIPT,{}),(SCRIPT,'id'),(SCRIPT,[False]),(SCRIPT,['private-id-canary']),
    (SCRIPT,[TABLE.rule_ids[0]]*2),(SCRIPT,[TABLE.rule_ids[0]])]


@pytest.mark.parametrize('source,ids',BAD)
def test_plan_and_evaluation_reject_same_bad_configuration_without_echo(source,ids):
    errors=[]
    for call in (lambda:TABLE.plan(trusted_logsource=source,allowed_rule_ids=ids),
                 lambda:TABLE.evaluate({},trusted_logsource=source,allowed_rule_ids=ids)):
        with pytest.raises(UnsupportedSigmaRule) as error:call()
        errors.append(str(error.value))
    assert errors[0]==errors[1] and 'private-id-canary' not in errors[0]


def synthetic_table(count,*,regex=False,process=0):
    # Synthetic boundary fixtures only, not additional upstream rules.
    rows=[{'id':f'fixture.plan.{i}','title':'Synthetic plan fixture','author':'GuardOath test fixture',
        'source_path':f'rules/windows/test/plan_{i}.yml',
        'logsource':{'product':'windows','category':'process_creation' if i<process else 'ps_script'},
        'detection':{'selection':{'ScriptBlockText|re' if regex else 'ScriptBlockText|contains':'alpha'},'condition':'selection'},
        'positive_event':{'ScriptBlockText':'alpha'},'negative_event':{'ScriptBlockText':'safe'}} for i in range(count)]
    return routing.RoutingTable([{'source_repository':'https://github.com/SigmaHQ/sigma','license':LICENSE,'rules':rows}])


def test_exact_selected_bound_matches_evaluation_and_no_truncation():
    table=synthetic_table(129)
    assert len(table.plan(trusted_logsource=SCRIPT,allowed_rule_ids=table.rule_ids[:128]).selected_rule_ids)==128
    for call in (lambda:table.plan(trusted_logsource=SCRIPT,allowed_rule_ids=table.rule_ids),
                 lambda:table.evaluate({},trusted_logsource=SCRIPT,allowed_rule_ids=table.rule_ids)):
        with pytest.raises(UnsupportedSigmaRule,match='128'):call()
    mixed=synthetic_table(129,process=1)
    plan=mixed.plan(trusted_logsource=SCRIPT,allowed_rule_ids=mixed.rule_ids)
    assert len(plan.selected_rule_ids)==128 and plan.excluded_rule_ids==(mixed.rule_ids[0],)


@pytest.mark.parametrize('timeout',[0,True,float('nan'),float('inf'),5.1])
def test_invalid_preview_deadline_is_not_replaced(timeout):
    with pytest.raises(ValueError):TABLE.plan(trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids,timeout_seconds=timeout)


def test_selection_preparation_time_is_included_and_late_plan_is_not_returned(monkeypatch):
    clock=[0.0];monkeypatch.setattr(budgets,'monotonic',lambda:clock[0])
    original=routing.RoutingTable._select
    def slow(self,*args):
        plan=original(self,*args);clock[0]+=.3;return plan
    monkeypatch.setattr(routing.RoutingTable,'_select',slow)
    with pytest.raises(SigmaBudgetExceeded,match='deadline'):
        TABLE.plan(trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids,timeout_seconds=.2)


def test_plans_do_not_refresh_inherited_deadline_and_context_recovers(monkeypatch):
    clock=[0.0];monkeypatch.setattr(budgets,'monotonic',lambda:clock[0])
    original=routing.RoutingTable._select
    def slow(self,*args):
        plan=original(self,*args);clock[0]+=.06;return plan
    monkeypatch.setattr(routing.RoutingTable,'_select',slow)
    with pytest.raises(SigmaBudgetExceeded):
        with budgets.evaluation_budget(max_workers=1,timeout_seconds=.1):
            assert TABLE.plan(trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids,timeout_seconds=5)
            TABLE.plan(trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids,timeout_seconds=5)
    assert TABLE.plan(trusted_logsource=SCRIPT,allowed_rule_ids=TABLE.rule_ids)


def test_preview_does_not_reset_outer_worker_quota_before_evaluation(monkeypatch):
    calls=[]
    def run(args,**kwargs):
        calls.append(kwargs);return subprocess.CompletedProcess(args,0,'1')
    monkeypatch.setattr(bounded_regex.subprocess,'run',run)
    table=synthetic_table(1,regex=True)
    with budgets.evaluation_budget(max_workers=1):
        assert bounded_regex.bounded_search('alpha','alpha')
        assert table.plan(trusted_logsource=SCRIPT,allowed_rule_ids=table.rule_ids)
        with pytest.raises(SigmaBudgetExceeded,match='worker budget'):
            table.evaluate({'ScriptBlockText':'alpha'},trusted_logsource=SCRIPT,allowed_rule_ids=table.rule_ids,max_workers=16)
    assert len(calls)==1


def test_oversized_source_is_rejected_before_materializing_its_keys():
    from collections.abc import Mapping
    class OversizedSource(Mapping):
        def __len__(self):return 1_000_000
        def __iter__(self):pytest.fail('unbounded source keys iterated')
        def __getitem__(self,key):pytest.fail('unbounded source value read')
    with pytest.raises(UnsupportedSigmaRule):
        TABLE.plan(trusted_logsource=OversizedSource(),allowed_rule_ids=TABLE.rule_ids)
    with pytest.raises(UnsupportedSigmaRule):
        TABLE.evaluate({},trusted_logsource=OversizedSource(),allowed_rule_ids=TABLE.rule_ids)
