"use strict";
// Diagnostic comparison only. Never authorizes the agent or verifies a log source.
(() => {
  function start() {
    const proposed = document.getElementById("agent-proposed");
    const observed = document.getElementById("agent-observed");
    const status = document.getElementById("agent-activity-result");
    const button = document.getElementById("agent-activity-check");
    const samples = {
      proposed: {tool: "shell.run", target: "host-42", arguments: {command: "Get-Process"}},
      matching: {tool: "shell.run", target: "host-42", arguments: {command: "Get-Process"}},
      changed: {tool: "shell.run", target: "host-42", arguments: {command: "Clear-EventLog -LogName Security"}}
    };
    for (const kind of ["matching", "changed", "missing"]) {
      document.getElementById("agent-sample-" + kind).addEventListener("click", () => {
        proposed.value = JSON.stringify(samples.proposed, null, 2);
        observed.value = kind === "missing" ? "" : JSON.stringify(samples[kind], null, 2);
        status.textContent = "Synthetic example loaded. Connect a tenant, then compare.";
      });
    }
    button.addEventListener("click", async () => {
      status.textContent = "Comparing the supplied reports...";
      button.disabled = true;
      try {
        const intended = JSON.parse(proposed.value);
        const seen = observed.value.trim() ? JSON.parse(observed.value) : null;
        const result = await api("/v1/agent-activity/compare", {proposed: intended, observed: seen});
        const descriptions = {
          matching_unverified: "Supplied actions match, but execution is NOT verified.",
          mismatch: "Mismatch: the proposed action differs from the supplied execution report.",
          no_observation: "No execution report was provided; execution cannot be verified."
        };
        status.textContent = (descriptions[result.status] || "Comparison incomplete.") +
          " No tool is executed or authorized. Finding IDs: " + (result.finding_ids.join(", ") || "none");
      } catch (_error) {
        status.textContent = "Comparison could not complete. Connect an authenticated tenant and supply bounded valid JSON. No action was authorized.";
      } finally {
        button.disabled = false;
      }
    });
  }
  document.addEventListener("DOMContentLoaded", start);
})();
