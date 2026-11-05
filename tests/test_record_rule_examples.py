"""Generated examples of a record's comparisons, against the whole record (2026-11-05).

The example of a comparison is reported by that comparison only, and its look-alike by none of the record's
comparisons, on each stage the comparison runs on; both are checked here with the real engine on both stages. When no
such document exists the generator says why (example_unavailable / look_alike_unavailable) and never offers a
violating document as a look-alike. Scenarios are those of docs/field-comparison-portfolio-evidence.md and
docs/field-comparison-witness-evidence.md, plus mixed types, nested fields, "different from" chains and a time bound.
"""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, FieldComparison, Policy, Rule, Stage
from guardoath.record_rules import MAX_CONTENT_CHARS, MAX_LISTED_BYTES, MAX_RECORD_WORK, UNAVAILABLE, record_rules

CHECKERS = default_checkers()


def comparisons(*relations, value_type="number"):
    types = value_type if isinstance(value_type, (list, tuple)) else [value_type] * len(relations)
    out = []
    for i, relation in enumerate(relations):
        left, right, operator = relation[:3]
        stages = relation[3] if len(relation) > 3 else (Stage.INPUT, Stage.OUTPUT)
        out.append(FieldComparison(rule_id=f"c{i}", left=left, right=right, operator=operator,
                                   value_type=types[i], stages=stages))
    return tuple(out)


def generated(items):
    return record_rules(SimpleNamespace(comparisons=items, required_sections=()))


def run(items, text, stage):
    policy = Policy(application_id="examples", version="v1", mode="enforce",
                    rules=(Rule(checker_id="cross_field", required=True, on_finding="block"),), comparisons=items)
    request = CheckRequest(application_id="examples", policy_version="v1", content=text, content_format="json")
    return asyncio.run(Engine([policy], CHECKERS).check(request, stage))


def assert_examples_are_exact(items):
    rules = generated(items)
    for comparison in items:
        rule = rules["business." + comparison.rule_id]
        for stage in comparison.stages:
            if rule["example"] is not None:
                reported = run(items, rule["example"], stage)
                assert reported.evaluation_status == "complete"
                assert [f.rule_id for f in reported.findings] == ["business." + comparison.rule_id], comparison.rule_id
            if rule["look_alike"] is not None:
                clean = run(items, rule["look_alike"], stage)
                assert clean.evaluation_status == "complete" and clean.findings == (), comparison.rule_id
                assert clean.recommended_action == Action.ALLOW == clean.effective_action
                assert [c.status for c in clean.checker_results] == ["passed"]
        for field in ("example", "look_alike"):
            missing = rule[field + "_unavailable"]
            assert (rule[field] is None) == (missing is not None), (comparison.rule_id, field)
            if missing is not None:
                assert missing["code"] in UNAVAILABLE and missing["reason"] == UNAVAILABLE[missing["code"]]
    return rules


@pytest.mark.parametrize("relations", [
    [("/shipped_qty", "/ordered_qty", "le"), ("/delivered_qty", "/shipped_qty", "le"),
     ("/refund_amount", "/purchase_amount", "le")],                                  # MFC-001
    [("/requested", "/stock", "le"), ("/stock", "/warehouse", "le")],                 # MFC-002
    [("/a", "/b", "le"), ("/b", "/c", "le"), ("/c", "/a", "le")],                     # MFC-007 equality cycle
    [("/a", "/b", "ne"), ("/b", "/c", "ne"), ("/a", "/c", "ne"), ("/a", "/c", "le")],
    [("/a~1b", "/c~0d", "lt"), ("/a/b", "/c", "gt")],                                 # escaped pointers
    [("/order/total", "/order/limit", "le"), ("/order/discount", "/order/total", "lt")],
], ids=["chain-refund", "chain", "equality-cycle", "different-from", "escaped", "nested-object"])
def test_feasible_records_get_an_example_and_a_look_alike_for_every_comparison(relations):
    rules = assert_examples_are_exact(comparisons(*relations))
    assert all(rule["example"] and rule["look_alike"] for rule in rules.values())


def test_a_comparison_implied_by_the_others_has_no_example_and_says_why():
    # low < x and x < high imply low < high, so low < high cannot fail while the other two pass (2026-11-05: this case
    # was wrongly listed with the feasible records; the generator was right).
    rules = assert_examples_are_exact(comparisons(("/low", "/x", "lt"), ("/x", "/high", "lt"), ("/low", "/high", "lt")))
    assert rules["business.c0"]["example"] and rules["business.c1"]["example"]
    assert rules["business.c2"]["example_unavailable"]["code"] == "cannot_fail_alone"
    assert all(rule["look_alike"] for rule in rules.values())


def test_dates_are_solved_too():
    items = comparisons(("/start", "/end", "lt"), ("/end", "/due", "le"), value_type="date")
    rules = assert_examples_are_exact(items)
    assert all(rule["example"] and rule["look_alike"] for rule in rules.values())


def test_a_strict_contradiction_has_examples_but_no_look_alike_and_says_why():
    rules = assert_examples_are_exact(comparisons(("/a", "/b", "lt"), ("/b", "/a", "lt")))   # MFC-007 strict
    for rule in rules.values():
        assert rule["example"] is not None and rule["look_alike"] is None
        assert rule["look_alike_unavailable"]["code"] == "cannot_all_pass"


def test_a_strict_self_comparison_says_why_instead_of_a_document():
    rules = assert_examples_are_exact(comparisons(("/a", "/a", "le"), ("/a", "/a", "lt")))   # MFC-009
    assert rules["business.c0"]["example_unavailable"]["code"] == "cannot_fail_alone"      # a <= a always passes
    assert rules["business.c1"]["example"] is not None                                      # a < a always fails
    assert all(rule["look_alike_unavailable"]["code"] == "cannot_all_pass" for rule in rules.values())


def test_equal_and_different_on_the_same_fields_cannot_both_pass():
    rules = assert_examples_are_exact(comparisons(("/a", "/b", "eq"), ("/a", "/b", "ne")))
    assert all(rule["look_alike_unavailable"]["code"] == "cannot_all_pass" for rule in rules.values())


def test_a_field_compared_as_a_number_and_as_a_date_says_why():
    items = comparisons(("/a", "/b", "le"), ("/a", "/c", "le"), value_type=["number", "date"])
    rules = assert_examples_are_exact(items)
    assert all(rule["look_alike_unavailable"]["code"] == "mixed_types" for rule in rules.values())


def test_a_field_that_is_also_an_object_of_other_fields_says_why():
    rules = assert_examples_are_exact(comparisons(("/a", "/b", "le"), ("/a/x", "/c", "le")))
    assert all(rule["look_alike_unavailable"]["code"] == "field_inside_field" for rule in rules.values())


def test_comparisons_on_other_stages_do_not_constrain_the_examples():
    items = comparisons(("/a", "/b", "lt", (Stage.INPUT,)), ("/b", "/a", "lt", (Stage.OUTPUT,)))
    rules = assert_examples_are_exact(items)
    assert all(rule["example"] and rule["look_alike"] for rule in rules.values())


def test_the_reason_texts_never_repeat_configured_values():
    items = comparisons(("/secret_field_name", "/b", "lt"), ("/b", "/secret_field_name", "lt"))
    for rule in generated(items).values():
        text = json.dumps([rule["example_unavailable"], rule["look_alike_unavailable"]])
        assert "secret_field_name" not in text


def test_fifty_different_from_comparisons_stay_within_the_search_budget_and_time():
    items = comparisons(*[(f"/v{i % 8}", f"/v{(i + 1 + i // 8) % 8}", "ne") for i in range(50)])
    started = time.perf_counter()
    rules = generated(items)
    assert time.perf_counter() - started < 5
    assert len(rules) == 50 and MAX_RECORD_WORK > 0
    for rule in rules.values():
        for field in ("example", "look_alike"):
            assert (rule[field] is None) == (rule[field + "_unavailable"] is not None)
    assert_examples_are_exact(items[:12])


@pytest.mark.parametrize("shape", ["independent", "one chain"])
def test_a_thousand_comparisons_are_listed_within_the_record_budgets(shape):
    # 2026-11-05: no comparison count limit. Generation is bounded per record instead: past the listed-text budget a
    # rule says listing_limit (or search_limit) rather than showing a document, and every listed document is exact.
    # The fields are nested in objects of at most 500, because one JSON object holds at most 1,000 fields
    # (guardoath.json_data.MAX_CONTAINER_ITEMS; see the json_limit test below).
    if shape == "independent":
        items = comparisons(*[(f"/g{i // 250}/left_{i}", f"/g{i // 250}/right_{i}", ["le", "lt", "ge", "gt", "eq", "ne"][i % 6])
                              for i in range(1000)])
    else:
        items = comparisons(*[(f"/g{i // 500}/f{i}", f"/g{(i + 1) // 500}/f{i + 1}", "lt") for i in range(1000)])
    started = time.perf_counter()
    rules = generated(items)
    assert time.perf_counter() - started < 15
    assert len(rules) == 1000
    listed = 0
    for rule in rules.values():
        for field in ("example", "look_alike"):
            if rule[field] is None:
                assert rule[field + "_unavailable"]["code"] in {"listing_limit", "search_limit"}
            else:
                listed += len(rule[field].encode("utf-8"))
    assert 0 < listed <= MAX_LISTED_BYTES
    first = rules["business.c0"]
    assert first["example"] and first["look_alike"]
    assert [f.rule_id for f in run(items, first["example"], Stage.INPUT).findings] == ["business.c0"]
    assert run(items, first["look_alike"], Stage.OUTPUT).findings == ()


def test_the_content_limit_is_the_checks_own():
    CheckRequest(application_id="a", policy_version="v", content="x" * MAX_CONTENT_CHARS)
    with pytest.raises(ValueError):
        CheckRequest(application_id="a", policy_version="v", content="x" * (MAX_CONTENT_CHARS + 1))


def test_examples_longer_than_one_check_accepts_say_so():
    # 1,500 independent comparisons use 3,000 fields: even compact, a document with all of them is over 32,000 chars.
    items = comparisons(*[(f"/left_{i}", f"/right_{i}", "le") for i in range(1500)])
    started = time.perf_counter()
    rules = generated(items)
    assert time.perf_counter() - started < 15
    for rule in rules.values():
        assert rule["example"] is None and rule["look_alike"] is None
        assert rule["example_unavailable"]["code"] == rule["look_alike_unavailable"]["code"] == "content_limit"


def test_more_fields_in_one_object_than_one_check_accepts_says_so():
    # 600 comparisons on 1,200 top-level fields: one JSON object holds at most 1,000 fields, so the checker refuses
    # every document that has them all (business.invalid_json). No example is offered, and the reason says that no
    # message can pass these comparisons (2026-11-05: Tests on the PC, 1,000 comparisons at the top level).
    from guardoath.json_data import MAX_CONTAINER_ITEMS
    items = comparisons(*[(f"/left_{i}", f"/right_{i}", "le") for i in range(600)])
    assert 2 * len(items) > MAX_CONTAINER_ITEMS
    rules = generated(items)
    for rule in rules.values():
        assert rule["example"] is None and rule["look_alike"] is None
        assert rule["example_unavailable"]["code"] == rule["look_alike_unavailable"]["code"] == "json_limit"
    assert "1,000 fields in one object" in UNAVAILABLE["json_limit"]
    # The same comparisons with their fields in two objects get exact examples.
    nested = comparisons(*[(f"/part{i % 2}/left_{i}", f"/part{i % 2}/right_{i}", "le") for i in range(600)])
    first = generated(nested)["business.c0"]
    assert first["example"] and first["look_alike"]
    assert [f.rule_id for f in run(nested, first["example"], Stage.INPUT).findings] == ["business.c0"]
    assert run(nested, first["look_alike"], Stage.OUTPUT).findings == ()
