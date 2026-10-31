"""UC053: Unicode preservation at the GuardOath API boundary.

Transport checks only. No multilingual intent or safety classification is claimed.
"""
from fastapi.testclient import TestClient
import pytest

from guardoath.app import create_app
from guardoath.engine import Engine
from guardoath.models import CheckerResult, Policy, Rule


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("message", [
    "Hola, can you help? नमस्ते",
    "Bonjour — مرحبا — こんにちは",
    "中文 mixed with English and español",
    "naïve café, emoji 🔐 and combining e\u0301",
])
def test_uc053_mixed_unicode_is_preserved_exactly(stage, message):
    observed = []

    async def capture(request, policy, stage):
        observed.append(request.content)
        return CheckerResult(checker_id="capture", checker_version="test", status="passed")

    policy = Policy(application_id="unicode-demo", version="v1",
                    rules=(Rule(checker_id="capture"),))
    client = TestClient(create_app(
        engine=Engine([policy], {"capture": capture}),
        credentials={"unicode-key": "unicode-demo"}))
    response = client.post(
        "/v1/checks/" + stage,
        headers={"Authorization": "Bearer unicode-key"},
        json={"application_id": "unicode-demo", "policy_version": "v1",
              "content": message, "content_format": "text"})
    assert response.status_code == 200
    assert observed == [message]
