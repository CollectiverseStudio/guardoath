"use strict";

const {test, expect} = require("@playwright/test");
const {createHash, randomUUID} = require("node:crypto");

// This suite uses real ASGI auth, tenant policy, Engine and SQLite receipt APIs.
// No browser route mocks replace a connection, invocation, policy or receipt.
test.skip(process.env.GUARDOATH_INVOCATION_BROWSER !== "1", "Use playwright.invocations.config.js for the authenticated fixture.");
const ALPHA = "synthetic-invocation-owner-alpha", BETA = "synthetic-invocation-owner-beta";
const APP = "usr-invocation-alpha";
const headers = (token = ALPHA) => ({"X-GuardOath-Session": token});

async function post(request, path, body, token = ALPHA) {
  const text = JSON.stringify(body);
  return request.post(path, {headers: {...headers(token), "Content-Type": "application/json",
    "x-amz-content-sha256": createHash("sha256").update(text).digest("hex")}, data: text});
}
async function observed(request, token = ALPHA) {
  const response = await request.get("/__test/observations", {headers: headers(token)});
  expect(response.status()).toBe(200); return response.json();
}
async function connect(page, token = ALPHA, url = "/dashboard", available = true) {
  await page.addInitScript(value => {
    if (!sessionStorage.getItem("guardoath-session")) sessionStorage.setItem("guardoath-session", value);
  }, token);
  await page.goto(url);
  await expect(page.locator("#dashboard-app")).toBeVisible();
  await expect(page.locator("#context-auth")).toHaveText("Customer session");
  await expect(page.locator("#invocation-availability")).toHaveText(available ? "Ready to invoke" : "Unavailable");
}
async function invoke(page, message) {
  await page.locator("#invocation-message").fill(message);
  const responsePromise = page.waitForResponse(response => response.url().endsWith("/v1/interactions")
    && response.request().method() === "POST");
  await page.locator("#invocation-run").click();
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  const value = await response.json();
  await expect(page.locator("#invocation-status")).not.toHaveText("Processing");
  return value;
}
function body(overrides = {}) {
  return {application_id: APP, request_id: randomUUID(),
    target: {kind: "model", connection_id: "connection-alpha", model_id: "fixture-model"},
    message: "A safe browser acceptance request.", max_output_tokens: 64, ...overrides};
}

test.beforeEach(async ({request}) => {
  for (const token of [ALPHA, BETA]) {
    const response = await request.get("/v1/applications/current", {headers: headers(token)});
    expect(response.status()).toBe(200);
    const current = await response.json();
    const saved = await post(request, "/v1/tenants/current/checks", {revision: current.tenant.revision,
      changes: {input_check_text_security: "block", output_check_text_security: "block",
        input_check_human_safety: null, output_check_human_safety: null,
        input_check_harmful_content: null, output_check_harmful_content: null, mode: "enforce"}}, token);
    expect(saved.status()).toBe(200);
  }
});

test("approved targets come from the authenticated catalog and unavailable tenants have no simulated choices", async ({page, request}) => {
  await connect(page);
  await expect(page.locator("#invocation-connection option")).toHaveText(["Approved Alpha connection"]);
  await expect(page.locator("#invocation-model option")).toHaveText(["Approved fixture model", "Approved small model"]);
  await expect(page.locator("#invocation-connection-detail")).toContainText("AWS Bedrock · Region us-east-1");
  await expect(page.locator("#invocation-connection-detail")).toContainText("fixture.pinned-model-v1");
  await expect(page.locator("#invocation-capabilities")).toContainText("Agents, tools, streaming and files are unavailable");
  const catalog = await request.get("/v1/connections", {headers: headers()});
  const value = await catalog.json();
  expect(value.connections.map(item => item.id)).toEqual(["connection-alpha"]);
  expect(JSON.stringify(value)).not.toContain(ALPHA);
  expect(JSON.stringify(value)).not.toMatch(/secret_env|role_arn|access_key|session_token/);
  await page.evaluate(() => sessionStorage.setItem("guardoath-session", "synthetic-invocation-owner-empty"));
  await page.reload();
  await expect(page.locator("#invocation-availability")).toHaveText("Unavailable");
  await expect(page.locator("#invocation-configuration")).toContainText("No provider connections are approved");
  await expect(page.locator("#invocation-connection")).toBeDisabled();
  await expect(page.locator("#invocation-model option")).toHaveText(["Select a connection"]);
  await expect(page.locator("#invocation-message")).toBeDisabled();
  await expect(page.locator("#invocation-run")).toBeDisabled();
  await expect(page.locator("#run-input")).toBeEnabled();
});

test("an unconfigured deployment explains the actual missing runtime while keeping manual validation available", async ({page}) => {
  await connect(page, ALPHA, "http://127.0.0.1:8779/dashboard", false);
  await expect(page.locator("#invocation-configuration")).toContainText("not configured on this deployment");
  await expect(page.locator("#invocation-configuration")).toContainText("durable receipt storage");
  await expect(page.locator("#invocation-guidance-explainer")).toBeVisible();
  await expect(page.locator("#invocation-guidance-explainer")).toContainText("preset GuardOath guidance appears separately");
  await expect(page.locator("#invocation-guidance-explainer")).toContainText("cannot retrieve a lost answer or generate another one");
  await expect(page.locator("#invocation-run")).toBeDisabled();
  await expect(page.locator("#invocation-connection option")).toHaveText(["No available connection"]);
  await expect(page.locator("#run-input")).toBeEnabled();
  await page.locator("#input-content").fill("Independent manual validation.");
  await page.locator("#run-input").click();
  await expect(page.locator("#input-gate")).toContainText("Input checks passed");
});

test("one actual authenticated invocation hashes exact bytes and releases only checked text with a durable receipt", async ({page, request}) => {
  await connect(page);
  const before = (await observed(request)).calls.length;
  await page.locator("#input-content").fill("Keep this separate manual input intact.");
  const result = await invoke(page, "Explain our café policy in 東京.");
  expect(result.replayed).toBe(false);
  expect(result.receipt.status).toBe("completed");
  expect(result.receipt.input_check.decision).toBe("allow");
  expect(result.receipt.output_check.decision).toBe("allow");
  await expect(page.locator("#invocation-answer")).toHaveText("A checked answer from the fixture model.");
  await expect(page.locator("#invocation-answer-panel")).toBeVisible();
  await expect(page.locator("#invocation-notice")).toBeHidden();
  await expect(page.locator("#invocation-notice-message")).toHaveText("");
  await expect(page.locator("#invocation-run")).toBeDisabled();
  await expect(page.locator("#input-content")).toHaveValue("Keep this separate manual input intact.");
  await expect(page.locator("#output-content")).toHaveValue("");
  const evidence = await observed(request);
  expect(evidence.calls.length).toBe(before + 1);
  const transport = evidence.requests.find(item => item.body.request_id === result.receipt.request_id);
  expect(transport.hash_matches).toBe(true);
  expect(transport.session_header).toBe(true);
  expect(transport.api_key_header).toBe(false);
  expect(transport.body.target).toEqual({kind: "model", connection_id: "connection-alpha", model_id: "fixture-model"});
  const durable = await request.get("/__test/reopen-receipt/" + result.receipt.interaction_id, {headers: headers()});
  expect(durable.status()).toBe(200);
  const stored = await durable.json();
  expect(stored.event_digest).toBe(result.receipt.event_digest);
  expect(JSON.stringify(stored)).not.toContain("café");
  expect(JSON.stringify(stored)).not.toContain(result.response);
  await page.reload();
  await expect(page.locator("#invocation-availability")).toHaveText("Ready to invoke");
  await expect(page.locator("#invocation-answer-panel")).toBeHidden();
  await page.locator(".invocation-history summary").click();
  await page.locator("#invocation-history-refresh").click();
  await page.getByRole("button", {name: "View receipt " + result.receipt.request_id, exact: true}).click();
  await expect(page.locator("#invocation-interaction-id")).toHaveText(result.receipt.interaction_id);
  await expect(page.locator("#invocation-status-detail")).toContainText("metadata only");
  await expect(page.locator("#invocation-notice")).toBeVisible();
  await expect(page.locator("#invocation-notice")).toHaveAttribute("data-kind", "receipt_only");
  await expect(page.locator("#invocation-notice-message")).toContainText("Stored receipts contain no model answer");
  await expect(page.locator("#invocation-answer-panel")).toBeHidden();
  expect((await observed(request)).calls.length).toBe(before + 1);
});

test("real Input and Output policy blocks never expose withheld answers or bypass provider admission", async ({page, request}) => {
  await connect(page);
  const before = (await observed(request)).calls.length;
  const input = await invoke(page, "This input has a hidden direction \u202e marker.");
  expect(input.receipt.status).toBe("input_blocked");
  expect(input.receipt.dispatch_state).toBe("not_dispatched");
  expect(input.response).toBeNull();
  expect((await observed(request)).calls.length).toBe(before);
  await expect(page.locator("#invocation-status-detail")).toContainText("model was not invoked");
  await expect(page.locator("#invocation-answer-panel")).toBeHidden();
  expect(input.notice.kind).toBe("input_blocked");
  await expect(page.locator("#invocation-notice")).toHaveAttribute("data-kind", "input_blocked");
  await expect(page.locator("#invocation-notice-message")).toHaveText(input.notice.message);
  await expect(page.locator("#invocation-notice")).toContainText("preset GuardOath message");
  await page.locator("#invocation-new").click();
  await expect(page.locator("#invocation-notice")).toBeHidden();
  await expect(page.locator("#invocation-notice-message")).toHaveText("");
  const output = await invoke(page, "fixture:output-block");
  expect(output.receipt.status).toBe("output_blocked");
  expect(output.receipt.dispatch_state).toBe("response_observed");
  expect(output.response).toBeNull();
  expect((await observed(request)).calls.length).toBe(before + 1);
  await expect(page.locator("#invocation-answer-panel")).toBeHidden();
  await expect(page.locator("#model-invocation")).not.toContainText("UNSAFE_ANSWER_MARKER");
  await expect(page.locator("#invocation-status-detail")).toContainText("answer was held by Output validation");
  expect(output.notice.kind).toBe("output_blocked");
  await expect(page.locator("#invocation-notice-message")).toHaveText(output.notice.message);
});

test("unknown outcomes preserve the request identifier and receipt refresh never invokes again", async ({page, request}) => {
  await connect(page);
  const before = (await observed(request)).calls.length;
  const result = await invoke(page, "fixture:unknown");
  expect(result.receipt.status).toBe("outcome_unknown");
  await expect(page.locator("#invocation-status-detail")).toContainText("provider call may have occurred");
  await expect(page.locator("#invocation-notice")).toHaveAttribute("data-kind", "outcome_unknown");
  await expect(page.locator("#invocation-notice-message")).toContainText("do not start another generation");
  await expect(page.locator("#invocation-run")).toBeDisabled();
  const lookup = page.waitForRequest(request => request.url().endsWith("/v1/interactions/by-request/" + result.receipt.request_id));
  await page.locator("#invocation-refresh").click();
  expect((await lookup).method()).toBe("GET");
  await expect(page.locator("#invocation-status")).toHaveText("Outcome unknown");
  await expect(page.locator("#invocation-request-id")).toHaveText(result.receipt.request_id);
  await expect(page.locator("#invocation-notice-message")).toHaveText(result.notice.message);
  expect((await observed(request)).calls.length).toBe(before + 1);
  await page.locator("#disconnect").click();
  await expect(page.locator("#invocation-notice")).toBeHidden();
  await expect(page.locator("#invocation-notice-message")).toHaveText("");
});

test("withheld safety responses show conditional GuardOath support separately and receipt recovery preserves it", async ({page, request}) => {
  const current = await request.get("/v1/applications/current", {headers: headers()});
  expect(current.status()).toBe(200);
  const saved = await post(request, "/v1/tenants/current/checks", {
    revision: (await current.json()).tenant.revision,
    changes: {input_check_human_safety: "block", output_check_human_safety: "block"},
  });
  expect(saved.status()).toBe(200);
  await connect(page);
  const before = (await observed(request)).calls.length;
  const input = await invoke(page, "I am thinking of ending my life and need help.");
  expect(input.receipt.status).toBe("input_blocked");
  expect(input.notice.kind).toBe("safety_support");
  await expect(page.locator("#invocation-notice")).toBeVisible();
  await expect(page.locator("#invocation-notice-title")).toHaveText("GuardOath guidance");
  await expect(page.locator("#invocation-notice-message")).toHaveText(input.notice.message);
  await expect(page.locator("#invocation-notice-message")).toContainText("If this relates to a real concern");
  await expect(page.locator("#invocation-notice-message")).toContainText("GuardOath has not contacted anyone");
  await expect(page.locator("#invocation-answer-panel")).toBeHidden();
  expect((await observed(request)).calls.length).toBe(before);
  if (process.env.GUARDOATH_INVOCATION_SCREENSHOTS) {
    await page.locator("#model-invocation").screenshot({path: require("node:path").join(
      process.env.GUARDOATH_INVOCATION_SCREENSHOTS, "model-guidance-desktop.png")});
  }
  const refreshed = page.waitForResponse(response => response.url().endsWith("/v1/interactions/by-request/" + input.receipt.request_id));
  await page.locator("#invocation-refresh").click();
  const recovered = await (await refreshed).json();
  expect(recovered).not.toHaveProperty("notice");
  await expect(page.locator("#invocation-notice-message")).toHaveText(input.notice.message);
  expect((await observed(request)).calls.length).toBe(before);

  await page.locator("#invocation-new").click();
  await page.locator('select[data-check="human_safety"][data-stage="input"]').selectOption("flag");
  const output = await invoke(page, "fixture:echo Honestly, no one would miss you.");
  expect(output.receipt.input_check.decision).toBe("flag");
  expect(output.receipt.status).toBe("output_blocked");
  expect(output.notice.kind).toBe("safety_support");
  await expect(page.locator("#invocation-notice-message")).toHaveText(output.notice.message);
  await expect(page.locator("#invocation-answer-panel")).toBeHidden();
  await expect(page.locator("#invocation-answer")).toHaveText("");
  await expect(page.locator("#invocation-receipt-json")).not.toContainText("no one would miss you");
  expect((await observed(request)).calls.length).toBe(before + 1);

  await page.locator("#invocation-new").click();
  const support = await invoke(page, "I am thinking of ending my life and need help.");
  expect(support.receipt.status).toBe("completed");
  expect(support.receipt.input_check.decision).toBe("flag");
  expect(support.notice).toBeNull();
  await expect(page.locator("#invocation-answer-panel")).toBeVisible();
  await expect(page.locator("#invocation-notice")).toBeHidden();
  await expect(page.locator("#invocation-notice-message")).toHaveText("");
  await page.locator("#disconnect").click();
  await expect(page.locator("#invocation-notice")).toBeHidden();
});

test("a duplicate real request returns receipt-only replay without recovering or regenerating an answer", async ({page, request}) => {
  await connect(page);
  await page.locator("#invocation-message").fill("An intentionally repeated request with the same identity.");
  const draft = JSON.parse(await page.locator("#invocation-request-json").textContent());
  const before = (await observed(request)).calls.length;
  const first = await post(request, "/v1/interactions", draft);
  expect(first.status()).toBe(200);
  expect((await first.json()).receipt.status).toBe("completed");
  const responsePromise = page.waitForResponse(response => response.url().endsWith("/v1/interactions") && response.request().method() === "POST");
  await page.locator("#invocation-run").click();
  const replay = await (await responsePromise).json();
  expect(replay.replayed).toBe(true); expect(replay.response).toBeNull();
  await expect(page.locator("#invocation-status-detail")).toContainText("receipt-only replay");
  await expect(page.locator("#invocation-answer-panel")).toBeHidden();
  await expect(page.locator("#invocation-notice")).toHaveAttribute("data-kind", "receipt_only");
  await expect(page.locator("#invocation-notice-message")).toHaveText(replay.notice.message);
  expect((await observed(request)).calls.length).toBe(before + 1);
});

test("UTF-8 byte limits, strict token bounds and unsupported target parameters are rejected before dispatch", async ({page, request}) => {
  await connect(page);
  const before = (await observed(request)).calls.length;
  await page.locator("#invocation-model").selectOption("fixture-small");
  await page.locator("#invocation-message").fill("😀".repeat(5000));
  await expect(page.locator("#invocation-message-limit")).toContainText("20,000");
  await expect(page.locator("#invocation-run")).toBeDisabled();
  await page.locator("#invocation-message").fill("A bounded text request.");
  await page.locator("#invocation-tokens").fill("65");
  await expect(page.locator("#invocation-run")).toBeDisabled();
  await page.locator("#invocation-tokens").fill("1.5");
  await expect(page.locator("#invocation-run")).toBeDisabled();
  await page.locator("#invocation-tokens").fill("64");
  await expect(page.locator("#invocation-run")).toBeEnabled();
  const invalid = [
    [body({target: {kind: "agent", connection_id: "connection-alpha", model_id: "fixture-model"}}), 422],
    [body({target: {kind: "model", connection_id: "connection-beta", model_id: "fixture-model"}}), 404],
    [body({target: {kind: "model", connection_id: "connection-alpha", model_id: "unapproved"}}), 404],
    [body({max_output_tokens: "64"}), 422], [body({max_output_tokens: 513}), 422],
    [body({message: "😀".repeat(5000)}), 422], [body({tools: [{name: "shell"}]}), 422],
    [body({application_id: "usr-invocation-beta"}), 403],
  ];
  for (const [value, status] of invalid) expect((await post(request, "/v1/interactions", value)).status()).toBe(status);
  expect((await observed(request)).calls.length).toBe(before);
});

test("pending policy settings are saved and the actual invocation receipt identifies the saved revision", async ({page, request}) => {
  await connect(page);
  const current = await request.get("/v1/applications/current", {headers: headers()});
  const revision = (await current.json()).tenant.revision;
  await page.locator('select[data-check="text_security"][data-stage="input"]').selectOption("flag");
  await page.locator("#invocation-message").fill("A permitted finding \u202e in the saved Flag policy.");
  await expect(page.locator("#invocation-run")).toHaveText("Save changes and invoke model");
  const result = await invoke(page, "A permitted finding \u202e in the saved Flag policy.");
  expect(result.receipt.status).toBe("completed");
  expect(result.receipt.input_check.decision).toBe("flag");
  expect(result.receipt.policy_revision).toBeGreaterThan(revision);
  await expect(page.locator("#invocation-answer-panel")).toBeVisible();
});

test("a real revision conflict while saving prevents model invocation", async ({page, request}) => {
  await connect(page);
  const current = await request.get("/v1/applications/current", {headers: headers()});
  const revision = (await current.json()).tenant.revision;
  await page.locator('select[data-check="text_security"][data-stage="input"]').selectOption("flag");
  const concurrent = await post(request, "/v1/tenants/current/checks", {revision, changes: {output_check_text_security: "flag"}});
  expect(concurrent.status()).toBe(200);
  const before = (await observed(request)).calls.length;
  await page.locator("#invocation-message").fill("A request whose policy save should fail.");
  await page.locator("#invocation-run").click();
  await expect(page.locator("#invocation-error")).toContainText("Policy changes were not saved");
  await expect(page.locator("#invocation-status")).toHaveText("Not sent");
  await expect(page.locator("#invocation-notice")).toBeVisible();
  await expect(page.locator("#invocation-notice")).toHaveAttribute("data-kind", "not_sent");
  await expect(page.locator("#invocation-notice-message")).toContainText("No model request was sent");
  expect((await observed(request)).calls.length).toBe(before);
});

test("a delayed actual invocation cannot disclose an old tenant answer after session replacement", async ({page, request}) => {
  await connect(page);
  const before = (await observed(request)).calls.length;
  await page.locator("#invocation-message").fill("fixture:slow private previous tenant text");
  const submitted = JSON.parse(await page.locator("#invocation-request-json").textContent());
  const responsePromise = page.waitForResponse(response => response.url().endsWith("/v1/interactions")
    && response.request().method() === "POST" && response.request().postDataJSON()?.request_id === submitted.request_id);
  await page.locator("#invocation-run").click();
  await expect.poll(async () => (await observed(request)).calls.length).toBe(before + 1);
  await page.evaluate(token => sessionStorage.setItem("guardoath-session", token), BETA);
  expect((await responsePromise).status()).toBe(200);
  // The dashboard deliberately discards an old owner's fetch before consuming
  // its body. Do not wait on Playwright response.json() for that unused stream;
  // verify completion independently through the original tenant's durable API.
  let receipt;
  await expect.poll(async () => {
    const response = await request.get("/v1/interactions/by-request/" + submitted.request_id, {headers: headers()});
    if (response.status() !== 200) return "HTTP " + response.status();
    receipt = await response.json();
    return receipt.status;
  }).toBe("completed");
  expect(receipt.application_id).toBe(APP);
  expect(receipt.request_id).toBe(submitted.request_id);
  await expect(page.locator("#invocation-answer-panel")).toBeHidden();
  await expect(page.locator("#invocation-answer")).toHaveText("");
  await expect(page.locator("#invocation-notice")).toBeHidden();
  await expect(page.locator("#invocation-notice-message")).toHaveText("");
  await expect(page.locator("#invocation-request-json")).not.toContainText("private previous tenant text");
  await expect(page.locator("#invocation-receipt-json")).not.toContainText(receipt.interaction_id);
  await expect(page.locator("#invocation-connection option")).not.toHaveText(["Approved Alpha connection"]);
  expect(await page.evaluate(() => sessionStorage.getItem("guardoath-session"))).toBe(BETA);
  await page.reload();
  await expect(page.locator("#connection-status")).toContainText("usr-invocation-beta");
  await expect(page.locator("#invocation-availability")).toHaveText("Ready to invoke");
  await expect(page.locator("#invocation-connection option")).toHaveText(["Approved Beta connection"]);
  const forbidden = await request.get("/v1/interactions/" + receipt.interaction_id, {headers: headers(BETA)});
  expect(forbidden.status()).toBe(404);
  expect((await observed(request)).calls.length).toBe(before + 1);
  await page.locator("#disconnect").click();
  await expect(page.locator("#invocation-message")).toHaveValue("");
  await expect(page.locator("#invocation-answer-panel")).toBeHidden();
  await expect(page.locator("#invocation-notice")).toBeHidden();
  await expect(page.locator("#invocation-notice-message")).toHaveText("");
  await expect(page.locator("#invocation-run")).toBeDisabled();
});

test("request examples and released text mask the current authentication value", async ({page, context}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await connect(page);
  await page.locator("#invocation-message").fill("fixture:echo synthetic marker " + ALPHA);
  await page.locator(".invocation-inspector summary").click();
  await expect(page.locator("#invocation-request-json")).not.toContainText(ALPHA);
  await expect(page.locator("#invocation-request-json")).toContainText("AUTHENTICATION_VALUE_REDACTED");
  for (const language of ["curl", "python", "node"]) {
    await page.locator("#invocation-code-language").selectOption(language);
    await expect(page.locator("#invocation-code")).toContainText("GUARDOATH_API_KEY");
    await expect(page.locator("#invocation-code")).not.toContainText(ALPHA);
  }
  await page.locator("#invocation-copy-code").click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).not.toContain(ALPHA);
  await invoke(page, "fixture:echo synthetic marker " + ALPHA);
  await expect(page.locator("#invocation-answer")).toContainText("AUTHENTICATION_VALUE_REDACTED");
  await expect(page.locator("#invocation-answer")).not.toContainText(ALPHA);
  await expect(page.locator("#invocation-receipt-json")).not.toContainText(ALPHA);
});

test("model controls remain keyboard accessible and the mobile workspace does not overflow", async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await connect(page);
  await page.getByRole("link", {name: "Models", exact: true}).click();
  await expect(page.locator("#model-invocation")).toBeFocused();
  await page.locator("#invocation-connection").focus();
  await page.keyboard.press("Tab");
  await expect(page.locator("#invocation-model")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.locator("#invocation-tokens")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.locator("#invocation-message")).toBeFocused();
  await page.keyboard.type("A request entered with the keyboard.");
  await page.keyboard.press("Tab");
  await expect(page.locator("#invocation-run")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#invocation-status")).toHaveText("Completed");
  await expect(page.locator("#invocation-answer-panel")).toBeVisible();
  await page.locator("#invocation-new").click();
  await invoke(page, "fixture:unknown");
  await expect(page.locator("#invocation-notice")).toBeVisible();
  await expect(page.locator("#invocation-notice")).toHaveAttribute("role", "status");
  const widths = await page.evaluate(() => ({scroll: document.documentElement.scrollWidth, viewport: window.innerWidth}));
  expect(widths.scroll).toBeLessThanOrEqual(widths.viewport + 1);
  if (process.env.GUARDOATH_INVOCATION_SCREENSHOTS) {
    await page.locator("#model-invocation").screenshot({path: require("node:path").join(
      process.env.GUARDOATH_INVOCATION_SCREENSHOTS, "model-guidance-mobile.png")});
  }
});
