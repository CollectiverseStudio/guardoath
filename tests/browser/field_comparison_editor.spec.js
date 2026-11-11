const { test, expect } = require("@playwright/test");

test("tenant can create, reload, edit stages and remove a comparison from the dashboard", async ({ page }) => {
  async function connect() {
    await page.goto("/dashboard");
    await page.locator("#demo-user").selectOption("demo2");
    await page.getByRole("button", { name: "Get demo API key", exact: true }).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  }
  await connect();
  await page.getByRole("button", { name: "Reset to defaults", exact: true }).click();
  await expect(page.locator("#tenant-message")).toHaveText("Reset to the demo defaults.");
  const token = await page.locator("#demo-key-value").inputValue();
  const headers = { Authorization: "Bearer " + token };
  async function current() {
    const response = await page.request.get("/v1/applications/current", { headers });
    expect(response.ok()).toBeTruthy();
    return (await response.json()).tenant;
  }
  const before = await current();
  const id = "dashboard_editor_rule";
  const row = page.locator('#comparison-drafts li[data-comparison-id="' + id + '"]');
  try {
    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("flag");
    await page.locator("#comparison-id").fill(id);
    await page.locator("#comparison-left").fill("not-a-pointer");
    await page.locator("#comparison-right").fill("/editor/right");
    await page.locator("#comparison-apply").click();
    await expect(page.locator("#comparison-message")).toContainText("absolute JSON pointers");
    await expect(row).toHaveCount(0);
    await page.locator("#comparison-left").fill("/editor/left");
    await page.locator("#comparison-apply").click();
    await expect(row).toHaveCount(1);
    expect((await current()).comparisons).toEqual(before.comparisons); // Draft is not active yet.
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    let saved = await current();
    expect(saved.comparisons).toEqual(before.comparisons.concat([{
      rule_id: id, left: "/editor/left", right: "/editor/right", operator: "le",
      value_type: "number", stages: ["input", "output"]
    }]));
    await connect(); // Reload from the authenticated backend, not browser draft state.
    await expect(row).toHaveCount(1);
    await page.locator("#rule-search").fill(id);
    const rule = page.locator('tr.rule-row[data-rule="business.' + id + '"]');
    await expect(rule).toBeVisible();
    await rule.locator('[data-stage="input"]').selectOption("block");
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    for (const [stage, action] of [["input", "block"], ["output", "flag"]]) {
      const response = await page.request.post("/v1/checks/" + stage, { headers, data: {
        application_id: "demo2", content_format: "json", content: '{"editor":{"left":2,"right":1}}'
      }});
      expect(response.ok()).toBeTruthy();
      const result = await response.json();
      expect(result.effective_action).toBe(action);
      expect(result.findings.map(item => item.rule_id)).toContain("business." + id);
    }
    // Checks update the tenant revision; reconnect before making the next edit.
    await connect();
    await page.getByRole("button", { name: "Edit comparison " + id, exact: true }).click();
    await expect(page.locator("#comparison-id")).toBeDisabled();
    await page.locator("#comparison-left").fill("/order_date");
    await page.locator("#comparison-right").fill("/shipment_date");
    await page.locator("#comparison-type").selectOption("date");
    await page.locator("#comparison-stage-input").uncheck();
    await page.locator("#comparison-apply").click();
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    saved = await current();
    expect(saved.comparisons.find(item => item.rule_id === id)).toEqual({
      rule_id: id, left: "/order_date", right: "/shipment_date", operator: "le",
      value_type: "date", stages: ["output"]
    });
    expect(saved.input_rule_actions["business." + id]).toBeUndefined();
    await page.getByRole("button", { name: "Remove comparison " + id, exact: true }).click();
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    expect((await current()).comparisons).toEqual(before.comparisons);
    await expect(row).toHaveCount(0);
  } finally {
    await connect();
    await page.getByRole("button", { name: "Reset to defaults", exact: true }).click();
    await expect(page.locator("#tenant-message")).toHaveText("Reset to the demo defaults.");
  }
});


test("comparison IDs enforce native HTML v-pattern validation before a draft can be saved", async ({page}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name:"Get demo API key", exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  await page.locator("#comparison-id").fill("invalid space");
  await page.locator("#comparison-left").fill("/left");
  await page.locator("#comparison-right").fill("/right");
  expect(await page.locator("#comparison-id").evaluate(e => e.validity.patternMismatch)).toBe(true);
  await page.locator("#comparison-apply").click();
  await expect(page.locator('#comparison-drafts [data-comparison-id="invalid space"]')).toHaveCount(0);
  // Existing test previously treated a hyphen/dot ID as valid even though
  // the authoritative tenant record rejects it. Preserve native validation
  // and require full parity with the server's lowercase rule namespace.
  await page.locator("#comparison-id").fill("valid_rule-1.x");
  expect(await page.locator("#comparison-id").evaluate(e => e.validity.patternMismatch)).toBe(true);
  await page.locator("#comparison-id").fill("valid_rule_1");
  expect(await page.locator("#comparison-id").evaluate(e => e.validity.patternMismatch)).toBe(false);
});


test("guided refund example stays a draft until authenticated Save and blocks in shared Engine", async ({page, request}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  const headers = {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
  const current = async () => {
    const response = await request.get("/v1/applications/current", {headers});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  };
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    await page.goto("/dashboard");
    await page.locator("#demo-user").selectOption("demo2");
    await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo2");

    const baseline = await current();
    await page.locator("#comparison-sample").click();
    await expect(page.locator("#comparison-id")).toHaveValue("refund_not_over_paid");
    await expect(page.locator("#comparison-left")).toHaveValue("/invoice/refund");
    await expect(page.locator("#comparison-right")).toHaveValue("/invoice/paid");
    await expect(page.locator("#comparison-message")).toContainText("not added or saved");
    expect((await current()).comparisons).toEqual(baseline.comparisons);
    expect((await current()).revision).toBe(baseline.revision);

    await page.locator("#comparison-apply").click();
    await expect(page.locator("#comparison-draft-summary")).toContainText("1 added");
    expect((await current()).comparisons).toEqual(baseline.comparisons);
    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await current();
    expect(saved.comparisons.find(x => x.rule_id === "refund_not_over_paid")).toEqual({
      rule_id: "refund_not_over_paid", left: "/invoice/refund", right: "/invoice/paid",
      operator: "le", value_type: "number", stages: ["input", "output"]
    });

    for (const stage of ["input", "output"]) {
      for (const [refund, shouldBlock] of [[75, true], [25, false]]) {
        const response = await request.post("/v1/checks/" + stage, {headers, data: {
          application_id: "demo2", content_format: "json",
          content: JSON.stringify({invoice: {refund, paid: 50}})
        }});
        expect(response.status()).toBe(200);
        const data = await response.json();
        expect(data.evaluation_status).toBe("complete");
        const found = data.findings.map(x => x.rule_id).includes("business.refund_not_over_paid");
        expect(found).toBe(shouldBlock);
        if (shouldBlock) expect(data.effective_action).toBe("block");
      }
    }
    // A second click must not replace or alter a saved definition.
    await page.locator("#comparison-sample").click();
    await expect(page.locator("#comparison-message")).toContainText("already exists");
    expect((await current()).comparisons).toEqual(saved.comparisons);
  } finally {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
  }
});

