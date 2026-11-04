"""Origin/metadata rejection and fixture failures; real isolated install is separate."""
from dataclasses import FrozenInstanceError
from importlib.metadata import PathDistribution
import json
from pathlib import Path
import shutil

import pytest

from guardoath.sigma_rules import evidence_catalog, routing, verify_installation as acceptance
from guardoath.sigma_rules.budgets import SigmaBudgetExceeded

ROOT = Path(__file__).resolve().parents[1]/'guardoath/sigma_rules'


@pytest.fixture
def installation(tmp_path, monkeypatch):
    site = tmp_path/'site'; package = site/'guardoath/sigma_rules'
    shutil.copytree(ROOT, package, ignore=shutil.ignore_patterns('__pycache__'))
    info = site/'guardoath-0.0.test.dist-info'; info.mkdir()
    (info/'METADATA').write_text('Metadata-Version: 2.1\nName: guardoath\nVersion: 0.0.test\n',encoding='utf-8')
    entries = [p.relative_to(site).as_posix() for p in package.rglob('*') if p.is_file()]
    (info/'RECORD').write_text(''.join(name+',,\n' for name in entries),encoding='utf-8')
    dist = PathDistribution(info)
    monkeypatch.setattr(acceptance.metadata,'distribution',lambda _:dist)
    # Unit layout fixtures mock origin discovery, never claim a wheel was installed.
    monkeypatch.setattr(acceptance,'_origins',lambda:{name:package/(name+'.py') for name in acceptance._MODULES})
    monkeypatch.setattr(evidence_catalog,'files',lambda _:package)
    monkeypatch.setattr(routing,'files',lambda _:package)
    return site,package,info


def test_complete_layout_executes_all_source_fixtures_with_immutable_evidence(installation):
    site,_,_ = installation
    report = acceptance.verify_installation(site)
    assert report.rule_count == report.pinned_rule_count == 228
    assert report.required_asset_count == 239
    assert report.positive_cases == report.benign_cases == report.missing_event_cases == 228
    assert report.malformed_event_rejections == report.routing_previews == 228
    assert report.source_revisions == ('05d1031f537e830b76da53bae2400c5814c89165',)
    with pytest.raises(FrozenInstanceError): report.rule_count = 0


@pytest.mark.parametrize('module',acceptance._MODULES)
def test_each_checkout_or_foreign_module_origin_rejects_before_execution(installation,monkeypatch,module):
    site,package,_ = installation
    origins = {name:package/(name+'.py') for name in acceptance._MODULES}
    origins[module] = ROOT/(module+'.py')
    monkeypatch.setattr(acceptance,'_origins',lambda:origins)
    monkeypatch.setattr(acceptance,'load_evidence_catalog',lambda:pytest.fail('foreign code acceptance proceeded'))
    with pytest.raises(acceptance.InstalledAcceptanceError,match='imports differ'):
        acceptance.verify_installation(site)


@pytest.mark.parametrize('kind',['missing','file','parent','metadata'])
def test_bad_root_or_foreign_distribution_is_not_checkout_fallback(installation,monkeypatch,kind):
    site,package,info = installation
    root = site
    if kind == 'missing': root = site/'private-canary-missing'
    elif kind == 'file': root = package/'routing.py'
    elif kind == 'parent': root = site.parent
    else:
        class ForeignDist:
            def locate_file(self,_): return ROOT.parent
        monkeypatch.setattr(acceptance.metadata,'distribution',lambda _:ForeignDist())
    with pytest.raises(acceptance.InstalledAcceptanceError) as error: acceptance.verify_installation(root)
    assert 'private-canary' not in str(error.value)


@pytest.mark.parametrize('direct',[
    '{"dir_info":{"editable":true}}','{broken','[]','{"dir_info":[]}',
])
def test_editable_or_malformed_direct_url_is_not_artifact_acceptance(installation,direct):
    site,_,info = installation
    (info/'direct_url.json').write_text(direct,encoding='utf-8')
    with pytest.raises(acceptance.InstalledAcceptanceError): acceptance.verify_installation(site)


def test_noneditable_local_wheel_direct_url_is_allowed(installation):
    site,_,info = installation
    (info/'direct_url.json').write_text('{"url":"file:///trusted.whl","archive_info":{}}',encoding='utf-8')
    assert acceptance.verify_installation(site).rule_count == 228


@pytest.mark.parametrize('missing',['guardoath/sigma_rules/'+name+'.py' for name in acceptance._MODULES]+
                         ['guardoath/sigma_rules/'+name for name in evidence_catalog.BATCH_NAMES]+
                         ['guardoath/sigma_rules/LICENSE.Detection.Rules.md'])
def test_required_module_and_asset_records_cannot_be_omitted(installation,missing):
    site,_,info = installation
    record = info/'RECORD'
    record.write_text(''.join(line for line in record.read_text().splitlines(keepends=True)
                              if not line.startswith(missing+',')),encoding='utf-8')
    with pytest.raises(acceptance.InstalledAcceptanceError,match='records are incomplete'):
        acceptance.verify_installation(site)


@pytest.mark.parametrize('asset',['upstream_batch_001.json','LICENSE.Detection.Rules.md',
    'upstream/batch_001/proc_creation_win_7zip_password_compression.yml'])
def test_missing_or_changed_actual_assets_fail_despite_complete_record(installation,asset):
    site,package,_ = installation
    (package/asset).write_text('private-drift-canary',encoding='utf-8')
    with pytest.raises(acceptance.InstalledAcceptanceError) as error: acceptance.verify_installation(site)
    assert 'private-drift-canary' not in str(error.value)


@pytest.mark.parametrize('failure',['positive','finding','negative','missing','malformed','timeout'])
def test_runtime_regressions_never_return_partial_success(installation,monkeypatch,failure):
    site,_,_ = installation
    original = routing.RoutingTable.evaluate
    def evaluate(self,event,**args):
        if failure == 'timeout': raise SigmaBudgetExceeded('private-budget-canary')
        result = original(self,event,**args) if not (failure == 'malformed' and event == {'invalid':[]}) else []
        if failure == 'positive' and result: return []
        if failure == 'finding' and result: return [{**result[0],'author':'altered'}]
        if failure == 'negative' and not result and event: return [{'rule_id':'wrong'}]
        if failure == 'missing' and event == {}: return [{'rule_id':'wrong'}]
        return result
    monkeypatch.setattr(routing.RoutingTable,'evaluate',evaluate)
    with pytest.raises(acceptance.InstalledAcceptanceError) as error: acceptance.verify_installation(site)
    assert 'private-budget-canary' not in str(error.value)


def test_invalid_preview_cannot_be_accepted(installation,monkeypatch):
    site,_,_ = installation
    monkeypatch.setattr(routing.RoutingTable,'plan',lambda *a,**k:routing.RoutingPlan('windows','ps_script',(),()))
    with pytest.raises(acceptance.InstalledAcceptanceError,match='preview differs'): acceptance.verify_installation(site)


def test_cli_usage_success_failure_and_no_partial_json(installation,capsys):
    site,_,_ = installation
    assert acceptance.main(['--expected-root',str(site)]) == 0
    assert json.loads(capsys.readouterr().out)['positive_cases'] == 228
    assert acceptance.main(['--expected-root',str(site/'private-canary')]) == 1
    output = capsys.readouterr()
    assert output.out == '' and 'private-canary' not in output.err
    assert acceptance.main([]) == acceptance.main([str(site)]) == 2
    assert capsys.readouterr().out == ''


def test_discovery_lists_real_current_imports_without_launching_anything():
    origins = acceptance._origins()
    assert set(origins) == set(acceptance._MODULES)
    assert all(path.is_file() and path.parent == ROOT for path in origins.values())
