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
