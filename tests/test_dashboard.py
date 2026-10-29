from fastapi.testclient import TestClient

from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Policy, Rule


def test_dashboard_and_local_assets_load():
    client = TestClient(create_app(credentials={"test-key": "demo"}))
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Check playground" in response.text
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    for asset in ["dashboard.css", "dashboard.js"]:
        assert client.get("/assets/" + asset).status_code == 200


def test_dashboard_metadata_requires_auth_and_is_application_scoped():
    policies = [Policy(application_id="demo", version="public-v1", rules=(Rule(checker_id="safety"),)),
                Policy(application_id="other", version="private-v1", rules=())]
    client = TestClient(create_app(engine=Engine(policies, default_checkers()), credentials={"test-key": "demo"}))
    assert client.get("/v1/applications/current").status_code == 401
    response = client.get("/v1/applications/current", headers={"Authorization": "Bearer test-key"})
    assert response.status_code == 200
    assert "private-v1" not in response.text
    assert response.json()["policies"][0]["checks"][0]["simulated"] is True
    assert "test-key" not in response.text


def conversation_client(enable_pii):
    from pathlib import Path
    from guardoath.config import load_policies
    engine = Engine(load_policies(Path(__file__).resolve().parents[1] / 'policies/playground.json'),
                    default_checkers(enable_pii=enable_pii))
    return TestClient(create_app(engine=engine, credentials={'test-key': 'demo'}))


def test_conversation_profile_cannot_pass_without_its_required_pii_adapter():
    client = conversation_client(False)
    response = client.post('/v1/checks/input', headers={'Authorization': 'Bearer test-key'},
                           json={'application_id': 'demo', 'policy_version': 'conversation-v1',
                                 'content': 'Explain returns.'})
    result = response.json()
    assert response.status_code == 200
    assert result['evaluation_status'] == 'incomplete'
    assert {c['checker_id'] for c in result['checker_results']} == {'pii', 'secrets', 'text_security', 'encoded_pii'}


def test_conversation_profile_runs_combined_checks_on_both_stages():
    import pytest
    pytest.importorskip('presidio_analyzer')
    client = conversation_client(True)
    for stage in ['input', 'output']:
        for content, flagged in [('Explain returns.', None), ('Email jane@example.com', 'pii'),
                                 ('password = "synthetic-example!42"', 'secrets'),
                                 ('Hidden\u200bcharacter', 'text_security')]:
            response = client.post('/v1/checks/' + stage, headers={'Authorization': 'Bearer test-key'},
                                   json={'application_id': 'demo', 'policy_version': 'conversation-v1',
                                         'content': content})
            result = response.json()
            assert response.status_code == 200
            assert result['evaluation_status'] == 'complete'
            checks = {c['checker_id']: c for c in result['checker_results']}
            assert set(checks) == {'pii', 'secrets', 'text_security', 'encoded_pii'}
            assert all(not c['simulated'] and not c['external'] for c in checks.values())
            if flagged:
                assert checks[flagged]['status'] == 'flagged'
                assert result['recommended_action'] == ('flag' if stage == 'input' else 'block')
            else:
                assert all(c['status'] == 'passed' for c in checks.values())
                assert result['recommended_action'] == 'allow'


def test_labeled_sensitive_data_setting_is_strict_and_requires_a_pii_rule():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Policy(application_id='demo',version='v1',rules=(),detect_labeled_sensitive_data='true')
    with pytest.raises(ValueError,match='require a pii'):
        Engine([Policy(application_id='demo',version='v1',rules=(),detect_labeled_sensitive_data=True)],{})
