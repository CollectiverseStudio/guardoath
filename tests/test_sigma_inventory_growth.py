"""Synthetic growing catalogs never increase one event's selected-rule budget."""
import pytest

from guardoath.sigma_rules import LICENSE, UnsupportedSigmaRule, SigmaBudgetExceeded
from guardoath.sigma_rules import budgets, routing
from guardoath.sigma_rules.events import evaluate_event

SCRIPT = {'product':'windows','category':'ps_script'}
PROCESS = {'product':'windows','category':'process_creation'}


def table(script=125, process=28):
    # Synthetic boundary fixtures, not imported SigmaHQ detections or provenance.
    rows = []
    for source,count,field in ((SCRIPT,script,'ScriptBlockText'),(PROCESS,process,'CommandLine')):
        for index in range(count):
            key = source['category']+'_'+str(index)
            rows.append({'id':'fixture.growth.'+key,'title':'Synthetic growing-inventory fixture',
                'author':'GuardOath test fixture','source_path':'rules/windows/test/'+key+'.yml',
                'logsource':dict(source),'detection':{'selection':{field+'|contains':'alpha'},'condition':'selection'},
                'positive_event':{field:'alpha'},'negative_event':{field:'safe'}})
    batch = {'source_repository':'https://github.com/SigmaHQ/sigma','license':LICENSE,'rules':rows}
    return routing.RoutingTable([batch])


@pytest.mark.parametrize('source,count,excluded',[(SCRIPT,125,28),(PROCESS,28,125)])
def test_153_metadata_rows_route_once_with_host_source_and_one_shared_budget(monkeypatch,source,count,excluded):
    inventory = table(); seen = []
    real = routing.evaluate_event
    def spy(selected,event,**kwargs):
        seen.append(tuple(rule.rule_id for rule in selected))
        return real(selected,event,**kwargs)
    monkeypatch.setattr(routing,'evaluate_event',spy)
    assert len(inventory.rule_ids) == 153
    allowed = tuple(reversed(inventory.rule_ids))
    preview = inventory.plan(trusted_logsource=source,allowed_rule_ids=allowed)
    assert len(preview.selected_rule_ids) == count and len(preview.excluded_rule_ids) == excluded
    # Both event payloads match, but only the trusted source selects a rule group.
    event = {'ScriptBlockText':'alpha','CommandLine':'alpha','category':'untrusted',
             'allowed_rule_ids':'untrusted'}
    findings = inventory.evaluate(event,trusted_logsource=source,allowed_rule_ids=allowed)
    assert len(seen) == 1 and seen[0] == preview.selected_rule_ids
    assert tuple(row['rule_id'] for row in findings) == preview.selected_rule_ids
    assert not set(preview.selected_rule_ids) & set(preview.excluded_rule_ids)


def test_larger_inventory_does_not_make_raw_153_rule_evaluation_legal(monkeypatch):
    inventory = table()
    selected = tuple(inventory._rules.values())
    monkeypatch.setattr(type(selected[0]),'matches',lambda *a:pytest.fail('oversized event evaluated'))
    with pytest.raises(UnsupportedSigmaRule,match='128'): evaluate_event(selected,{})


def test_128_selected_plus_excluded_is_legal_but_129_selected_is_never_truncated():
    inventory = table(script=129,process=28)
    script = [i for i in inventory.rule_ids if inventory.source_for(i) == SCRIPT]
    process = [i for i in inventory.rule_ids if inventory.source_for(i) == PROCESS]
    allowed = script[:128]+process
    plan = inventory.plan(trusted_logsource=SCRIPT,allowed_rule_ids=allowed)
    assert len(plan.selected_rule_ids) == 128 and len(plan.excluded_rule_ids) == 28
    assert len(inventory.evaluate({'ScriptBlockText':'alpha'},trusted_logsource=SCRIPT,allowed_rule_ids=allowed)) == 128
    for call in (inventory.plan,lambda **args:inventory.evaluate({},**args)):
        with pytest.raises(UnsupportedSigmaRule,match='128'):
            call(trusted_logsource=SCRIPT,allowed_rule_ids=inventory.rule_ids)


def test_large_catalog_preview_and_event_do_not_refresh_consumed_worker_quota(monkeypatch):
    inventory = table()
    def evaluator(*args,**kwargs): budgets.remaining_seconds(launch_worker=True)
    monkeypatch.setattr(routing,'evaluate_event',evaluator)
    with budgets.evaluation_budget(max_workers=1):
        budgets.remaining_seconds(launch_worker=True)
        assert len(inventory.plan(trusted_logsource=SCRIPT,allowed_rule_ids=inventory.rule_ids).selected_rule_ids) == 125
        with pytest.raises(SigmaBudgetExceeded,match='worker budget'):
            inventory.evaluate({},trusted_logsource=SCRIPT,allowed_rule_ids=inventory.rule_ids,max_workers=16)
