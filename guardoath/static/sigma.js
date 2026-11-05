"use strict";
// Optional preview of rules saved in the authoritative tenant record. The server
// runs only its own pinned synthetic fixtures, not arbitrary customer events.
const $ = id => document.getElementById(id);
let key = "";
let rules = [];
async function headers(payload) {
  const result = {"X-GuardOath-Key": key};
  if (payload !== undefined) {
    const buffer = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(payload));
    result["x-amz-content-sha256"] = [...new Uint8Array(buffer)].map(x => x.toString(16).padStart(2, "0")).join("");
    result["Content-Type"] = "application/json";
  }
  return result;
}
function show(message) { $("sigma-status").textContent = message; }
function line(label, value) {
  const p = document.createElement("p");
  const b = document.createElement("strong"); b.textContent = label + ": ";
  p.append(b, document.createTextNode(value || "Not specified"));
  return p;
}
function current() { return rules.find(x => x.rule_id === $("sigma-rule").value); }
function details() {
  const area = $("sigma-details"); area.replaceChildren();
  const r = current(); if (!r) return;
  area.append(line("Use case", r.use_case), line("Source", r.product + " / " + r.category),
              line("Original author", r.author), line("License", r.license),
              line("False positives", (r.false_positives || []).join("; ") || "Not specified"));
  const link = document.createElement("a"); link.href = r.source_url;
  link.target = "_blank"; link.rel = "noopener noreferrer";
  link.textContent = "View pinned source"; area.append(link);
}
function filter() {
  const term = $("sigma-filter").value.trim().toLowerCase();
  const currentId = $("sigma-rule").value, select = $("sigma-rule"); select.replaceChildren();
  for (const rule of rules.filter(r => [r.rule_id, r.title, r.use_case, r.category]
    .some(value => (value || "").toLowerCase().includes(term)))) {
    const opt = document.createElement("option"); opt.value = rule.rule_id; opt.textContent = rule.title;
    select.append(opt);
  }
  if ([...select.options].some(x => x.value === currentId)) select.value = currentId;
  details();
}
$("sigma-load").addEventListener("click", async () => {
  const next = $("sigma-key").value.trim();
  if (!next) { show("Enter a tenant or demo API key."); return; }
  $("sigma-load").disabled = true; key = next;
  $("sigma-key").value = "";
  try {
    const response = await fetch("/v1/sigma/catalog", {headers: await headers(), cache: "no-store"});
    if (!response.ok) throw new Error("Could not authenticate or load pinned rules.");
    const data = await response.json();
    rules = data.rules || [];
    $("sigma-count").textContent = "(" + rules.length + ")";
    $("sigma-workspace").hidden = false;
    filter();
    show("Catalog loaded. To run a sample, enable that rule in this tenant's dashboard settings and save.");
  } catch (err) { key = ""; $("sigma-workspace").hidden = true; show(err.message); }
  finally { $("sigma-load").disabled = false; }
});
$("sigma-rule").addEventListener("change", details);
$("sigma-filter").addEventListener("input", filter);
async function run(sample) {
  const rule = current(); if (!rule) return;
  $("sigma-result").textContent = "Running pinned synthetic " + sample + " event...";
  try {
    const text = JSON.stringify({rule_id: rule.rule_id, sample});
    const response = await fetch("/v1/sigma/demo", {method: "POST", cache: "no-store",
      headers: await headers(text), body: text});
    if (!response.ok) throw new Error(response.status === 403
      ? "This rule is Off for the tenant, or the credential lacks permission. Enable and save it on the dashboard."
      : "Synthetic evaluation unavailable (HTTP " + response.status + ").");
    const data = await response.json();
    $("sigma-result").textContent = data.findings.length + " matching enabled rule(s)" +
      (data.sample_rule_matched ? " | Selected rule matched" : " | Selected rule did not match") +
      " | Recommended " + data.recommended_action + " | Effective " + data.effective_action +
      " | Synthetic fixture only. No customer event was inspected.";
  } catch (err) { $("sigma-result").textContent = err.message; }
}
$("sigma-positive").addEventListener("click", () => run("positive"));
$("sigma-lookalike").addEventListener("click", () => run("lookalike"));
