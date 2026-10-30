"""Additive API regressions for bounded JSON-contract use cases.

These demonstrate narrow schema contracts, not semantic industry detection.
"""
import json
import pytest
from fastapi.testclient import TestClient
from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Policy, Rule


def check(schema, payload, stage):
    policy = Policy(application_id="catalog-api", version="v1", mode="enforce",
                    rules=(Rule(checker_id="schema", on_finding="block"),),
                    input_schema=schema, output_schema=schema)
    client = TestClient(create_app(engine=Engine([policy], default_checkers()),
                                   credentials={"catalog-key": "catalog-api"}))
    response = client.post(
        f"/v1/checks/{stage}", headers={"Authorization": "Bearer catalog-key"},
        json={"application_id": "catalog-api", "policy_version": "v1",
              "content_format": "json", "content": json.dumps(payload)})
    assert response.status_code == 200
    result = response.json()
    assert result["evaluation_status"] == "complete"
    return result["effective_action"]


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("coords,valid", [
    ([], True), ([[0, 0], [90, 180]], True),
    ([[0, 0], [90, 180], [-90, -180]], False), ([[0, 0, 0]], False)])
def test_uc068_declared_coordinate_count_and_shape(stage, coords, valid):
    schema = {"type": "object", "required": ["coordinates"],
              "additionalProperties": False, "properties": {"coordinates": {
                  "type": "array", "maxItems": 2, "items": {
                      "type": "array", "minItems": 2, "maxItems": 2,
                      "items": {"type": "number"}}}}}
    assert check(schema, {"coordinates": coords}, stage) == ("allow" if valid else "block")


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("value,valid", [
    ("BILLING", True), ("SUPPORT", True), ("billing", False),
    ("BILLING ", False), ("UNKNOWN", False)])
def test_uc079_exact_taxonomy_not_semantic_inference(stage, value, valid):
    schema = {"type": "object", "required": ["category"],
              "additionalProperties": False,
              "properties": {"category": {"enum": ["BILLING", "SUPPORT"]}}}
    assert check(schema, {"category": value}, stage) == ("allow" if valid else "block")


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("lat,lon,valid", [
    (-90, -180, True), (90, 180, True), (0, 0, True),
    (-90.00001, 0, False), (0, 180.00001, False)])
def test_uc087_coordinate_bounds(stage, lat, lon, valid):
    schema = {"type": "object", "required": ["lat", "lon"],
              "additionalProperties": False,
              "properties": {"lat": {"type": "number", "minimum": -90, "maximum": 90},
                             "lon": {"type": "number", "minimum": -180, "maximum": 180}}}
    assert check(schema, {"lat": lat, "lon": lon}, stage) == ("allow" if valid else "block")


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("payload,valid", [
    ({"order_id": "A1"}, True),
    ({"order_id": "A1", "admin": True}, False),
    ({"order_id": "A1", "role": "superuser"}, False),
    ({"order_id": "A1", "nested": {}}, False)])
def test_uc092_extra_fields(stage, payload, valid):
    schema = {"type": "object", "required": ["order_id"],
              "additionalProperties": False,
              "properties": {"order_id": {"type": "string"}}}
    assert check(schema, payload, stage) == ("allow" if valid else "block")
