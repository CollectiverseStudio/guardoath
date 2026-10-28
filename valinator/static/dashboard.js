"use strict";
const $ = id => document.getElementById(id);
let key = "", application = null, revision = 0, busy = false;
const currentPolicy = () => application?.policies.find(p => p.version === $("policy").value);
const groupLabels = {passed:"Passed", findings:"Findings", incomplete:"Incomplete", simulated:"Simulated", partial:"Partial coverage", disabled:"Disabled", not_applicable:"Not applicable", not_implemented:"Not implemented"};
function reset() {
  revision++;
  $("answer").hidden = true; $("answer").textContent = "";
  $("answer-status").textContent = "Not checked"; $("answer-status").className = "pill";
  $("answer-note").textContent = "An answer appears here when the selected checks complete without findings.";
  $("result-status").textContent = "Ready to test"; $("result-status").className = "pill";
  $("result-summary").textContent = "Run the checks to inspect this conversation.";
  for (const stage of ["input", "output"]) { const box = $(stage + "-results"); box.replaceChildren();
    const title = document.createElement("h3"); title.textContent = stage === "input" ? "Input checks" : "Output checks";
    const note = document.createElement("p"); note.textContent = "Waiting for a check."; note.className = "muted"; box.append(title, note); }
  $("run-error").textContent = "";
}
async function api(path, body, token = key) {
  const response = await fetch(path, {method: body ? "POST" : "GET", cache: "no-store",
    headers: {"Authorization": "Bearer " + token, ...(body ? {"Content-Type": "application/json"} : {})},
    ...(body ? {body: JSON.stringify(body)} : {})});
  if (!response.ok) throw new Error(response.status === 401 ? "The API key was not accepted." : response.status === 422 ? "Check the required fields, content length, and selected format." : "The check could not be completed (HTTP " + response.status + ").");
  return response.json();
}
function coverage() {
  $("coverage").replaceChildren(); const policy = currentPolicy();
  for (const group of application?.groups || []) {
    const checks = (policy?.checks || []).filter(c => group.checker_ids.includes(c.id));
    const state = !group.checker_ids.length ? "not implemented" : !checks.length ? "disabled" : checks.some(c => !c.available) ? "unavailable" : checks.some(c => c.simulated) ? "simulated" : "enabled";
    const tag = document.createElement("span"); tag.textContent = group.name + " · " + state; tag.title = group.limitations; $("coverage").append(tag);
  }
  $("schema-sample").disabled = !policy?.checks.some(c => c.id === "schema");
  $("business-sample").disabled = !policy?.checks.some(c => c.id === "cross_field");
  $("redaction-sample").disabled = !application?.policies.some(p => p.version === "redaction-v1");
  $("completeness-sample").disabled = !application?.policies.some(p => p.version === "completeness-v1");
  $("secret-sample").disabled = !application?.policies.some(p => p.version === "secrets-v1");
}
$("connect").addEventListener("click", async () => {
  const token = $("api-key").value.trim(); if (!token || busy) return;
  reset(); const attempt = revision; $("connect").disabled = true;
  key = ""; application = null; $("run").disabled = true; $("policy").disabled = true;
  $("coverage").replaceChildren(); $("disconnect").disabled = true;
  try { const data = await api("/v1/applications/current", null, token); if (attempt !== revision) return;
    key = token; application = data; $("api-key").value = ""; $("policy").replaceChildren();
    for (const policy of data.policies) { const option = document.createElement("option"); option.value = policy.version; option.textContent = policy.version + " · " + policy.mode + " mode"; $("policy").append(option); }
    $("connection-status").textContent = "Connected to " + data.application_id + ". " + data.policies.length + " policy available.";
    $("policy").disabled = false; $("disconnect").disabled = false; $("run").disabled = !data.policies.length; coverage();
  } catch (error) { if (attempt === revision) $("connection-status").textContent = error.message || "Connection failed."; }
  finally { $("connect").disabled = false; }
});
$("disconnect").addEventListener("click", () => {
  key = ""; application = null; $("api-key").value = ""; reset(); $("coverage").replaceChildren();
  $("policy").replaceChildren(); $("policy").disabled = true; $("run").disabled = true; $("disconnect").disabled = true;
  $("connection-status").textContent = "Disconnected. Key removed from this tab’s memory.";
});
$("policy").addEventListener("change", () => { reset(); coverage(); });
for (const id of ["input-content", "output-content", "input-format", "output-format", "api-key"]) $(id).addEventListener("input", reset);
for (const button of document.querySelectorAll("[data-sample]")) button.addEventListener("click", () => {
  reset(); $("input-format").value = "text"; $("output-format").value = "text";
  $("input-content").value = "Can you explain your return policy?";
  const examples = {clean: "You can return an unused item within 30 days. Please check the store’s policy for details.", email: "Your contact email is jane@example.com.", card: "The test card number is 4111 1111 1111 1111.", ssn:"The example SSN candidate is 219-09-9999."};
  if (button.dataset.sample === "schema") { $("input-format").value = "json"; $("output-format").value = "json"; $("input-content").value = '{"message":"Can I return this?"}'; $("output-content").value = '{"unexpected_field":"This does not match the output schema."}'; }
  else if (button.dataset.sample === "business") { $("input-format").value = "json"; $("output-format").value = "json"; $("input-content").value = JSON.stringify({start_date:"2026-10-01",end_date:"2026-10-03",purchase_amount:100,refund_amount:25},null,2); $("output-content").value = JSON.stringify({start_date:"2026-10-03",end_date:"2026-10-01",purchase_amount:100,refund_amount:150},null,2); }
  else if (button.dataset.sample === "redaction") { $("policy").value = "redaction-v1"; coverage(); $("output-content").value = "Contact jane@example.com. Example SSN: 219-09-9999. Test card: 4111 1111 1111 1111."; }
  else if (button.dataset.sample === "completeness") { $("policy").value = "completeness-v1"; coverage(); $("input-content").value = "Summary: Customer requests return instructions."; $("output-content").value = "Summary: Return instructions are available.\nNext steps:"; }
  else if (button.dataset.sample === "secret") { $("policy").value = "secrets-v1"; coverage(); $("output-content").value = "Synthetic token pattern for testing only: " + ["ghp", "_", "A".repeat(36)].join(""); }
  else $("output-content").value = examples[button.dataset.sample];
});
function render(stage, result) {
  const box = $(stage + "-results"); box.replaceChildren(); const title = document.createElement("h3"); title.textContent = stage === "input" ? "Input checks" : "Output checks"; box.append(title);
  const summary = document.createElement("p"); summary.textContent = "Recommended: " + result.recommended_action + " · Effective: " + result.effective_action + " · " + result.total_latency_ms.toFixed(1) + " ms"; box.append(summary);
  if (result.redaction && !["not_requested", "not_needed"].includes(result.redaction.status)) {
    const section = document.createElement("section"); section.className = "redaction-box";
    const heading = document.createElement("h3"); heading.textContent = result.redaction.status === "preview" ? "Redacted preview — observe mode" : result.redaction.status === "ready" ? "Redacted content — rechecked" : "Redaction withheld";
    const note = document.createElement("p"); note.textContent = result.redaction.explanation; section.append(heading,note);
    if (result.redaction.validation_results.length) { const rechecks = document.createElement("p"); rechecks.textContent = "Rechecked: " + result.redaction.validation_results.map(c => c.checker_id + " · " + c.status + (c.simulated ? " (simulated)" : "")).join(", "); section.append(rechecks); }
    if (result.redaction.content !== null) { const content = document.createElement("pre"); content.className = "redacted-content"; content.textContent = result.redaction.content; section.append(content); }
    box.append(section);
  }
  function checkerRow(check) { const row = document.createElement("div"); row.className = "checker";
    const heading = document.createElement("div"), name = document.createElement("span"), status = document.createElement("span");
    name.textContent = check.checker_id; status.className = "pill" + (check.simulated ? " warn" : check.status === "passed" ? " good" : check.status === "flagged" ? " bad" : " warn");
    status.textContent = (check.simulated ? "SIMULATED · " : "") + check.status; heading.append(name, status); row.append(heading);
    if (check.error_code) { const error = document.createElement("p"); error.textContent = "Check unavailable: " + check.error_code; row.append(error); }
    for (const finding of check.findings) { const detail = document.createElement("p"); detail.className = "finding"; detail.textContent = finding.explanation; row.append(detail); }
    return row;
  }
  for (const group of result.groups || []) {
    const details = document.createElement("details"); details.className = "check-group"; details.dataset.group = group.group_id;
    details.open = ["findings", "incomplete", "simulated"].includes(group.status);
    const heading = document.createElement("summary"), name = document.createElement("span"), status = document.createElement("span");
    name.textContent = group.name; status.className = "pill" + (group.status === "passed" ? " good" : group.status === "findings" ? " bad" : " warn");
    status.textContent = groupLabels[group.status] || group.status; heading.append(name,status); details.append(heading);
    const limitations = document.createElement("p"); limitations.className = "group-limitations"; limitations.textContent = group.limitations; details.append(limitations);
    for (const check of result.checker_results.filter(c => group.checker_ids.includes(c.checker_id))) details.append(checkerRow(check));
    box.append(details);
  }
}
$("run").addEventListener("click", async () => {
  if (!application || busy) return;
  reset(); const attempt = revision, policy = $("policy").value, app = application.application_id, token = key;
  const proposed = $("output-content").value; busy = true; $("run").disabled = true; $("result-status").textContent = "Checking…";
  try {
    const results = await Promise.all(["input", "output"].map(stage => api("/v1/checks/" + stage, {application_id: app, policy_version: policy, content: $(stage + "-content").value, content_format: $(stage + "-format").value}, token)));
    if (attempt !== revision) return;
    results.forEach((result, i) => render(i ? "output" : "input", result));
    const noChecks = results.some(r => !r.checker_results.some(c => ["passed", "flagged"].includes(c.status)));
    const incomplete = noChecks || results.some(r => r.evaluation_status !== "complete"), findings = results.some(r => r.findings.length > 0), simulated = results.some(r => r.simulated), skipped = results.some(r => r.checker_results.some(c => ["error", "unsupported", "skipped"].includes(c.status)));
    const status = $("result-status"), answerStatus = $("answer-status");
    if (incomplete) { status.textContent = "Incomplete"; status.className = "pill warn"; $("result-summary").textContent = noChecks ? "No checks completed for at least one side. This result is not a pass." : "A required check did not complete. This result is not a pass."; $("answer-note").textContent = "Preview held because check coverage is incomplete."; answerStatus.textContent = "Held"; }
    else if (findings) { status.textContent = "Findings to review"; status.className = "pill bad"; $("result-summary").textContent = "Review the input and output findings below. Observe mode may still report an effective action of allow."; $("answer-note").textContent = "Preview held for review because checks reported findings."; answerStatus.textContent = "Held for review"; }
    else { status.textContent = simulated ? "Simulated coverage" : "No findings"; status.className = simulated || skipped ? "pill warn" : "pill good";
      $("result-summary").textContent = simulated ? "No findings, but some checks are simulated. This is not a completed safety or bias assessment." : "No findings from the selected completed checks. This is not a guarantee of correctness or safety.";
      $("answer-note").textContent = simulated || skipped ? "Preview only. Some checks were simulated or skipped; see coverage above." : "The selected checks completed without findings. Only their defined coverage was assessed.";
      answerStatus.textContent = simulated || skipped ? "Unverified preview" : "Selected checks passed"; answerStatus.className = status.className;
      $("answer").textContent = proposed; $("answer").hidden = false;
    }
  } catch (error) { if (attempt === revision) { $("run-error").textContent = error.message || "Checks failed. No pass was recorded."; $("result-status").textContent = "Check failed"; $("answer-note").textContent = "Preview held because checks failed."; } }
  finally { busy = false; $("run").disabled = !application?.policies.length; }
});
