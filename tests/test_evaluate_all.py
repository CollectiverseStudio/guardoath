"""All-suite orchestration preserves real results and never enables providers."""
import asyncio
import json
from pathlib import Path
import shutil

import pytest
import guardoath.evaluate_all as all_checks

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def full_collection(tmp_path_factory):
    pytest.importorskip('tldextract')
    pytest.importorskip('phonenumbers')
    directory=tmp_path_factory.mktemp('collections')/'all'
    report=asyncio.run(all_checks.run_all(ROOT,directory,True))
    return directory,report


def test_all_bundled_suites_and_reports(full_collection):
    directory,collection=full_collection
    assert collection['totals']=={'total':1430,'matched':1430,'mismatched':0,'incomplete':396,'simulated':0}
    assert [row['suite'] for row in collection['suites']]==list(all_checks.SUITES)
    assert len(collection['suites'])==15
    for name in all_checks.SUITES:
        report=json.loads((directory/name/'report.json').read_text())
        assert report['summary']['matched']==report['summary']['total']
        assert (directory/name/'report.html').is_file()
    assert json.loads((directory/'collection.json').read_text())==collection


def test_collection_index_is_source_free_and_not_a_clean_content_claim(full_collection):
    directory,_=full_collection
    html=(directory/'index.html').read_text()
    assert '1430 of 1430 expectations matched' in html
    assert 'not clean-content or accuracy claims' in html and 'Incomplete' in html
    assert "default-src 'none'" in html and '<script' not in html
    for name in all_checks.SUITES:assert name+'/report.html' in html
    for private in ['jane@example.com','Unused items may be returned within 30 days.','ORD-1234','private-factor']:
        assert private not in html


def test_existing_collection_is_never_overwritten(full_collection):
    directory,_=full_collection
    original=(directory/'collection.json').read_bytes()
    with pytest.raises(ValueError,match='new collection'):
        asyncio.run(all_checks.run_all(ROOT,directory,True))
    assert (directory/'collection.json').read_bytes()==original


def test_bad_inputs_fail_before_creating_output(tmp_path):
    destination=tmp_path/'not-created'
    with pytest.raises(Exception):asyncio.run(all_checks.run_all(tmp_path/'missing-root',destination))
    assert not destination.exists()


def fixture_root(tmp_path):
    for name in ['evals','policies']:(tmp_path/name).mkdir()
    for name in ['evals/evidence.json','policies/evidence-batch.json']:
        shutil.copyfile(ROOT/name,tmp_path/name)
    return tmp_path


def test_real_mismatched_expectation_produces_failure_exit_and_visible_count(tmp_path,monkeypatch):
    (tmp_path/'input').mkdir()
    root=fixture_root(tmp_path/'input')
    suite=json.loads((root/'evals/evidence.json').read_text())
    suite['cases'][0]['expected']['finding_rule_ids']=['deliberately_wrong_rule']
    (root/'evals/evidence.json').write_text(json.dumps(suite))
    monkeypatch.setattr(all_checks,'SUITES',('evidence',))
    output=tmp_path/'results'
    code=all_checks.main(['--root',str(root),'--output',str(output)])
    assert code==1
    collection=json.loads((output/'collection.json').read_text())
    assert collection['totals']['mismatched']==1 and collection['totals']['matched']==99
    assert '99 of 100 expectations matched' in (output/'index.html').read_text()


def test_provider_selection_is_ignored_and_external_cases_are_rejected(tmp_path,monkeypatch):
    (tmp_path/'input').mkdir()
    root=fixture_root(tmp_path/'input')
    monkeypatch.setenv('GUARDOATH_CONTENT_SAFETY_PROVIDER','bedrock')
    monkeypatch.setenv('GUARDOATH_AWS_REGION','invalid-inherited-region')
    monkeypatch.setenv('GUARDOATH_ENABLE_AZURE_PROMPT_SHIELDS','1')
    monkeypatch.setattr(all_checks,'SUITES',('evidence',))
    assert all_checks.main(['--root',str(root),'--output',str(tmp_path/'local-results')])==0
    policies=json.loads((root/'policies/evidence-batch.json').read_text())
    policies['policies'][0]['rules'].append({'checker_id':'content_safety'})
    (root/'policies/evidence-batch.json').write_text(json.dumps(policies))
    output=tmp_path/'provider-not-created'
    assert all_checks.main(['--root',str(root),'--output',str(output)])==2
    assert not output.exists()
