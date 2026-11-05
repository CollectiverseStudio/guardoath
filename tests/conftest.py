"""Shared test setup.

Sigma (2026-11-04): a Sigma evaluation has a 1-second wall-clock limit (guardoath/sigma_rules/budgets.py). Tests that
check what a rule matches ran under that real clock, so a pause of the test machine failed them (local run 2026-11-04:
a rule with no regular expression hit the limit). In those tests the Sigma clock is frozen, so the limit cannot expire
and only what matches is tested; every other assertion is unchanged. The modules that test the limit itself keep the
real clock or set their own (each test's own monkeypatch runs after this fixture and replaces it).
"""
import pytest

REAL_CLOCK_MODULES = frozenset({"test_sigma_event_budgets", "test_sigma_regex_deadlines", "test_sigma_routing",
                                "test_sigma_routing_plan"})


@pytest.fixture(autouse=True)
def frozen_sigma_clock(request, monkeypatch):
    module = request.node.module.__name__.rsplit(".", 1)[-1] if getattr(request.node, "module", None) else ""
    if module.startswith("test_sigma") and module not in REAL_CLOCK_MODULES:
        from guardoath.sigma_rules import budgets
        now = budgets.monotonic()
        monkeypatch.setattr(budgets, "monotonic", lambda: now)
    yield
