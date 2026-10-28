"use strict";
const $ = id => document.getElementById(id);
let key = "", application = null, revision = 0, busy = false, inputResult = null;
const canProceed = r => r && r.evaluation_status === "complete" && !r.simulated && !r.findings.length && r.recommended_action === "allow" && r.effective_action === "allow" && r.checker_results.length > 0 && r.checker_results.every(c => c.status === "passed" && !c.simulated);
function buttons() {
  $("run").disabled = $("run-input").disabled = busy || !application?.policies.length;
  $("run-output").disabled = busy || !application || !canProceed(inputResult);
  updateSampleButtons();
}
function waiting(stage) { const box = $(stage + "-results"); box.replaceChildren();
  const title = document.createElement("h3"); title.textContent = stage === "input" ? "Input checks" : "Output checks";
  const note = document.createElement("p"); note.textContent = stage === "input" ? "Waiting for a check." : "Waiting for the input test or proposed answer check."; note.className = "muted"; box.append(title, note);
}
const currentPolicy = () => application?.policies.find(p => p.version === $("policy").value);
const groupLabels = {passed:"Passed", findings:"Findings", incomplete:"Incomplete", simulated:"Simulated", partial:"Partial coverage", disabled:"Disabled", not_applicable:"Not applicable", not_implemented:"Not implemented"};
function reset(keepInput = false) {
  revision++;
  if (!keepInput) inputResult = null;
  $("input-gate").textContent = canProceed(inputResult) ? "Input checks passed. Paste your AI’s answer, then run output checks." : "Complete the input test first.";
  buttons();
  $("answer").hidden = true; $("answer").textContent = "";
  $("answer-status").textContent = "Not checked"; $("answer-status").className = "pill";
  $("answer-note").textContent = "An answer appears here when the selected checks complete without findings.";
  $("result-status").textContent = "Ready to test"; $("result-status").className = "pill";
  $("result-summary").textContent = "Run the checks to inspect this conversation.";
  if (!keepInput) waiting("input");
  waiting("output");
  $("run-error").textContent = "";
}
async function api(path, body, token = key) {
  const response = await fetch(path, {method: body ? "POST" : "GET", cache: "no-store",
    headers: {"Authorization": "Bearer " + token, ...(body ? {"Content-Type": "application/json"} : {})},
    ...(body ? {body: JSON.stringify(body)} : {})});
  if (!response.ok && [429, 503].includes(response.status) && response.headers.has("Retry-After")) {
    const seconds = Number(response.headers.get("Retry-After"));
    throw new Error((response.status === 429 ? "Application limit reached." : "Service capacity reached.") + (Number.isSafeInteger(seconds) && seconds > 0 ? " Retry in " + seconds + (seconds === 1 ? " second." : " seconds.") : " Try again shortly."));
  }
  if (!response.ok) throw new Error(response.status === 401 ? "The API key was not accepted." : response.status === 422 ? "Check the required fields, content length, and selected format." : "The check could not be completed (HTTP " + response.status + ").");
  return response.json();
}
function coverage() {
  $("coverage").replaceChildren(); const policy = currentPolicy();
  const external = policy?.checks.some(c => c.external);
  $("execution-mode").textContent = external ? (policy.external_checks === "after_local_pass" ? "External provider gated" : "External checks disabled") : "Local checks";
  $("data-routing").textContent = external ? (policy.external_checks === "after_local_pass" ? "Input is sent to the configured Azure service and may incur charges only after all configured local checks pass without simulation. This does not guarantee that all sensitive data was detected. Provider cost is not estimated. Request context is not sent by this connector." : "This policy disables external checks. No input is sent to the provider.") : "Selected checks run locally. No provider content transfer is configured.";
  for (const group of application?.groups || []) {
    const checks = (policy?.checks || []).filter(c => group.checker_ids.includes(c.id));
    const state = !group.checker_ids.length ? "not implemented" : !checks.length ? "disabled" : checks.some(c => !c.available) ? "unavailable" : checks.some(c => c.simulated) ? "simulated" : "enabled";
    const tag = document.createElement("span"); tag.textContent = group.name + " · " + state; tag.title = group.limitations; $("coverage").append(tag);
  }
  $("profile-status").textContent = policy ? (policy.version === "conversation-v1" ? "Combined conversation checks are active on both stages." : "Focused example profile: " + policy.version + ". Only its configured checks run.") : "Connect to load the combined conversation profile.";
  updateSampleButtons();
}
$("connect").addEventListener("click", async () => {
  const token = $("api-key").value.trim(); if (!token || busy) return;
  reset(); const attempt = revision; $("connect").disabled = true;
  key = ""; application = null; buttons(); $("run").disabled = true; $("policy").disabled = true;
  $("coverage").replaceChildren(); $("execution-mode").textContent = "Local checks"; $("data-routing").textContent = ""; $("resource-limits").textContent = ""; $("disconnect").disabled = true;
  try { const data = await api("/v1/applications/current", null, token); if (attempt !== revision) return;
    key = token; application = data; $("api-key").value = ""; $("policy").replaceChildren();
    for (const policy of data.policies) { const option = document.createElement("option"); option.value = policy.version; option.textContent = (policy.version === "conversation-v1" ? "Conversation — combined general text checks" : policy.version) + " · " + policy.mode + " mode"; $("policy").append(option); }
    if (data.policies.some(p => p.version === "conversation-v1")) $("policy").value = "conversation-v1";
    $("connection-status").textContent = "Connected to " + data.application_id + ". " + data.policies.length + " policy available.";
    const limits = data.resource_limits;
    $("resource-limits").textContent = limits ? "Per server process: " + limits.checks_per_minute + " checks/minute per application · burst " + limits.burst + " · " + limits.application_concurrency + " concurrent checks per application. Each stage uses one API check; a full sequence uses two checks." : "";
    $("policy").disabled = false; $("disconnect").disabled = false; buttons(); coverage();
  } catch (error) { if (attempt === revision) $("connection-status").textContent = error.message || "Connection failed."; }
  finally { $("connect").disabled = false; }
});
$("disconnect").addEventListener("click", () => {
  key = ""; application = null; $("api-key").value = ""; $("resource-limits").textContent = ""; reset(); $("coverage").replaceChildren(); $("profile-status").textContent = "Connect to load the combined conversation profile.";
  $("execution-mode").textContent = "Local checks"; $("data-routing").textContent = "";
  $("policy").replaceChildren(); $("policy").disabled = true; $("run").disabled = true; $("disconnect").disabled = true;
  $("connection-status").textContent = "Disconnected. Key removed from this tab’s memory.";
});
$("policy").addEventListener("change", () => { reset(); coverage(); });
for (const id of ["input-content", "input-format", "api-key"]) $(id).addEventListener("input", () => reset());
for (const id of ["output-content", "output-format"]) $(id).addEventListener("input", () => reset(true));
// Each example owns exactly one editor. Profiles are never switched implicitly.
const syntheticToken = () => ["ghp", "_", "A".repeat(36)].join("");
const businessData = (invalid = false, stage = "input") => JSON.stringify({start_date: invalid ? "2026-10-03" : "2026-10-01", end_date: invalid ? "2026-10-01" : "2026-10-03", purchase_amount:100, refund_amount:invalid ? (stage === "output" ? 150 : 125) : (stage === "output" ? 20 : 25)}, null, 2);
const mixedPrivacy = "Contact jane@example.com. Example SSN: 219-09-9999. Test card: 4111 1111 1111 1111.\nPhone: 202-555-0147\nDOB: 1980-02-29\nMRN: A1234\nmember_id: M000123\nbank_account_number: 76543210";
function structuredData(stage) {
  return {request_id:"123e4567-e89b-42d3-a456-426614174000",
    [stage === "input" ? "message" : "answer"]:stage === "input" ? "Can you review this structured request?" : "The example request has been reviewed.",
    [stage === "input" ? "priority" : "status"]:stage === "input" ? "normal" : "ok",
    contact:{email:"builder@example.com"}, items:[{sku:"ABC-123",quantity:2}],
    metadata:{created_at:"2026-10-28T12:30:00Z",due_date:"2026-10-30",notify_at:"12:30:00Z",ip_v4:"192.0.2.1",ip_v6:"2001:db8::1"}, sequence:10};
}
function structureExample(stage,kind) {
  const value=structuredData(stage), field=stage === "input" ? "message" : "answer";
  if (kind === "missing-field") delete value[field];
  if (kind === "wrong-type") value[field]=42;
  if (kind === "extra-field") value.unexpected="Synthetic extra field";
  if (kind === "invalid-date") value.metadata.due_date="2026-02-30";
  if (kind === "invalid-email") value.contact.email="not-an-email";
  if (kind === "too-many-items") value.items=["ABC-123","DEF-456","GHI-789","JKL-012"].map(sku=>({sku,quantity:2}));
  return {format:"json",text:kind === "malformed-json" ? '{"'+field+'":' : JSON.stringify(value,null,2)};
}
function numericExample(stage,kind) {
  let text;
  if (kind === "decimal-over") text = stage === "input" ? '{"left":1.0000000000000001,"right":1.0}' : '{"left":1.0000000000000002,"right":1.0}';
  else if (kind === "decimal-within") text = stage === "input" ? '{"left":1.0,"right":1.0000000000000001}' : '{"left":1.0000000000000001,"right":1.0000000000000002}';
  else {
    const value={left:stage === "input" ? 20 : 15,right:100};
    if (kind === "numeric-exceeds") value.left=stage === "input" ? 125 : 150;
    if (kind === "comparison-missing") delete value.right;
    if (kind === "number-invalid") value.left=String(value.left);
    text=JSON.stringify(value,null,2);
  }
  // Keep precision examples as literal text: JSON.parse/stringify in the browser
  // would round their values before the API ever received them.
  return {format:"json",text};
}
function refundExample(stage,kind) {
  const value=JSON.parse(businessData(false,stage));
  if (kind === "refund-exceeds") value.refund_amount=stage === "input" ? 125 : 150;
  else {value.start_date="2026-10-03";value.end_date="2026-10-01";}
  return {format:"json",text:JSON.stringify(value,null,2)};
}
const samples = [
  {id:"clean", label:"Clean question", outputLabel:"Safe reply", content:stage => {
    if (currentPolicy()?.version === "business-number-le-v1") return numericExample(stage,"clean");
    if (currentPolicy()?.version === "structure-v1") return structureExample(stage,"clean");
    if (currentPolicy()?.version === "business-v1") return {format:"json", text:businessData(false,stage)};
    if (currentPolicy()?.version === "completeness-v1") return {text:stage === "input" ? "Summary: Customer requests return instructions." : "Summary: Return instructions are available.\nNext steps: Contact the store for instructions."};
    return {text:stage === "input" ? "Can you explain your return policy?" : "You can return an unused item within 30 days. Please check the store’s policy for details."};
  }},
  {id:"email", label:"Email address", checker:"pii", input:"Can you help me update my contact email? jane@example.com", output:"Your contact email is jane@example.com."},
  {id:"card", label:"Card number", checker:"pii", input:"Can you check this synthetic payment example? Card 4111 1111 1111 1111", output:"The test card number is 4111 1111 1111 1111."},
  {id:"ssn", label:"SSN candidate", checker:"pii", input:"Please review this synthetic support form.\nSSN: 219-09-9999", output:"The example SSN candidate is 219-09-9999."},
  {id:"phone", label:"Phone number", profiles:["conversation-v1","pii-extended-v1","redaction-v1"], input:"Can you update my contact details?\nPhone: 202-555-0147", output:"Here are the requested contact details.\nPhone: 202-555-0186"},
  {id:"birth-date", label:"Birth date", profiles:["conversation-v1","pii-extended-v1","redaction-v1"], input:"Please review this synthetic intake form.\nDOB: 1980-02-29", output:"The example profile contains the following birth date.\nDOB: 1990-12-31"},
  {id:"medical-id", label:"Medical record ID", profiles:["conversation-v1","pii-extended-v1","redaction-v1"], input:"Can you help with this synthetic record?\nMRN: A1234", output:"Here is the medical record identifier from the example.\nMRN: B5678"},
  {id:"insurance-id", label:"Insurance member ID", profiles:["conversation-v1","pii-extended-v1","redaction-v1"], input:"Please review my synthetic insurance form.\ninsurance_member_id: M000123", output:"The example insurance details include this member ID.\ninsurance_member_id: M000456"},
  {id:"bank-account", label:"Bank account", profiles:["conversation-v1","pii-extended-v1","redaction-v1"], input:"Can you review this synthetic payment form?\nbank_account_number: 76543210", output:"The example payment details include this account.\nbank_account_number: 87654321"},
  {id:"redaction", label:"Redaction preview", profiles:["redaction-v1"], input:"Please review these synthetic form values.\n"+mixedPrivacy, output:"The draft answer includes these synthetic identifiers.\n"+mixedPrivacy},
  {id:"hidden", label:"Hidden characters", checker:"text_security", input:"Please explain this hidden\u200bcharacter.", output:"The requested answer contains a hidden\u200bcharacter."},
  {id:"encoded", label:"Encoded text", checker:"text_security", input:"Please review this labeled text: base64: SGVsbG8=", output:"The answer includes an encoded note: base64: V29ybGQ="},
  {id:"completeness", label:"Missing information", profiles:["completeness-v1"], input:"Details: Customer needs help, but the required summary is missing.", output:"Summary: Return instructions are available.\nNext steps:"},
  {id:"password", label:"Quoted password", profiles:["conversation-v1","secrets-extended-v1"], input:'Can you review this synthetic configuration?\npassword = "synthetic-input!42"', output:'The draft reply accidentally includes a configuration literal.\npassword = "synthetic-example!42"'},
  {id:"confidential", label:"Confidential term", profiles:["conversation-v1","secrets-extended-v1"], input:"Can you review internal notes about PROJECT LANTERN?", output:"Internal draft about PROJECT LANTERN."},
  {id:"encoded-secret", label:"Encoded credential", profiles:["conversation-v1","secrets-extended-v1"], content:stage => ({text:(stage === "input" ? "Please inspect this encoded configuration: base64: " : "The draft answer includes an encoded credential: base64: ")+btoa(syntheticToken())})},
  {id:"secret", label:"Credential pattern", checker:"secrets", content:stage => ({text:(stage === "input" ? "Can you review this synthetic token? " : "Synthetic token pattern for testing only: ")+syntheticToken()})},
  {id:"schema", label:"Schema error", profiles:["business-v1","structure-v1"], content:stage => ({format:"json", text:stage === "input" ? '{"unexpected_input":"Missing required request fields."}' : '{"unexpected_field":"This does not match the output schema."}'})},
  {id:"business", label:"Business rule error", profiles:["business-v1"], content:stage => ({format:"json", text:businessData(true,stage)})},
  ...[["missing-field","Missing field"],["wrong-type","Wrong data type"],["extra-field","Unexpected field"],["invalid-date","Invalid date"],["invalid-email","Invalid email"],["too-many-items","Too many items"],["malformed-json","Malformed JSON"]].map(([id,label])=>({id,label,profiles:["structure-v1"],content:stage=>structureExample(stage,id)})),
  ...[["numeric-exceeds","Exceeds numeric limit"],["comparison-missing","Missing comparison value"],["number-invalid","Invalid number"],["decimal-over","Decimal over limit"],["decimal-within","Decimal within limit"]].map(([id,label])=>({id,label,profiles:["business-number-le-v1"],content:stage=>numericExample(stage,id)})),
  ...[["refund-exceeds","Refund too large"],["date-reversed","Reversed dates"]].map(([id,label])=>({id,label,profiles:["business-v1"],content:stage=>refundExample(stage,id)})),
  {id:"section-duplicate",label:"Duplicate section",profiles:["completeness-v1"],input:"Summary: First request.\nSUMMARY: Second request.",output:"Summary: Instructions available.\nNext steps: Contact the store.\nNEXT STEPS: Open a ticket."},
  {id:"section-empty",label:"Empty section",profiles:["completeness-v1"],input:"Summary:   ",output:"Summary: Instructions available.\nNext steps:   "},
  {id:"section-multiline",label:"Multiline sections",profiles:["completeness-v1"],input:"Summary:\nCustomer requests return instructions.",output:"Summary:\nInstructions are available.\nNext steps:\nContact the store for details."},
  {id:"section-short",label:"Short next steps",stages:["output"],profiles:["completeness-v1"],output:"Summary: Instructions available.\nNext steps: Hi"},
  {id:"output-confidential", label:"Output-only confidential term", stages:["output"], profiles:["conversation-v1","secrets-extended-v1"], output:"The draft reply reveals our unreleased pricing plan."},
  {id:"multiple", label:"Multiple issues", stages:["output"], profiles:["conversation-v1"], content:() => ({text:'Your contact email is jane@example.com.\npassword = "synthetic-example!42"\nThe reply also includes hidden\u200btext.'})},
];
function sampleAvailable(sample, stage) {
  const policy = currentPolicy();
  if (!policy) return false;
  if (sample.profiles && !sample.profiles.includes(policy.version)) return false;
  return !sample.checker || policy.checks.some(c => c.id === sample.checker && c.stages.includes(stage));
}
function updateSampleButtons() {
  for (const stage of ["input","output"]) for (const sample of samples) {
    const button = $(stage+"-"+sample.id+"-sample"); if (!button) continue;
    const available=sampleAvailable(sample,stage);
    button.disabled = busy || !available;
    button.hidden = !available;
    button.title = sample.profiles ? "Requires profile: " + sample.profiles.join(", ") + ". Changes only " + stage + " data." : "Changes only " + stage + " data.";
  }
}
for (const stage of ["input","output"]) {
  const title = document.createElement("span"); title.textContent = "Try an " + stage + " example"; $(stage+"-samples").append(title);
  for (const sample of samples.filter(s => !s.stages || s.stages.includes(stage))) {
    const button = document.createElement("button"); button.className = "chip"; button.id = stage+"-"+sample.id+"-sample";
    button.dataset.sample = sample.id; button.dataset.stage = stage; button.textContent = stage === "output" ? (sample.outputLabel || sample.label) : sample.label; button.disabled = true; button.hidden = true;
    button.addEventListener("click", () => {
      if (busy || !sampleAvailable(sample,stage)) return;
      const example = sample.content ? sample.content(stage) : {text:sample[stage]};
      reset(stage === "output");
      $(stage+"-format").value = example.format || "text";
      $(stage+"-content").value = example.text;
    });
    $(stage+"-samples").append(button);
  }
}
function render(stage, result) {
  const box = $(stage + "-results"); box.replaceChildren(); const title = document.createElement("h3"); title.textContent = stage === "input" ? "Input checks" : "Output checks"; box.append(title);
  const summary = document.createElement("p"); summary.textContent = "Recommended: " + result.recommended_action + " · Effective: " + result.effective_action + " · " + result.total_latency_ms.toFixed(1) + " ms" + (result.cost_basis === "provider_unpriced" ? " · Provider cost not estimated" : ""); box.append(summary);
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
    name.textContent = check.checker_id + (check.external ? " · external provider" : ""); status.className = "pill" + (check.simulated ? " warn" : check.status === "passed" ? " good" : check.status === "flagged" ? " bad" : " warn");
    status.textContent = (check.simulated ? "SIMULATED · " : "") + check.status; heading.append(name, status); row.append(heading);
    if (check.error_code) { const error = document.createElement("p"); const gateMessages = {external_local_gate:"External check withheld: configured local checks must all pass without simulation.", external_policy_disabled:"External check withheld: this policy disables provider calls."}; error.textContent = gateMessages[check.error_code] || "Check unavailable: " + check.error_code; row.append(error); }
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
async function execute(target) {
  if (!application || busy || (target === "output" && !canProceed(inputResult))) return;
  reset(target === "output"); const attempt = revision, policy = $("policy").value, app = application.application_id, token = key;
  const proposed = $("output-content").value; busy = true; buttons(); $("result-status").textContent = "Checking…";
  try {
    const assess = stage => api("/v1/checks/" + stage, {application_id: app, policy_version: policy, content: $(stage + "-content").value, content_format: $(stage + "-format").value}, token);
    if (target !== "output") {
      const checked = await assess("input");
      if (attempt !== revision) return;
      inputResult = checked; render("input", checked);
      $("input-gate").textContent = canProceed(checked) ? "Input checks passed. Paste your AI’s answer, then run output checks." : "Input held. Review findings or incomplete checks and correct the message before continuing.";
    }
    const results = [inputResult];
    if (target !== "input" && canProceed(inputResult)) {
      const checked = await assess("output");
      if (attempt !== revision) return;
      results.push(checked); render("output", checked);
    }
    const noChecks = results.some(r => !r.checker_results.some(c => ["passed", "flagged"].includes(c.status)));
    const incomplete = noChecks || results.some(r => r.evaluation_status !== "complete"), findings = results.some(r => r.findings.length > 0), simulated = results.some(r => r.simulated), skipped = results.some(r => r.checker_results.some(c => ["error", "unsupported", "skipped"].includes(c.status)));
    const status = $("result-status"), answerStatus = $("answer-status");
    if (incomplete) { status.textContent = "Incomplete"; status.className = "pill warn"; $("result-summary").textContent = noChecks ? "No checks completed for at least one side. This result is not a pass." : "A required check did not complete. This result is not a pass."; $("answer-note").textContent = "Preview held because check coverage is incomplete."; answerStatus.textContent = "Held"; }
    else if (findings) { status.textContent = "Findings to review"; status.className = "pill bad"; $("result-summary").textContent = "Review the input and output findings below. Observe mode may still report an effective action of allow."; $("answer-note").textContent = "Preview held for review because checks reported findings."; answerStatus.textContent = "Held for review"; }
    else if (results.length === 1 && canProceed(inputResult)) {
      status.textContent = "Input checks passed"; status.className = "pill good";
      $("result-summary").textContent = "Input test complete. Continue to the proposed AI answer and output test.";
      $("answer-note").textContent = "Waiting for output checks. No answer has been released.";
    }
    else if (results.length === 1 && !canProceed(inputResult) && !simulated) {
      status.textContent = "Input held"; status.className = "pill warn";
      $("result-summary").textContent = "Input did not pass every configured check. Review its results before continuing.";
      $("answer-note").textContent = "Preview held at the input test.";
    }
    else { status.textContent = simulated ? "Simulated coverage" : "No findings"; status.className = simulated || skipped ? "pill warn" : "pill good";
      $("result-summary").textContent = simulated ? "No findings, but some checks are simulated. This is not a completed safety or bias assessment." : "No findings from the selected completed checks. This is not a guarantee of correctness or safety.";
      $("answer-note").textContent = simulated || skipped ? "Preview only. Some checks were simulated or skipped; see coverage above." : "The selected checks completed without findings. Only their defined coverage was assessed.";
      answerStatus.textContent = simulated || skipped ? "Unverified preview" : "Selected checks passed"; answerStatus.className = status.className;
      if (results.length === 2 && canProceed(results[1])) { $("answer").textContent = proposed; $("answer").hidden = false; }
      else { $("answer-note").textContent = "Preview held because every configured check must pass without simulation or skipped checks."; }
    }
  } catch (error) { if (attempt === revision) { $("run-error").textContent = error.message || "Checks failed. No pass was recorded."; $("result-status").textContent = "Check failed"; $("answer-note").textContent = "Preview held because checks failed."; } }
  finally { busy = false; buttons(); }
}
$("run").addEventListener("click", () => execute("both"));
$("run-input").addEventListener("click", () => execute("input"));
$("run-output").addEventListener("click", () => execute("output"));
