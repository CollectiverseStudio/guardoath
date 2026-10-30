import asyncio
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.evaluate import Suite, evaluate, load_suite, main, render_html
from guardoath.models import CheckerResult, Policy, Rule

ROOT = Path(__file__).resolve().parents[1]


def core_report():
    return asyncio.run(evaluate(load_suite(ROOT/'evals/local.json'),
        Engine(load_policies(ROOT/'policies/playground.json'),default_checkers()),
        {'secrets','prompt_security','business','structure'}))


def test_handwritten_core_cases_match_real_engine():
    report = core_report()
    assert report['summary'] == {'total':21,'matched':21,'mismatched':0,'incomplete':2,'simulated':0}
    assert '"request":' not in json.dumps(report)
    assert 'SGVsbG8=' not in json.dumps(report)
    assert 'refund_amount' not in json.dumps(report)
    assert not any('content' in row for row in report['cases'])


def test_complete_local_suite_and_redaction_reports_omit_values():
    pytest.importorskip('tldextract')
    pytest.importorskip('phonenumbers')
    report = asyncio.run(evaluate(load_suite(ROOT/'evals/local.json'),
        Engine(load_policies(ROOT/'policies/playground.json'),default_checkers(enable_pii=True))))
    assert report['summary']['total'] == report['summary']['matched'] == 30
    assert next(c for c in report['cases'] if c['case_id'] == 'redaction.email')['redaction_status'] == 'preview'
    for private in ['jane@example.com','219-09-9999','4111 1111','ghp'+'_'+'A'*36,'[REDACTED']:
        assert private not in json.dumps(report)
        assert private not in render_html(report)


def single_suite(tmp_path, *, mismatch=False):
    raw = json.loads((ROOT/'evals/local.json').read_text(encoding='utf-8'))
    raw['cases'] = [next(c for c in raw['cases'] if c['case_id']=='secrets.clean')]
    raw['cases'][0]['request']['content'] = 'private-source-value-that-must-not-be-reported'
    if mismatch: raw['cases'][0]['expected']['recommended_action'] = 'block'
    path = tmp_path/'cases.json'
    path.write_text(json.dumps(raw))
    return path


def test_cli_mismatch_exit_and_report_do_not_echo_input(tmp_path,capsys):
    source = single_suite(tmp_path,mismatch=True)
    report = tmp_path/'report.json'
    html = tmp_path/'report.html'
    code = main(['--cases',str(source),'--policies',str(ROOT/'policies/playground.json'),'--report',str(report),'--html',str(html)])
    assert code == 1
    assert json.loads(report.read_text(encoding='utf-8'))['cases'][0]['differences'] == ['recommended_action']
    text = capsys.readouterr().out+report.read_text(encoding='utf-8')+html.read_text(encoding='utf-8')
    assert 'private-source-value' not in text
    assert '1 mismatched' in text


def test_invalid_suite_errors_are_sanitized(tmp_path,capsys):
    path = tmp_path/'cases.json'
    path.write_text('{"format_version":"private-source-value"}')
    assert main(['--cases',str(path)]) == 2
    assert 'private-source-value' not in capsys.readouterr().out


@pytest.mark.parametrize('kind',['duplicate_case','duplicate_json','oversize','unknown_field'])
def test_strict_bounded_suite_loading(tmp_path,kind):
    path = single_suite(tmp_path)
    raw = json.loads(path.read_text(encoding='utf-8'))
    if kind=='duplicate_case': raw['cases'] *= 2
    elif kind=='unknown_field': raw['cases'][0]['unexpected'] = 'private-source-value'
    if kind=='oversize': path.write_text(' '*262145)
    elif kind=='duplicate_json': path.write_text('{"cases":[],"cases":[]}')
    else: path.write_text(json.dumps(raw))
    with pytest.raises(ValueError,match='Unable to load the bounded regression suite'): load_suite(path)


def test_reports_cannot_replace_input_even_with_overwrite(tmp_path):
    path = single_suite(tmp_path)
    original = path.read_bytes()
    assert main(['--cases',str(path),'--report',str(path),'--overwrite']) == 2
    assert path.read_bytes() == original


def test_report_overwrite_requires_explicit_flag_and_paths_must_differ(tmp_path):
    source = single_suite(tmp_path)
    output = tmp_path/'report.json'
    output.write_text('existing')
    args = ['--cases',str(source),'--policies',str(ROOT/'policies/playground.json'),'--report',str(output)]
    assert main(args) == 2
    assert output.read_text(encoding='utf-8') == 'existing'
    assert main(args+['--overwrite']) == 0
    assert json.loads(output.read_text(encoding='utf-8'))['summary']['matched'] == 1
    assert main(args+['--html',str(output),'--overwrite']) == 2


def test_empty_group_is_configuration_error(tmp_path):
    source = single_suite(tmp_path)
    assert main(['--cases',str(source),'--group','no-matching-group']) == 2


def test_external_engine_refused_before_execution(tmp_path):
    raw = json.loads(single_suite(tmp_path).read_text(encoding='utf-8'))
    raw['cases'][0]['request']['policy_version'] = 'v1'
    suite = Suite.model_validate(raw)
    calls = []
    async def provider(*args): calls.append('provider')
    provider.external = True
    selected = Policy(application_id='demo',version='v1',external_checks='after_local_pass',rules=(Rule(checker_id='provider'),))
    with pytest.raises(ValueError,match='only support local adapters'):
        asyncio.run(evaluate(suite,Engine([selected],{'provider':provider})))
    assert not calls


def test_unknown_policy_refused_before_any_partial_execution(tmp_path):
    raw = json.loads(single_suite(tmp_path).read_text(encoding='utf-8'))
    raw['cases'].append(deepcopy(raw['cases'][0]))
    raw['cases'][1]['case_id'] = 'missing.policy'
    raw['cases'][1]['request']['policy_version'] = 'missing'
    calls = []
    async def local(*args):
        calls.append('local')
        return CheckerResult(checker_id='local',checker_version='1',status='passed')
    selected = Policy(application_id='demo',version='secrets-v1',rules=(Rule(checker_id='local'),))
    with pytest.raises(ValueError,match='unavailable policy'):
        asyncio.run(evaluate(Suite.model_validate(raw),Engine([selected],{'local':local})))
    assert not calls


def test_subprocess_ignores_provider_environment(tmp_path):
    import os
    source = single_suite(tmp_path)
    env = {**os.environ,'GUARDOATH_ENABLE_AZURE_PROMPT_SHIELDS':'1',
           'GUARDOATH_AZURE_CONTENT_SAFETY_ENDPOINT':'invalid-endpoint',
           'GUARDOATH_POLICY_FILE':'missing-policy'}
    result = subprocess.run([sys.executable,'-m','guardoath.evaluate','--cases',str(source),
                             '--policies',str(ROOT/'policies/playground.json')],
                            cwd=ROOT,env=env,capture_output=True,text=True,timeout=15)
    assert result.returncode == 0
    assert '1/1 expectations matched' in result.stdout
    assert 'private-source-value' not in result.stdout+result.stderr


def test_html_escapes_labels_and_has_no_active_dependencies():
    report = core_report()
    report['cases'][0]['case_id'] = '<img src=x onerror=alert(1)>'
    html = render_html(report)
    assert '<img' not in html and '&lt;img' in html
    assert '<script' not in html
    assert "default-src 'none'" in html
    assert 'This is not detector accuracy' in html
    assert 'Incomplete evaluations: 2' in html


def test_simulated_expectation_is_counted_without_claiming_real_assessment(tmp_path):
    raw = json.loads(single_suite(tmp_path).read_text(encoding='utf-8'))
    case = raw['cases'][0]
    case['request']['policy_version'] = 'v1'
    case['expected']['checker_statuses'] = {'safety':'passed'}
    case['expected']['simulated'] = True
    selected = Policy(application_id='demo',version='v1',rules=(Rule(checker_id='safety'),))
    report = asyncio.run(evaluate(Suite.model_validate(raw),Engine([selected],default_checkers())))
    assert report['summary']['matched'] == report['summary']['simulated'] == 1
    assert 'class="warn"' in render_html(report)


def test_report_cannot_overwrite_hardlinked_source(tmp_path):
    import os
    source = single_suite(tmp_path)
    linked = tmp_path/'linked.json'
    os.link(source,linked)
    original = source.read_bytes()
    assert main(['--cases',str(source),'--report',str(linked),'--overwrite']) == 2
    assert source.read_bytes() == original


def test_complete_secrets_group_suite_matches_without_optional_pii():
    report=asyncio.run(evaluate(load_suite(ROOT/'evals/secrets.json'),Engine(load_policies(ROOT/'policies/secrets-batch.json'),default_checkers())))
    assert report['summary']=={'total':100,'matched':100,'mismatched':0,'incomplete':8,'simulated':0}, [
        (r['case_id'], r['differences'], r['checker_statuses']) for r in report['cases'] if not r['matched']]
    for candidate in ['PROJECT LANTERN','synthetic-example!42','ghp'+'_'+'A'*36]:
        assert candidate not in json.dumps(report)
        assert candidate not in render_html(report)
