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
          " settings. The other stage remains saved and unchanged. Save checks applies both stages.";
    };
    Object.entries(buttons).forEach(([stage, button]) =>
      button.addEventListener("click", () => select(stage)));
    panel.addEventListener("guardoath:focus-stage", event => select(event.detail?.stage));
    select("both");
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
}());
