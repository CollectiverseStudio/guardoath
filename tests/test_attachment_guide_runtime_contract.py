"""GuardOath attachment guide must describe the actual tenant and browser contract.

These regressions cover source-backed behavior, not production configuration
or a claim that unsupported file formats are inspected.
"""
from pathlib import Path

from fastapi.testclient import TestClient

from guardoath.app import create_app
from guardoath.tenant_record import TenantRecord


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "guardoath" / "static"


def _guide_attachments_section():
    page = TestClient(create_app(credentials={"test-key": "demo"})).get("/guide")
    assert page.status_code == 200
    return page.text.split('<section class="panel" id="attachments">', 1)[1].split(
        "</section>", 1
    )[0]


def test_legacy_missing_attachment_settings_are_auto_not_off():
    initial = TenantRecord(
        tenant_id="attachment-guide", display_name="Attachment guide", revision=9,
        input_check_text_security="allow",  # A tenant must have at least one enabled check.
    )
    stored = initial.model_dump()
    stored.pop("input_attachment_csv_mode")
    stored.pop("output_attachment_csv_mode")
    old_record = TenantRecord.model_validate(stored)
    assert old_record.input_attachment_csv_mode == "auto"
    assert old_record.output_attachment_csv_mode == "auto"

    stored["input_attachment_csv_mode"] = "off"
    explicit = TenantRecord.model_validate(stored)
    assert explicit.input_attachment_csv_mode == "off"
    assert explicit.output_attachment_csv_mode == "auto"


def test_served_developer_guide_explains_missing_vs_explicit_off_without_false_clean():
    guide = _guide_attachments_section()
    assert "existing saved tenant records <strong>missing</strong>" in guide
    assert "default that stage to <code>auto</code>" in guide
    assert "inspection activates only when real file bytes" in guide
    assert "An explicitly persisted <code>off</code> remains Off" in guide
    assert "not_inspected_policy_off" in guide
    assert "audit provenance before any targeted change" in guide
    assert "incomplete/Block" in guide
    assert "no browser attachment picker yet" not in guide


def test_browser_file_selectors_and_attachment_routes_match_served_guide():
    guide = _guide_attachments_section()
    dashboard = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    script = (STATIC / "dashboard.js").read_text(encoding="utf-8")

    for stage in ("input", "output"):
        assert f'id="{stage}-attachments" type="file"' in dashboard
        assert f"/v1/checks/{stage}/attachments" in guide
    assert "file selectors for authenticated tenants" in guide
    assert "async function selectedAttachmentBytes(stage)" in script
    assert "file.arrayBuffer()" in script
    assert "if (!files.length) return null;" in script
    assert "if (attachments_b64) request.attachments_b64 = attachments_b64;" in script
    assert '(attachments_b64 ? "/attachments" : "")' in script
    assert "a filename or chat mention alone does not activate" in guide
