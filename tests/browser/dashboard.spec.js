const { test, expect } = require("@playwright/test");
async function connect(page) {
  await page.goto("/dashboard");
  await page.getByLabel("Application API key").fill("playwright-test-key");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo");
}
async function run(page) {
  await page.locator("#run").click();
  await expect(page.locator("#result-status")).not.toHaveText("Checking…");
}
test("clean content shows the answer after real local checks", async ({ page }) => {
  await connect(page); await run(page);
  await expect(page.locator("#result-status")).toHaveText("No findings");
  await expect(page.locator("#answer")).toBeVisible();
  await expect(page.locator("#answer")).toContainText("unused item");
});
test("output email finding holds the answer", async ({ page }) => {
  await connect(page); await page.getByRole("button", {name:"Email address", exact:true}).click(); await run(page);
  await expect(page.locator("#output-results")).toContainText("Potential email address");
  await expect(page.locator("#answer")).toBeHidden();
  await expect(page.locator("#answer-status")).toHaveText("Held for review");
});
test("input card finding is shown separately", async ({ page }) => {
  await connect(page); await page.locator("#input-content").fill("Card 4111 1111 1111 1111"); await run(page);
  await expect(page.locator("#input-results")).toContainText("Potential credit-card");
  await expect(page.locator("#answer")).toBeHidden();
});
test("editing content removes a previous result", async ({ page }) => {
  await connect(page); await run(page); await expect(page.locator("#answer")).toBeVisible();
  await page.locator("#output-content").fill("A different answer.");
  await expect(page.locator("#answer")).toBeHidden();
  await expect(page.locator("#answer-status")).toHaveText("Not checked");
});
test("answer markup is displayed as text", async ({ page }) => {
  await connect(page); await page.locator("#output-content").fill('<img src=x onerror="window.injected=true">'); await run(page);
  await expect(page.locator("#answer")).toContainText("<img");
  await expect(page.locator("#answer img")).toHaveCount(0);
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
});
test("key is not stored and disconnect clears results", async ({ page }) => {
  await connect(page); await run(page);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0,0]);
  await expect(page.locator("#api-key")).toHaveValue("");
  await page.getByRole("button", { name:"Disconnect", exact:true }).click();
  await expect(page.locator("#run")).toBeDisabled(); await expect(page.locator("#answer")).toBeHidden();
});
test("an output API failure cannot show a passed answer", async ({ page }) => {
  await connect(page); await page.route("**/v1/checks/output", route => route.fulfill({status:503,body:'{}'})); await run(page);
  await expect(page.locator("#result-status")).toHaveText("Check failed"); await expect(page.locator("#answer")).toBeHidden();
});
test("incomplete checks hold the answer", async ({ page }) => {
  await connect(page); await page.route("**/v1/checks/output", async route => {
    const response = await route.fetch(); const result = await response.json();
    result.evaluation_status = "incomplete"; result.recommended_action = "block";
    result.checker_results[0].status = "error"; result.checker_results[0].error_code = "checker_timeout";
    await route.fulfill({response,json:result});
  }); await run(page);
  await expect(page.locator("#result-status")).toHaveText("Incomplete"); await expect(page.locator("#answer")).toBeHidden();
});
test("simulated checks never look like verified coverage", async ({ page }) => {
  await connect(page); await page.route("**/v1/checks/*", async route => {
    const response = await route.fetch(); const result = await response.json();
    result.simulated = true; result.checker_results[0].simulated = true;
    await route.fulfill({response,json:result});
  }); await run(page);
  await expect(page.locator("#result-status")).toHaveText("Simulated coverage");
  await expect(page.locator("#answer-status")).toHaveText("Unverified preview");
});
test("mobile layout fits the viewport", async ({ page }) => {
  await page.setViewportSize({width:390,height:844}); await connect(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("grouped coverage does not mark planned checks as passed", async ({ page }) => {
  await connect(page); await run(page);
  await expect(page.locator('#output-results [data-group="sensitive_data"] summary')).toContainText("Passed");
  await expect(page.locator('#output-results [data-group="accuracy"] summary')).toContainText("Not implemented");
  await expect(page.locator('#output-results [data-group="structure"] summary')).toContainText("Disabled");
});

test("business policy reports cross-field failures and holds the preview", async ({ page }) => {
  await connect(page); await page.locator("#policy").selectOption("business-v1");
  await page.getByRole("button", {name:"Business rule error", exact:true}).click(); await run(page);
  await expect(page.locator('#input-results [data-group="business"] summary')).toContainText("Passed");
  await expect(page.locator('#output-results [data-group="business"] summary')).toContainText("Findings");
  await expect(page.locator("#output-results")).toContainText("date_order");
  await expect(page.locator("#output-results")).toContainText("refund_limit");
  await expect(page.locator("#answer")).toBeHidden();
});

test("invalid date formats appear in structural findings", async ({ page }) => {
  await connect(page); await page.locator("#policy").selectOption("business-v1");
  await page.getByRole("button", {name:"Business rule error", exact:true}).click();
  await page.locator("#output-content").fill(JSON.stringify({start_date:"2026-02-30",end_date:"2026-10-03",purchase_amount:100,refund_amount:25}));
  await run(page);
  await expect(page.locator('#output-results [data-group="structure"] summary')).toContainText("Findings");
  await expect(page.locator("#answer")).toBeHidden();
});

test("SSN candidates appear under sensitive data", async ({ page }) => {
  await connect(page); await page.getByRole("button", {name:"SSN candidate",exact:true}).click(); await run(page);
  await expect(page.locator('#output-results [data-group="sensitive_data"]')).toContainText("Social Security number");
  await expect(page.locator("#answer")).toBeHidden();
});

test("redaction shows a rechecked preview without returning detected values", async ({ page }) => {
  await connect(page); await page.getByRole("button", {name:"Redaction preview",exact:true}).click(); await run(page);
  const preview = page.locator('#output-results .redacted-content');
  await expect(preview).toBeVisible();
  await expect(preview).toContainText("[REDACTED_EMAIL_ADDRESS]");
  await expect(preview).toContainText("[REDACTED_US_SSN]");
  await expect(preview).toContainText("[REDACTED_CREDIT_CARD]");
  await expect(preview).not.toContainText("jane@example.com");
  await expect(page.locator('#output-results .redaction-box')).toContainText("observe mode");
  await expect(page.locator("#answer")).toBeHidden();
});

test("redacted preview uses safe text rendering", async ({ page }) => {
  await connect(page); await page.getByRole("button", {name:"Redaction preview",exact:true}).click();
  await page.locator("#output-content").fill('<img src=x onerror="window.injected=true"> jane@example.com'); await run(page);
  await expect(page.locator('#output-results .redacted-content')).toContainText("<img");
  await expect(page.locator('#output-results .redacted-content img')).toHaveCount(0);
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
});

test("credential example shows a real secret finding and holds the answer", async ({ page }) => {
  await connect(page); await page.getByRole("button", {name:"Credential pattern",exact:true}).click(); await run(page);
  await expect(page.locator('#output-results [data-group="secrets"] summary')).toContainText("Findings");
  await expect(page.locator('#output-results [data-group="secrets"]')).toContainText("GitHub token pattern");
  await expect(page.locator("#output-results")).toContainText("Recommended: block");
  await expect(page.locator("#answer")).toBeHidden();
});

test("clean text passes the selected secret patterns only", async ({ page }) => {
  await connect(page); await page.locator("#policy").selectOption("secrets-v1"); await run(page);
  await expect(page.locator('#output-results [data-group="secrets"] summary')).toContainText("Passed");
  await expect(page.locator('#output-results [data-group="sensitive_data"] summary')).toContainText("Disabled");
  await expect(page.locator("#answer")).toBeVisible();
});

test("missing required output information holds the answer", async ({ page }) => {
  await connect(page); await page.locator("#completeness-sample").click(); await run(page);
  await expect(page.locator('#input-results [data-group="business"] summary')).toContainText("Passed");
  await expect(page.locator('#output-results [data-group="business"] summary')).toContainText("Findings");
  await expect(page.locator("#output-results")).toContainText("next_steps");
  await expect(page.locator("#answer")).toBeHidden();
});
test("adding the required information releases the preview", async ({ page }) => {
  await connect(page); await page.locator("#completeness-sample").click();
  await page.locator("#output-content").fill("Summary: Return instructions are available.\nNext steps: Contact the store for instructions.");
  await run(page);
  await expect(page.locator("#result-status")).toHaveText("No findings");
  await expect(page.locator("#answer")).toBeVisible();
  await expect(page.locator('#output-results [data-group="business"]')).toContainText("No assessment of truth");
});

test("hidden characters flag the input while output passes", async ({ page }) => {
  await connect(page); await page.locator("#hidden-sample").click(); await run(page);
  await expect(page.locator('#input-results [data-group="prompt_security"] summary')).toContainText("Findings");
  await expect(page.locator('#output-results [data-group="prompt_security"] summary')).toContainText("Passed");
  await expect(page.locator("#input-results")).toContainText("invisible, bidirectional");
  await expect(page.locator("#answer")).toBeHidden();
});
test("labeled encoded text is held for review without exposing decoded text", async ({ page }) => {
  await connect(page); await page.locator("#encoded-sample").click(); await run(page);
  await expect(page.locator("#input-results")).toContainText("One layer was decoded locally");
  await expect(page.locator("#input-results")).not.toContainText("Hello");
  await expect(page.locator("#answer")).toBeHidden();
  await page.locator("#input-content").fill("Explain your return instructions."); await run(page);
  await expect(page.locator("#answer")).toBeVisible();
});

test("dashboard explains shared request allowance", async ({ page }) => {
  await connect(page);
  await expect(page.locator("#resource-limits")).toContainText("checks/minute per application");
  await expect(page.locator("#resource-limits")).toContainText("Each playground run uses two checks");
  await page.getByRole("button", {name:"Disconnect",exact:true}).click();
  await expect(page.locator("#resource-limits")).toBeEmpty();
});
test("rate limiting holds the preview and shows retry guidance", async ({ page }) => {
  await connect(page);
  await page.route("**/v1/checks/input", route => route.fulfill({status:429,headers:{"Retry-After":"17"},body:'{"detail":"private-server-detail"}'}));
  await run(page);
  await expect(page.locator("#result-status")).toHaveText("Check failed");
  await expect(page.locator("#run-error")).toHaveText("Application limit reached. Retry in 17 seconds.");
  await expect(page.locator("#run-error")).not.toContainText("private-server-detail");
  await expect(page.locator("#answer")).toBeHidden();
});
test("server capacity rejection holds the preview", async ({ page }) => {
  await connect(page);
  await page.route("**/v1/checks/output", route => route.fulfill({status:503,headers:{"Retry-After":"1"},body:'{}'}));
  await run(page);
  await expect(page.locator("#run-error")).toHaveText("Service capacity reached. Retry in 1 second.");
  await expect(page.locator("#answer")).toBeHidden();
});

test("external-provider routing and unknown cost are visible before and after checks", async ({ page }) => {
  // Local response fixtures exercise disclosure; this test never contacts Azure.
  await page.route("**/v1/applications/current", async route => {
    const response = await route.fetch(); const data = await response.json();
    data.policies[0].checks[0].external = true; data.policies[0].external_checks = "after_local_pass";
    await route.fulfill({response,json:data});
  });
  await page.route("**/v1/checks/input", async route => {
    const response = await route.fetch(); const data = await response.json();
    data.checker_results[0].external = true; data.cost_usd = null; data.cost_basis = "provider_unpriced";
    await route.fulfill({response,json:data});
  });
  await connect(page);
  await expect(page.locator("#execution-mode")).toHaveText("External provider gated");
  await expect(page.locator("#data-routing")).toContainText("only after all configured local checks pass");
  await run(page);
  await expect(page.locator("#input-results")).toContainText("Provider cost not estimated");
  await expect(page.locator("#input-results")).toContainText("external provider");
  await page.getByRole("button",{name:"Disconnect",exact:true}).click();
  await expect(page.locator("#data-routing")).toBeEmpty();
});

test("a withheld external check explains its local gate", async ({ page }) => {
  await page.route("**/v1/checks/input", async route => {
    const response = await route.fetch(); const data = await response.json();
    data.checker_results[0].status = "skipped"; data.checker_results[0].external = true;
    data.checker_results[0].error_code = "external_local_gate";
    data.evaluation_status = "incomplete"; data.cost_usd = null; data.cost_basis = "provider_unpriced";
    await route.fulfill({response,json:data});
  });
  await connect(page); await run(page);
  await expect(page.locator("#input-results")).toContainText("External check withheld: configured local checks must all pass");
  await expect(page.locator("#answer")).toBeHidden();
});

test("standalone regression report shows assertions separately from evaluations", async ({ page }, testInfo) => {
  const { execFileSync } = require("node:child_process");
  const { pathToFileURL } = require("node:url");
  const reportPath = testInfo.outputPath("regression-report.html");
  execFileSync("python", ["-m","valinator.evaluate","--pii","--html",reportPath], {stdio:"pipe"});
  const networkRequests = [];
  page.on("request", request => { if (/^https?:/.test(request.url())) networkRequests.push(request.url()); });
  await page.goto(pathToFileURL(reportPath).href);
  await expect(page.getByRole("heading",{name:"Valinator regression report"})).toBeVisible();
  await expect(page.locator("body")).toContainText("30 of 30 expectations matched");
  await expect(page.locator("body")).toContainText("Incomplete evaluations: 2");
  await expect(page.locator("body")).not.toContainText("jane@example.com");
  await expect(page.locator("tbody tr")).toHaveCount(30);
  expect(networkRequests).toEqual([]);
});
