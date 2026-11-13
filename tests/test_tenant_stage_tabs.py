"""Tenant stage tabs preserve authoritative settings and finding navigation."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "guardoath" / "static"


def test_stage_tabs_preserve_both_columns_and_original_settings():
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")
    js = (ROOT / "tenant_stage_tabs.js").read_text(encoding="utf-8")
    css = (ROOT / "dashboard.css").read_text(encoding="utf-8")
    dashboard = (ROOT / "dashboard.js").read_text(encoding="utf-8")
    for stage in ("input", "output", "both"):
        assert 'id="tenant-tab-' + stage + '"' in html
    assert 'src="/assets/tenant_stage_tabs.js"' in html
    assert 'id="tenant-checks"' in html
    assert 'id="tenant-stage-hidden-draft"' in html
    assert 'id="tenant-attachment-input-field"' in html
    assert 'id="tenant-attachment-output-field"' in html
    assert 'id="tenant-attachment-mode-input"' in html
    assert 'id="tenant-attachment-mode-output"' in html
    assert '#tenant-panel[data-stage-focus="input"] #tenant-attachment-output-field' in css
    assert '#tenant-panel[data-stage-focus="output"] #tenant-attachment-input-field' in css
    assert 'hiddenStage + "_attachment_csv_mode"' in dashboard
    assert '"CSV/TSV attachment inspection"' in dashboard
    assert 'guardoath:stage-view-changed' in js
    assert 'function updateStageHiddenDraftWarning(' in dashboard
    assert 'name.startsWith(hiddenStage + "_check_")' in dashboard
    assert 'panel.dataset.stageFocus = stage' in js
    assert 'guardoath:focus-stage' in js
    assert 'section.hidden' not in js
    assert 'fetch(' not in js
    assert '#check-table tr > :nth-child(3)' in css
    assert '#check-table tr > :nth-child(2)' in css
    assert 'new CustomEvent("guardoath:focus-stage", {detail: {stage}})' in dashboard
    assert 'for (const stage of ["input", "output"])' in dashboard
    assert 'id="tenant-save"' in html
