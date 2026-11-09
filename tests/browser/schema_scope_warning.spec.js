// A sample from one check is still evaluated against EVERY enabled tenant check.
// The dashboard must make overlapping JSON Schema policy visible before Run,
// not silently disable the required check or pretend this is isolated execution.
const {test, expect} = require("@playwright/test");

async function connect(page) {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo3");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo3");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}

async function tenant(request, headers) {
  const r = await request.get("/v1/applications/current", {headers});
  expect(r.status(), (await r.text()).slice(0, 150)).toBe(200);
  return (await r.json()).tenant;
}

test("customer sees concurrent-schema warning when testing another rule; engine still blocks invalid JSON", async ({page, request}) => {
  const headers = await connect(page);
  let cleanup = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    cleanup = true;
    const prior = await tenant(request, headers);
    const schema = {
      type: "object", properties: {question: {type: "string"}},
      required: ["question"], additionalProperties: false
    };
    const change = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: prior.revision, changes: {
        input_check_schema: "block", output_check_schema: "block",
        input_schema: schema, output_schema: schema
      }
    }});
    expect(change.status(), (await change.text()).slice(0, 160)).toBe(200);
    const saved = await tenant(request, headers);
    await connect(page);
    // Authenticated tenants automatically use their authoritative tenant policy.
    // The profile selector lives inside a hidden developer-only details panel,
    // so trying to selectOption there hangs waiting for visibility.
    await expect(page.locator("#policy")).toHaveValue("tenant");
    await expect(page.locator("#tenant-check-input-schema")).toHaveValue("block");
    await expect(page.locator("#tenant-check-output-schema")).toHaveValue("block");

    // Text checks (e.g. prompt injection) are still accompanied by required
    // schema validation. This is an incomplete full-policy result, not a clean
    // isolated prompt-injection pass, and must never be silently whitelisted.
    await page.locator("#input-format").selectOption("text");
    await expect(page.locator("#input-policy-scope")).toBeVisible();
    await expect(page.locator("#input-policy-scope")).toContainText("JSON Schema");
    await expect(page.locator("#input-policy-scope")).toContainText("incomplete");
    await expect(page.locator("#output-policy-scope")).toContainText("JSON Schema");

    const plain = await request.post("/v1/checks/input", {headers, data: {
      application_id: "demo3", content_format: "text",
      content: "Explain the reimbursement policy."
    }});
    expect(plain.status(), (await plain.text()).slice(0, 160)).toBe(200);
    const plainResult = await plain.json();
    expect(plainResult.checker_results.find(c => c.checker_id === "schema").status).toBe("skipped");
    expect(plainResult.evaluation_status).toBe("incomplete");
    expect(plainResult.recommended_action).toBe("block");

    // Selecting another check's example cannot disable schema enforcement.
    await page.locator("#input-format").selectOption("json");
    await expect(page.locator("#input-policy-scope")).toContainText("Every JSON message");
    await page.locator("#input-example-check").selectOption("general");
    await expect(page.locator("#input-policy-scope")).toBeVisible();
    await expect(page.locator("#input-policy-scope")).toContainText("JSON Schema check also validates");
    await expect(page.locator("#input-policy-scope")).toContainText("Choosing an example does not isolate");

    const malformedForPolicy = JSON.stringify({unrelated: "safe"});
    const direct = await request.post("/v1/checks/input", {headers, data: {
      application_id: "demo3", content_format: "json", content: malformedForPolicy
    }});
    expect(direct.status(), (await direct.text()).slice(0, 160)).toBe(200);
    const directResult = await direct.json();
    expect(directResult.findings.map(f => f.rule_id)).toContain("schema.invalid");

    await page.locator("#input-content").fill(malformedForPolicy);
    const inputCall = page.waitForResponse(r => r.url().endsWith("/v1/checks/input") &&
      r.request().method() === "POST");
    await page.locator("#run-input").click();
    const fromDashboard = await (await inputCall).json();
    expect(fromDashboard.findings.map(f => f.rule_id)).toContain("schema.invalid");
    await expect(page.locator("#input-results")).toContainText("A required field is missing.");
    await expect(page.locator("#input-results")).toContainText("An unexpected field is present.");
    // The customer can now distinguish the actual schema finding from the
    // unrelated check they meant to test, without implying an isolated run.
    await expect(page.locator("#input-results .check-attribution")).toContainText(
      "Checks reporting findings: schema");
    await expect(page.locator("#input-results .check-attribution")).toContainText(
      "All enabled checks participate in the final policy decision");
    // The customer sees the real returned rule ID, not just an ambiguous
    // description. This is independent of whichever example was selected.
    await expect(page.locator("#input-results .finding-rule-id").first()).toHaveText("schema.invalid");
    await expect(page.locator("#input-results .finding-rule-id")).toHaveCount(
      fromDashboard.findings.filter(f => f.rule_id === "schema.invalid").length);
    expect((await tenant(request, headers)).revision).toBe(saved.revision);
    expect(JSON.stringify(fromDashboard.findings)).not.toContain("unrelated");

    const good = await request.post("/v1/checks/input", {headers, data: {
      application_id: "demo3", content_format: "json",
      content: JSON.stringify({question: "Where can I find the policy?"})
    }});
    expect(good.status(), (await good.text()).slice(0, 160)).toBe(200);
    expect((await good.json()).checker_results.find(c => c.checker_id === "schema").status).toBe("passed");
  } finally {
    if (cleanup) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status(), (await reset.text()).slice(0, 160)).toBe(200);
    }
  }
});
