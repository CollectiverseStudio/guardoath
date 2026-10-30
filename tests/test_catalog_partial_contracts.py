"""Additional API contract regressions; no semantic domain capability claims."""
import pytest
from test_catalog_partial_boundaries import check


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("value,valid", [
    (None, True), (3, True), (0, True), ("3", False), (True, False), (-1, False)])
def test_uc074_null_and_missing_feature_contract(stage, value, valid):
    schema = {"type": "object", "properties": {
        "feature": {"type": ["integer", "null"], "minimum": 0}},
        "required": ["feature"], "additionalProperties": False}
    assert check(schema, {"feature": value}, stage) == ("allow" if valid else "block")


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("value,valid", [
    (0, True), (1, True), (100, True), (-1, False),
    (101, False), ("100", False)])
def test_uc085_sensor_range_not_noise_filtering(stage, value, valid):
    schema = {"type": "object", "properties": {
        "reading": {"type": "number", "minimum": 0, "maximum": 100}},
        "required": ["reading"], "additionalProperties": False}
    assert check(schema, {"reading": value}, stage) == ("allow" if valid else "block")


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("age,years", [(20, 0), (30, 10), (20, 21), (0, 1), (30, -1)])
def test_uc078_cross_field_consistency_gap(stage, age, years):
    schema = {"type": "object", "properties": {
        "age": {"type": "integer", "minimum": 0},
        "years": {"type": "integer", "minimum": 0}},
        "required": ["age", "years"], "additionalProperties": False}
    assert check(schema, {"age": age, "years": years}, stage) == (
        "allow" if age >= 0 and years >= 0 else "block")
    # A schema's simple bounds do not prove years <= age.


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("payload,valid", [
    ({"mode": "READ"}, True),
    ({"mode": "WRITE", "approved": True}, True),
    ({"mode": "WRITE"}, False),
    ({"mode": "WRITE", "approved": False}, False),
    ({"mode": "DELETE"}, False),
    ({"mode": "READ", "admin": True}, False)])
def test_uc080_static_conditional_policy(stage, payload, valid):
    schema = {"type": "object", "properties": {
        "mode": {"enum": ["READ", "WRITE"]}, "approved": {"type": "boolean"}},
        "required": ["mode"], "additionalProperties": False,
        "if": {"properties": {"mode": {"const": "WRITE"}}, "required": ["mode"]},
        "then": {"properties": {"approved": {"const": True}},
                 "required": ["approved"]}}
    assert check(schema, payload, stage) == ("allow" if valid else "block")


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("payload,valid", [
    ({"id": "A1"}, True),
    ({"id": "A1", "privileged": True}, False),
    ({"id": "A1", "roles": []}, False),
    ({"id": "A1", "extra": None}, False),
    ({"id": 123}, False), ({}, False)])
def test_uc092_overposting_and_missing_fields(stage, payload, valid):
    schema = {"type": "object", "properties": {"id": {"type": "string"}},
              "required": ["id"], "additionalProperties": False}
    assert check(schema, payload, stage) == ("allow" if valid else "block")
