"use strict";
// Stage focus is presentation only. Both settings remain mounted and saved by
// the existing tenant form; never mutate policy or validation decisions.
(function () {
  const init = () => {
    const panel = document.getElementById("tenant-panel");
    const buttons = {
      input: document.getElementById("tenant-tab-input"),
      output: document.getElementById("tenant-tab-output"),
      both: document.getElementById("tenant-tab-both")
    };
    if (!panel || Object.values(buttons).some(button => !button)) return;
    const description = document.getElementById("tenant-stage-description");
    const select = stage => {
      if (!Object.hasOwn(buttons, stage)) return;
      panel.dataset.stageFocus = stage;
      Object.entries(buttons).forEach(([name, button]) =>
        button.setAttribute("aria-pressed", name === stage ? "true" : "false"));
      description.textContent = stage === "both"
        ? "Showing Input and Output settings together."
        : "Showing " + (stage === "input" ? "Input (user message)" : "Output (AI answer)") +
          " settings. The other stage is only hidden, not disabled. Edits in either stage remain unsaved until Save checks applies both stages.";
      panel.dispatchEvent(new CustomEvent("guardoath:stage-view-changed", {detail: {stage}}));
    };
    Object.entries(buttons).forEach(([stage, button]) =>
      button.addEventListener("click", () => select(stage)));
    panel.addEventListener("guardoath:focus-stage", event => select(event.detail?.stage));
    // Guided setup changes only presentation. The existing tenant fields,
    // Save checks revision, and validation engine remain authoritative.
    const start = document.getElementById("checks-wizard-start");
    const exit = document.getElementById("checks-wizard-exit");
    const progress = document.getElementById("checks-wizard-progress");
    const guide = document.getElementById("checks-wizard-guidance");
    const navigation = document.getElementById("checks-wizard-navigation");
    const back = document.getElementById("checks-wizard-back");
    const next = document.getElementById("checks-wizard-next");
    const wizardSave = document.getElementById("checks-wizard-save");
    const tenantSave = document.getElementById("tenant-save");
    const comparisonTools = document.getElementById("checks-wizard-comparison-tools");
    const comparisonSummary = document.getElementById("checks-wizard-comparison-summary");
    const openComparisons = document.getElementById("checks-wizard-open-comparisons");
    const comparisonDetails = document.getElementById("comparison-configuration");
    // Read the currently selected tenant-form draft, not a hardcoded default.
    // The revision-controlled Save checks remains the only persistence action.
    const comparisonAction = stage => {
      const value = document.getElementById("tenant-check-" + stage + "-cross_field")?.value;
      return {block: "Block", flag: "Flag", allow: "Allow", off: "Off", "": "Off"}[value] || "Unavailable";
    };
    const updateComparisonSummary = () => {
      if (!comparisonSummary) return;
      const input = comparisonAction("input");
      const output = comparisonAction("output");
      comparisonSummary.textContent = "Draft Field comparisons check — Input: " + input +
        " · Output: " + output + ". " +
        ((input === "Off" || output === "Off")
          ? "Rules do not execute in a stage whose check is Off. "
          : "") +
        "Changing definitions or actions requires Save checks before the tenant uses them.";
    };
    const steps = [
      {stage: "input", name: "Input protection", note: "Step 1: Choose checks for the user's message BEFORE sending it to AI. Block holds the message; Flag and Allow report findings without blocking."},
      {stage: "output", name: "Output protection", note: "Step 2: Choose checks for the AI answer BEFORE returning it to the user. Settings for both stages remain pending until saved."},
      {stage: "both", name: "Review and save", note: "Step 3: Review both stages. Select Save Input and Output checks here to persist with the tenant revision. Navigation never saves or disables checks."}
    ];
    let wizardStep = -1;
    const renderWizard = () => {
      if (!start || !exit || !progress || !guide || !navigation || !back || !next || !wizardSave || !tenantSave) return;
      const active = wizardStep >= 0;
      start.hidden = active;
      exit.hidden = !active;
      navigation.hidden = !active;
      wizardSave.hidden = !active || wizardStep !== 2;
      if (comparisonTools) comparisonTools.hidden = !active || wizardStep !== 2;
      if (active && wizardStep === 2) updateComparisonSummary();
      if (!active) {
        progress.textContent = "Guided setup available";
        guide.textContent = "For a simple chat application, start with Input protection and then review Output protection.";
        return;
      }
      const step = steps[wizardStep];
      select(step.stage);
      progress.textContent = "Step " + (wizardStep + 1) + " of 3 · " + step.name;
      guide.textContent = step.note;
      back.disabled = wizardStep === 0;
      next.hidden = wizardStep === 2;
      next.textContent = wizardStep === 0 ? "Next: Output protection" : "Next: Review and save";
    };
    start?.addEventListener("click", () => { wizardStep = 0; renderWizard(); });
    back?.addEventListener("click", () => {
      if (wizardStep > 0) { wizardStep -= 1; renderWizard(); }
    });
    next?.addEventListener("click", () => {
      if (wizardStep >= 0 && wizardStep < steps.length - 1) { wizardStep += 1; renderWizard(); }
    });
    // A customer can also use the existing Input, Output, and Both tabs.
    // Synchronize the step label with the actual column in view, including
    // navigation from a finding, so the wizard never describes the wrong stage.
    panel.addEventListener("guardoath:stage-view-changed", event => {
      if (wizardStep < 0) return;
      const selected = steps.findIndex(step => step.stage === event.detail?.stage);
      if (selected !== -1 && selected !== wizardStep) {
        wizardStep = selected;
        renderWizard();
      }
    });
    // Field Comparison definitions live in the existing optional editor.
    // Opening it neither stages nor saves rules, and all protected host
    // references remain read-only in that same tenant UI.
    openComparisons?.addEventListener("click", () => {
      if (wizardStep !== 2 || !comparisonDetails) return;
      comparisonDetails.open = true;
      const heading = comparisonDetails.querySelector("summary");
      heading?.focus?.();
      comparisonDetails.scrollIntoView?.({block: "start", behavior: "auto"});
    });
    // Show what will execute at each stage from the current unsaved draft.
    panel.addEventListener("change", event => {
      if (wizardStep === 2 && ["tenant-check-input-cross_field", "tenant-check-output-cross_field"].includes(event.target?.id)) {
        updateComparisonSummary();
      }
    });
    // Save uses the exact existing revision-checked tenant action. There is
    // deliberately no second policy store, API route, or implicit save.
    wizardSave?.addEventListener("click", () => {
      if (wizardStep !== 2 || !tenantSave || tenantSave.disabled) return;
      tenantSave.click();
      document.getElementById("tenant-message")?.scrollIntoView?.({block: "nearest"});
    });
    exit?.addEventListener("click", () => {
      wizardStep = -1; select("both"); renderWizard();
    });
    // The dashboard emits focus-stage=both on every successful tenant connection.
    // Reset the wizard too, so it never labels a different tenant's view as Step 2.
    panel.addEventListener("guardoath:focus-stage", event => {
      if (event.detail?.stage === "both" && wizardStep >= 0) {
        wizardStep = -1;
        renderWizard();
      }
    });
    select("both");
    renderWizard();
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
}());
