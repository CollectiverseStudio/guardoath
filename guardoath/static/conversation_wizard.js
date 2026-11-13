"use strict";
// Progressive enhancement only: navigation never changes tenant policies, messages,
// check decisions, or the existing input-before-output enforcement.
(function () {
  const init = () => {
    const nav = document.getElementById("conversation-wizard");
    if (!nav) return;
    const ids = ["wizard-input", "wizard-input-check", "wizard-output", "wizard-output-check"];
    const sections = ids.map(id => document.getElementById(id));
    if (sections.some(section => !section)) return;
    const tabs = Array.from(nav.querySelectorAll("[data-wizard-step]"));
    const status = document.getElementById("wizard-status");
    const previous = document.getElementById("wizard-back");
    const next = document.getElementById("wizard-next");
    const start = document.getElementById("wizard-start");
    const allSteps = document.getElementById("wizard-show-all");
    const prepared = document.getElementById("batch-run");
    // Explain what each step does and when the customer may proceed.
    // Navigation is presentation only; the backend Input receipt remains authoritative.
    const guidance = document.createElement("p");
    guidance.id = "wizard-step-guidance";
    guidance.className = "hint";
    guidance.setAttribute("role", "status");
    nav.append(guidance);
    const instructions = [
      "Enter the customer's message and choose its format. An example fills the message but does not choose which checks run.",
      "Run Input checks. A detected issue may still be Allowed; review the finding and effective decision before continuing.",
      "After Input validation permits it, paste the proposed AI answer. GuardOath does not generate the answer.",
      "Run Output checks and inspect whether the answer is allowed, redacted, or held before release."
    ];
    // Default to the established full workflow until a customer opts into guided focus.
    let current = null;
    const showAll = (message = "All four steps visible. Start the guided test to focus.") => {
      current = null;
      sections.forEach(section => { section.hidden = false; });
      tabs.forEach(tab => {
        tab.setAttribute("aria-current", "false");
        tab.setAttribute("aria-pressed", "false");
      });
      previous.disabled = true;
      next.disabled = true;
      status.textContent = message;
      guidance.textContent = "Guided mode is available: choose Start guided test to focus on one stage at a time. Input must be validated before an Output result can be released.";
    };
    const show = index => {
      if (!Number.isInteger(index) || index < 0 || index >= sections.length) return;
      current = index;
      // Guided navigation restores single-step view. The prepared example
      // shortcut can expose all stages but cannot override Input validation.
      if (prepared?.open) prepared.open = false;
      sections.forEach((section, i) => { section.hidden = i !== current; });
      tabs.forEach((tab, i) => {
        tab.setAttribute("aria-current", i === current ? "step" : "false");
        tab.setAttribute("aria-pressed", i === current ? "true" : "false");
      });
      previous.disabled = current === 0;
      next.disabled = current === sections.length - 1;
      status.textContent = "Step " + (current + 1) + " of " + sections.length;
      guidance.textContent = instructions[current];
    };
    tabs.forEach((tab, i) => tab.addEventListener("click", () => show(i)));
    previous.addEventListener("click", () => show(current - 1));
    next.addEventListener("click", () => { if (current !== null) show(current + 1); });
    start?.addEventListener("click", () => show(0));
    allSteps?.addEventListener("click", () => {
      if (prepared?.open) prepared.open = false;
      showAll();
    });
    prepared?.addEventListener("toggle", () => {
      if (!prepared.open) {
        if (current === null) showAll();
        else show(current);
        return;
      }
      showAll("Prepared example: all four steps visible");
    });
    // A completed click may still result in a blocked input. Let the operator
    // see the findings; do not auto-advance past a security decision.
    const results = document.getElementById("input-results");
    const note = document.createElement("p");
    note.className = "hint";
    note.id = "wizard-finding-context";
    note.setAttribute("role", "status");
    if (results && results.parentElement) results.parentElement.insertBefore(note, results);
    const explain = () => {
      const select = document.getElementById("input-example-check");
      const chosen = select?.value && select.selectedOptions?.[0]?.textContent
        ? select.selectedOptions[0].textContent : "No example category selected";
      note.textContent = "Selected example: " + chosen + ". This chooses sample content, not a single check to execute. All enabled Input checks run. Findings below identify the actual check and rule; unrelated integrity or safety findings are separate.";
    };
    document.getElementById("input-samples")?.addEventListener("change", explain);
    explain();
    showAll();
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
}());
