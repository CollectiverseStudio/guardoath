"use strict";
const {test, expect} = require("@playwright/test");
const {createHash} = require("node:crypto");

// Targeted presentation-boundary tests deliberately remove/corrupt only the
// optional notice or a receipt-read response. Model requests, tenant saves,
// validation and durable dispatch run through the real local ASGI fixture.
test.skip(process.env.GUARDOATH_INVOCATION_BROWSER !== "1", "Authenticated invocation fixture required.");
const TOKEN = "synthetic-invocation-owner-alpha";
const headers = {"X-GuardOath-Session": TOKEN};

async function calls(request) {
  const response = await request.get("/__test/observations", {headers});
  expect(response.status()).toBe(200);
  return (await response.json()).calls.length;
}

test.beforeEach(async ({request}) => {
  const current = await request.get("/v1/applications/current", {headers});
  expect(current.status()).toBe(200);
  const text = JSON.stringify({revision: (await current.json()).tenant.revision,
    changes: {mode: "enforce", input_check_text_security: "block", output_check_text_security: "block",
      input_check_human_safety: null, output_check_human_safety: null,
      input_check_harmful_content: null, output_check_harmful_content: null}});
  const saved = await request.post("/v1/tenants/current/checks", {headers: {...headers,
    "Content-Type": "application/json", "x-amz-content-sha256": createHash("sha256").update(text).digest("hex")}, data: text});
  expect(saved.status()).toBe(200);
});

async function connect(page) {
  await page.addInitScript(token => sessionStorage.setItem("guardoath-session", token), TOKEN);
  await page.goto("/dashboard");
  await expect(page.locator("#invocation-availability")).toHaveText("Ready to invoke");
}

test("older responses without a notice still display preset guidance from the real receipt", async ({page, request}) => {
  await page.route("**/v1/interactions", async route => {
    if (route.request().method() !== "POST") return route.continue();
    const actual = await route.fetch();
    const value = await actual.json();
    delete value.notice;
    await route.fulfill({response: actual, json: value});
  });
  await connect(page);
  const before = await calls(request);
  await page.locator("#invocation-message").fill("An input with a hidden \u202e marker.");
  await page.locator("#invocation-run").click();
  await expect(page.locator("#invocation-status")).toHaveText("Input blocked");
  await expect(page.locator("#invocation-notice")).toHaveAttribute("data-kind", "input_blocked");
  await expect(page.locator("#invocation-notice-message")).toContainText("before a model was called");
  await expect(page.locator("#invocation-answer-panel")).toBeHidden();
  expect(await calls(request)).toBe(before);
});

test("untrusted notice content and failed recovery stay visible as safe guidance without releasing or retrying", async ({page, request}) => {
  const injected = '<img id="untrusted-notice-image" src=x onerror="window.noticeInjectionRan=true"> ' + TOKEN;
  await page.route("**/v1/interactions", async route => {
    if (route.request().method() !== "POST") return route.continue();
    const actual = await route.fetch();
    const value = await actual.json();
    value.notice = {kind: "unavailable", message: injected};
    await route.fulfill({response: actual, json: value});
  });
  await connect(page);
  const before = await calls(request);
  await page.locator("#invocation-message").fill("A harmless fixture request with a corrupted presentation response.");
  const original = JSON.parse(await page.locator("#invocation-request-json").textContent());
  await page.locator("#invocation-run").click();
  await expect(page.locator("#invocation-status")).toHaveText("Receipt needed");
  await expect(page.locator("#invocation-notice")).toHaveAttribute("data-kind", "outcome_unknown");
  await expect(page.locator("#invocation-notice-message")).toContainText("do not start another generation");
  await expect(page.locator("#invocation-notice-message")).not.toContainText(TOKEN);
  await expect(page.locator("#invocation-notice-message")).not.toContainText("img");
  await expect(page.locator("#untrusted-notice-image")).toHaveCount(0);
  expect(await page.evaluate(() => window.noticeInjectionRan)).toBeUndefined();
  await expect(page.locator("#invocation-answer-panel")).toBeHidden();
  await expect(page.locator("#invocation-answer")).toHaveText("");
  expect(await calls(request)).toBe(before + 1);

  let recoveries = 0;
  await page.route("**/v1/interactions/by-request/" + original.request_id, async route => {
    expect(route.request().method()).toBe("GET"); recoveries++;
    await route.fulfill({status: 503, contentType: "application/json", body: JSON.stringify({detail: injected})});
  });
  await page.locator("#invocation-refresh").click();
  await expect(page.locator("#invocation-notice")).toHaveAttribute("data-kind", "receipt_unavailable");
  await expect(page.locator("#invocation-notice-message")).toContainText("The refresh made no model call");
  await expect(page.locator("#invocation-notice-message")).toContainText("cannot recover a lost answer");
  await expect(page.locator("#invocation-notice-message")).not.toContainText(TOKEN);
  expect(JSON.parse(await page.locator("#invocation-request-json").textContent()).request_id).toBe(original.request_id);
  expect(recoveries).toBe(1);
  expect(await calls(request)).toBe(before + 1);
  await expect(page.locator("#invocation-run")).toBeDisabled();
  await page.locator("#disconnect").click();
  await expect(page.locator("#invocation-notice")).toBeHidden();
  await expect(page.locator("#invocation-notice-message")).toHaveText("");
});
