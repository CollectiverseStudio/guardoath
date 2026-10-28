from copy import deepcopy
import json
from pathlib import Path

import pytest

from valinator.compare import compare_reports, load_report, main, render_html
from valinator.evaluate import main as evaluate_main

ROOT = Path(__file__).resolve().parents[1]


def report():
    return {'format_version':'1','suite_id':'same-suite','execution_scope':'local_engine',
        'created_at':'2026-10-28T12:00:00+00:00',
        'summary':{'total':1,'matched':1,'mismatched':0,'incomplete':0,'simulated':0},
        'cases':[{'case_id':'example','group':'secrets','stage':'input','matched':True,'differences':[],
        'evaluation_status':'complete','recommended_action':'allow','effective_action':'allow',
        'checker_statuses':{'secrets':'passed'},'finding_rule_ids':[],'redaction_status':'not_requested',
        'simulated':False,'latency_ms':1.0}]}


def recount(value):
    cases=value['cases']
    value['summary']={'total':len(cases),'matched':sum(c['matched'] for c in cases),
        'mismatched':sum(not c['matched'] for c in cases),
        'incomplete':sum(c['evaluation_status']=='incomplete' for c in cases),
        'simulated':sum(c['simulated'] for c in cases)}
    return value


def save(tmp_path,name,value):
    path=tmp_path/name
    path.write_text(json.dumps(value),encoding='utf-8')
    return path


def test_timing_alone_does_not_change_outcomes():
    previous=report()
    current=deepcopy(previous)
    current['cases'][0]['latency_ms']=2.5
    result=compare_reports(previous,current)
    assert result['summary']['unchanged']==1
    assert result['cases'][0]['changed_fields']==[]
    assert result['cases'][0]['latency_delta_ms']==1.5


@pytest.mark.parametrize('kind', ['new_mismatch','resolved_mismatch','outcome_changed','mismatch_details_changed','identity_changed'])
def test_classification_distinguishes_assertions_outcomes_and_identity(kind):
    previous=report()
    current=deepcopy(previous)
    left,right=previous['cases'][0],current['cases'][0]
    if kind=='new_mismatch':
        right.update(matched=False,differences=['recommended_action'])
    elif kind=='resolved_mismatch':
        left.update(matched=False,differences=['recommended_action'])
    elif kind=='outcome_changed':
        # Both expectations still match; changed expected behavior must stay visible.
        right.update(recommended_action='flag',checker_statuses={'secrets':'flagged'},finding_rule_ids=['secrets.github_token'])
    elif kind=='mismatch_details_changed':
        left.update(matched=False,differences=['recommended_action'])
        right.update(matched=False,differences=['checker_statuses'])
    elif kind=='identity_changed': right['stage']='output'
    result=compare_reports(recount(previous),recount(current))
    assert result['cases'][0]['change']==kind
    assert result['summary'][kind]==1


def test_added_and_removed_cases_remain_visible():
    previous=report()
    current=deepcopy(previous)
    current['cases'][0]['case_id']='new-case'
    result=compare_reports(previous,current)
    assert result['summary']['added']==result['summary']['removed']==1
    assert result['summary']['unchanged']==0


@pytest.mark.parametrize('invalid', ['duplicate_id','false_summary','unknown_field','nan','wrong_match','no_timezone','raw_content'])
def test_strict_report_validation_rejects_misleading_or_private_fields(tmp_path,invalid):
    value=report()
    if invalid=='duplicate_id': value['cases']*=2; recount(value)
    elif invalid=='false_summary': value['summary']['matched']=0
    elif invalid=='unknown_field': value['unexpected']='private-source-value'
    elif invalid=='nan': value['cases'][0]['latency_ms']=float('nan')
    elif invalid=='wrong_match': value['cases'][0]['differences']=['recommended_action']
    elif invalid=='no_timezone': value['created_at']='2026-10-28T12:00:00'
    elif invalid=='raw_content': value['cases'][0]['content']='private-source-value'
    path=save(tmp_path,'bad.json',value)
    with pytest.raises(ValueError,match='Unable to load a bounded local regression report'): load_report(path)


def test_bounded_loader_rejects_duplicate_json_keys_and_oversize(tmp_path):
    path=tmp_path/'bad.json'
    for data in ['{"cases":[],"cases":[]}', ' '*262145]:
        path.write_text(data,encoding='utf-8')
        with pytest.raises(ValueError): load_report(path)


def test_different_suites_cannot_be_silently_aligned():
    current=report(); current['suite_id']='different-suite'
    with pytest.raises(ValueError,match='same suite identifier'): compare_reports(report(),current)


def test_standalone_cli_exit_codes_and_input_protection(tmp_path):
    old=save(tmp_path,'before.json',report())
    new=save(tmp_path,'after.json',report())
    output=tmp_path/'compare.json'
    html=tmp_path/'compare.html'
    args=['--baseline',str(old),'--current',str(new),'--report',str(output),'--html',str(html)]
    assert main(args)==0
    assert json.loads(output.read_text())['summary']['unchanged']==1
    assert main(args)==2  # Existing files are protected.
    assert main(args+['--overwrite'])==0
    original=old.read_bytes()
    assert main(['--baseline',str(old),'--current',str(new),'--report',str(old),'--overwrite'])==2
    assert old.read_bytes()==original
    changed=report(); changed['cases'][0]['recommended_action']='flag'
    save(tmp_path,'after.json',changed)
    assert main(['--baseline',str(old),'--current',str(new)])==0
    assert main(['--baseline',str(old),'--current',str(new),'--fail-on-change'])==1
    changed['cases'][0].update(matched=False,differences=['recommended_action'])
    save(tmp_path,'after.json',recount(changed))
    assert main(['--baseline',str(old),'--current',str(new)])==1


def test_rejected_report_does_not_echo_private_values(tmp_path,capsys):
    invalid=report(); invalid['cases'][0]['content']='private-source-value'
    path=save(tmp_path,'bad.json',invalid)
    good=save(tmp_path,'good.json',report())
    assert main(['--baseline',str(path),'--current',str(good)])==2
    assert 'private-source-value' not in capsys.readouterr().out


def test_evaluator_keeps_report_format_and_embeds_safe_comparison(tmp_path):
    baseline=tmp_path/'baseline.json'
    current=tmp_path/'current.json'
    html=tmp_path/'report.html'
    comparison=tmp_path/'comparison.json'
    args=['--cases',str(ROOT/'evals/local.json'),'--policies',str(ROOT/'policies/playground.json'),'--group','secrets']
    assert evaluate_main(args+['--report',str(baseline)])==0
    assert evaluate_main(args+['--baseline',str(baseline),'--report',str(current),'--html',str(html),'--comparison-report',str(comparison)])==0
    assert load_report(current).summary.total==5
    assert json.loads(comparison.read_text())['summary']['unchanged']==5
    rendered=html.read_text(encoding='utf-8')
    assert 'Changes from baseline' in rendered
    assert 'ghp'+'_'+'A'*36 not in rendered
    original=baseline.read_bytes()
    assert evaluate_main(args+['--baseline',str(baseline),'--report',str(baseline),'--overwrite'])==2
    assert baseline.read_bytes()==original


def test_comparison_html_escapes_untrusted_labels_and_has_no_active_dependencies():
    comparison=compare_reports(report(),report())
    comparison['suite_id']='<script>alert(1)</script>'
    html=render_html(comparison)
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert "default-src 'none'" in html
    assert 'their equality is not verified' in html


def test_identifier_order_does_not_create_a_false_change():
    old=report()
    old['cases'][0]['finding_rule_ids']=['secrets.a','secrets.b']
    old['cases'][0]['checker_statuses']={'secrets':'flagged'}
    current=deepcopy(old)
    current['cases'][0]['finding_rule_ids'].reverse()
    assert compare_reports(old,current)['summary']['unchanged']==1
