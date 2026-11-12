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
    let current = 0;
    const show = index => {
      if (!Number.isInteger(index) || index < 0 || index >= sections.length) return;
      current = index;
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
    // A completed click may still result in a blocked input. Let the operator
    // see the findings; do not auto-advance past a security decision.
    show(0);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
}());
