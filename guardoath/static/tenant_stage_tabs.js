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
