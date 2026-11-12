"""Guided dashboard navigation must not bypass validation or hide errors."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "guardoath" / "static"


def test_conversation_wizard_has_four_separate_steps_and_existing_action_controls():
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")
    js = (ROOT / "conversation_wizard.js").read_text(encoding="utf-8")
    for element in ("wizard-input", "wizard-input-check", "wizard-output", "wizard-output-check"):
        assert 'id="' + element + '"' in html
        assert element in js
    for element in ("run-input", "run-output", "input-results", "output-results",
                    "input-gate", "result-summary", "run-error"):
        assert 'id="' + element + '"' in html
    assert 'src="/assets/conversation_wizard.js"' in html
    assert 'id="wizard-start"' in html
    assert 'id="wizard-show-all"' in html
    assert "showAll();" in js and "start?.addEventListener" in js
    assert "aria-current" in js
    assert "section.hidden = i !== current" in js
    assert "fetch(" not in js and "localStorage" not in js
    assert "do not auto-advance" in js


def test_sigma_optional_editor_is_guarded_on_dashboard():
    js = (ROOT / "dashboard.js").read_text(encoding="utf-8")
    assert 'typeof window.sigmaTenant.render === "function"' in js
    assert 'typeof window.sigmaTenant.delta === "function"' in js
    assert 'id = "sigma-editor-unavailable"' in js
