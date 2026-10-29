"""Portable content-safety contract; no vendor SDK/credentials required."""
import asyncio
import subprocess
import sys
from fastapi.testclient import TestClient
import pytest
from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.content_safety import build_content_safety
from guardoath.engine import Engine
from guardoath.models import CheckerResult, CheckRequest, Stage


def test_no_provider_or_sdk_is_implicitly_selected():
    assert build_content_safety() is None
    assert 'content_safety' not in default_checkers()
    completed = subprocess.run([sys.executable, '-c',
        "from guardoath.checkers import default_checkers; import sys; default_checkers(); assert 'boto3' not in sys.modules; assert 'botocore' not in sys.modules"], capture_output=True)
    assert completed.returncode == 0
    with pytest.raises(ValueError, match='Unsupported content safety provider'):
        build_content_safety('auto')


def test_invalid_provider_is_rejected_at_startup(monkeypatch):
    monkeypatch.setenv('GUARDOATH_CONTENT_SAFETY_PROVIDER', 'auto')
    with pytest.raises(ValueError, match='Unsupported content safety provider'): create_app()


def test_provider_can_be_replaced_without_changing_api_or_policy():
    selected = load_policies('policies/content-safety.json')[0]
    selected = selected.model_copy(update={'detect_labeled_sensitive_data': False,
        'rules': tuple(r for r in selected.rules if r.checker_id != 'pii')})
    calls = []
    async def portable_adapter(request, policy, stage):
        calls.append(stage)
        return CheckerResult(checker_id='content_safety', checker_version='another-provider/1', status='passed', external=True)
    portable_adapter.external = True
    portable_adapter.provider = 'another-provider'
    engine = Engine([selected], {**default_checkers(), 'content_safety': portable_adapter})
    client = TestClient(create_app(engine=engine, credentials={'test-key': 'demo'}))
    headers = {'Authorization': 'Bearer test-key'}
    for stage in Stage:
        result = client.post('/v1/checks/' + stage, headers=headers, json={
            'application_id': 'demo', 'policy_version': 'content-safety-v1', 'content': 'A plain example.'}).json()
        assert result['evaluation_status'] == 'complete' and not result['findings']
        assert result['checker_results'][-1]['checker_id'] == 'content_safety'
        assert result['checker_results'][-1]['checker_version'] == 'another-provider/1'
    assert calls == [Stage.INPUT, Stage.OUTPUT]
    meta = client.get('/v1/applications/current', headers=headers).json()['policies'][0]
    assert meta['checks'][-1]['provider'] == 'another-provider'


def test_disabled_provider_is_incomplete_and_visible_as_external():
    selected = load_policies('policies/content-safety.json')[0]
    engine = Engine([selected], default_checkers())
    check = engine.describe_policies('demo')[0]['checks'][-1]
    assert check['id'] == 'content_safety' and check['external'] and not check['available']
    assert check['provider'] is None
    for stage in Stage:
        result = asyncio.run(engine.check(CheckRequest(application_id='demo',
            policy_version=selected.version, content='A plain example.'), stage))
        assert result.evaluation_status == 'incomplete'
        assert result.checker_results[-1].status == 'unsupported'
        assert result.checker_results[-1].external and result.cost_usd is None
