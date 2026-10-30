"""UC041 structured order contract, not semantic e-commerce intent."""
import asyncio
import json
import pytest
from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage

SCHEMA = {"type":"object","required":["intent","sku","quantity"],"additionalProperties":False,
          "properties":{"intent":{"enum":["purchase","return"]},
                        "sku":{"type":"string","minLength":1,"maxLength":32},
                        "quantity":{"type":"integer","minimum":1,"maximum":50}}}

@pytest.mark.parametrize("stage",[Stage.INPUT,Stage.OUTPUT])
@pytest.mark.parametrize("record,valid",[
    ({"intent":"purchase","sku":"A-1","quantity":1},True),
    ({"intent":"return","sku":"A-1","quantity":50},True),
    ({"intent":"unknown","sku":"A-1","quantity":1},False),
    ({"intent":"purchase","sku":"","quantity":1},False),
    ({"intent":"purchase","sku":"A-1","quantity":0},False),
    ({"intent":"purchase","sku":"A-1","quantity":51},False),
    ({"intent":"purchase","sku":"A-1","quantity":"2"},False),
    ({"intent":"purchase","quantity":1},False),
    ({"intent":"purchase","sku":"A-1","quantity":1,"role":"admin"},False)])
def test_uc041_structured_order_contract_not_semantic_intent(stage,record,valid):
    policy=Policy(application_id="order-demo",version="v1",mode="enforce",
                  rules=(Rule(checker_id="schema",on_finding=Action.BLOCK),),
                  input_schema=SCHEMA,output_schema=SCHEMA)
    result=asyncio.run(Engine([policy],default_checkers()).check(CheckRequest(
        application_id="order-demo",policy_version="v1",
        content_format="json",content=json.dumps(record)),stage))
    assert result.evaluation_status=="complete"
    assert result.effective_action==(Action.ALLOW if valid else Action.BLOCK)
    assert bool(result.findings) is not valid
