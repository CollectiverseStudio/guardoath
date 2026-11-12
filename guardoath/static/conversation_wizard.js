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
    const prepared = document.getElementById("batch-run");
    let current = 0;
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
    };
    tabs.forEach((tab, i) => tab.addEventListener("click", () => show(i)));
    previous.addEventListener("click", () => show(current - 1));
    next.addEventListener("click", () => show(current + 1));
    prepared?.addEventListener("toggle", () => {
      if (!prepared.open) { show(current); return; }
      sections.forEach(section => { section.hidden = false; });
      tabs.forEach(tab => {
        tab.setAttribute("aria-current", "false");
        tab.setAttribute("aria-pressed", "false");
      });
      previous.disabled = true;
      next.disabled = true;
      status.textContent = "Prepared example: all four steps visible";
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
    show(0);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
}());
