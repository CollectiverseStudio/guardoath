"""Standalone regression cases for document-field contracts, not document OCR.

These tests intentionally operate on extracted JSON only and do not assert that
GuardOath can read receipts, invoices, IDs, medical scans or PDF images.
"""
import asyncio
import json

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage


SCHEMA = {
    "type": "object",
    "required": ["merchant", "date", "total"],
    "additionalProperties": False,
    "properties": {
        "merchant": {"type": "string", "minLength": 1},
        "date": {"type": "string", "format": "date"},
        "total": {"type": "number", "minimum": 0},
    },
}


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("record,expected", [
    ({"merchant": "Shop", "date": "2026-10-30", "total": 12.50}, "allow"),
    ({"merchant": "Shop", "date": "2026-10-30"}, "block"),
    ({"merchant": "", "date": "2026-10-30", "total": 12.50}, "block"),
    ({"merchant": "Shop", "date": "2026-13-30", "total": 12.50}, "block"),
    ({"merchant": "Shop", "date": "2026-10-30", "total": -1}, "block"),
    ({"merchant": "Shop", "date": "2026-10-30", "total": 12.50, "hidden": 1}, "block"),
])
def test_uc033_extracted_receipt_fields_only(stage, record, expected):
    policy = Policy(application_id="receipt-demo", version="v1",
                    rules=(Rule(checker_id="schema", on_finding=Action.BLOCK),),
                    input_schema=SCHEMA, output_schema=SCHEMA)
    engine = Engine([policy], default_checkers())
    result = asyncio.run(engine.check(CheckRequest(
        application_id="receipt-demo", policy_version="v1",
        content_format="json", content=json.dumps(record)), stage))
    assert result.evaluation_status == "complete"
    assert result.effective_action == expected
