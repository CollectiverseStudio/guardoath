"use strict";
const {test, expect} = require("@playwright/test");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");

test.skip(process.env.GUARDOATH_INVOCATION_BROWSER !== "1", "Invocation acceptance configuration required.");

test("invocation source guards require tenant-bound capabilities and completed nonsimulated evidence before release", () => {
  const source = fs.readFileSync(path.resolve(__dirname, "../../guardoath/static/dashboard.js"), "utf8");
  const core = source.slice(source.indexOf("const invocationIdPattern ="),
    source.indexOf('$("invocation-reload").addEventListener'));
  const sandbox = {application: {application_id: "tenant-alpha"}};
  vm.createContext(sandbox); vm.runInContext(core, sandbox);
  const catalog = {application_id: "tenant-alpha", available: true, reason_code: "ready", max_input_bytes: 16384,
    capabilities: {buffered_text: true, response_retention: false, agents: false, tools: false, streaming: false, files: false},
    connections: [{id: "approved", version: "v1", label: "Approved connection", provider: "openai", region: null,
      monthly_call_limit: 10, monthly_output_token_limit: 1000,
      models: [{id: "allowed", label: "Allowed model", provider_model_id: "pinned-model-v1", max_output_tokens: 100}]}]};
  expect(sandbox.validInvocationCatalog(catalog)).toBe(true);
  for (const alter of [value => {value.application_id = "tenant-beta";},
    value => {value.capabilities.tools = true;}, value => {value.capabilities.buffered_text = false;},
    value => {value.connections[0].id = 123;}, value => {value.connections[0].models.push(value.connections[0].models[0]);},
    value => {value.available = false;}]) {
    const value = structuredClone(catalog); alter(value);
    expect(sandbox.validInvocationCatalog(value)).toBe(false);
  }
  const evidence = {check_id: "check-1", decision: "allow", evaluation_status: "complete", simulated: false,
    checker_ids: ["text_security"], finding_ids: []};
  const receipt = {schema_version: 1, application_id: "tenant-alpha", interaction_id: "a".repeat(32), request_id: "request-1",
    created_at: "2026-11-22T00:00:00Z", updated_at: "2026-11-22T00:00:00Z", status: "completed",
    response_retained: false, policy_revision: 1, policy_digest: "b".repeat(64), event_digest: "c".repeat(64),
    catalog_version: "guarded-models-1", sequence: 5, dispatch_state: "response_observed",
    target: {connection_id: "approved", connection_version: "v1", provider: "openai", region: null,
      model_id: "allowed", provider_model_id: "pinned-model-v1"}, input_check: evidence, output_check: {...evidence}};
  const expected = {request_id: "request-1", target: {connection_id: "approved", model_id: "allowed"}};
  expect(sandbox.validInvocationReceipt(receipt, expected)).toBe(true);
  expect(sandbox.releasableInvocation(receipt)).toBe(true);
  for (const alter of [value => {value.application_id = "tenant-beta";}, value => {value.request_id = "request-other";},
    value => {value.target.model_id = "unapproved";}, value => {value.input_check.simulated = true;},
    value => {value.input_check.evaluation_status = "incomplete";}, value => {value.output_check.decision = "block";},
    value => {value.dispatch_state = "intent_recorded";}, value => {value.response_retained = true;}]) {
    const value = structuredClone(receipt); alter(value);
    expect(sandbox.validInvocationReceipt(value, expected)).toBe(false);
  }
  const blocked = {...receipt, status: "output_blocked", output_check: {...evidence, decision: "block"}};
  expect(sandbox.validInvocationReceipt(blocked, expected)).toBe(true);
  expect(sandbox.releasableInvocation(blocked)).toBe(false);
});

test("invocation discovery fetches only the advertised literal path and preserves current-session 401 expiry", async () => {
  const source = fs.readFileSync(path.resolve(__dirname, "../../guardoath/static/dashboard.js"), "utf8");
  const core = source.slice(source.indexOf("const invocationIdPattern ="),
    source.indexOf('$("invocation-reload").addEventListener'));
  const nodes = new Map(), requests = [];
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {value: "", textContent: "", replaceChildren() {}});
    return nodes.get(id);
  };
  let expired = 0;
  const sandbox = {application: {application_id: "tenant-alpha"}, invocation: {catalogSequence: 0},
    key: "session synthetic", SESSION: "session ", $: node, Option: function() {},
    currentInspectionOwner: () => () => true, requestHeaders: async () => ({}),
    fetch: async (url, options) => {requests.push({url, options}); return {status: 401, ok: false};},
    expireCustomerSession: () => {expired++;}};
  vm.createContext(sandbox); vm.runInContext(core, sandbox);
  sandbox.synchronizeInvocationOwner = () => true;
  sandbox.refreshInvocationControls = () => {};
  sandbox.populateInvocationModels = () => {};
  await sandbox.loadInvocationCatalog();
  sandbox.application.invocation_catalog = "https://untrusted.invalid/catalog";
  await sandbox.loadInvocationCatalog();
  expect(requests).toEqual([]);
  expect(node("invocation-configuration").textContent).toContain("does not advertise model invocation");
  sandbox.application.invocation_catalog = "/v1/connections";
  await sandbox.loadInvocationCatalog();
  expect(requests.map(item => item.url)).toEqual(["/v1/connections"]);
  expect(requests[0].options.redirect).toBe("error");
  expect(expired).toBe(1);
});
