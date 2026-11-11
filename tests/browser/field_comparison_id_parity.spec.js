// Customer-facing validation agrees with the server's authoritative record-rule ID contract.
const {test, expect} = require("@playwright/test");

async function connect(page) {
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}
async function summary(request, headers) {
  const response = await request.get("/v1/applications/current", {headers});
  expect(response.status()).toBe(200);
  return (await response.json()).tenant;
}
function comparison(id) {
  return {rule_id: id, left: "/left", right: "/right", operator: "le",
          value_type: "number", stages: ["input", "output"]};
}

test("manual and bulk comparison ID checks align with tenant API; valid ID persists and runs both stages", async ({page, request}) => {
  const headers = await connect(page);
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    resetRequired = true;
    await connect(page);
    const initial = await summary(request, headers);

    await page.locator("#comparison-id").fill("Bad-ID");
    await page.locator("#comparison-left").fill("/left");
    await page.locator("#comparison-right").fill("/right");
    await page.locator("#comparison-apply").click();
    expect(await page.locator("#comparison-id").evaluate(node => node.validity.patternMismatch)).toBe(true);
    await expect(page.locator("#comparison-draft-summary"))
      .toContainText(initial.comparisons.length + " comparisons configured");
    expect((await summary(request, headers)).revision).toBe(initial.revision);

    await page.locator("#comparison-bulk-panel > summary").click();
    for (const bad of ["Bad-ID", "dotted.name", "7starts_with_digit", "hyphen-name"]) {
      await page.locator("#comparison-bulk-json").fill(JSON.stringify([comparison(bad)]));
      await page.locator("#comparison-bulk-confirm").check();
      await page.locator("#comparison-bulk-import").click();
      await expect(page.locator("#comparison-bulk-status")).toContainText("invalid rule_id");
      await expect(page.locator("#comparison-draft-summary"))
        .toContainText(initial.comparisons.length + " comparisons configured");
    }

    // The server's stricter rule-ID contract rejects these IDs without writing.
    const denied = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: initial.revision, changes: {comparisons: [comparison("Bad-ID")]}
    }});
    expect(denied.status()).toBe(422);
    expect((await summary(request, headers)).comparisons).toEqual(initial.comparisons);

    const valid = comparison("lowercase_id_001");
    await page.locator("#comparison-bulk-json").fill(JSON.stringify([valid]));
    await page.locator("#comparison-bulk-confirm").check();
    await page.locator("#comparison-bulk-import").click();
    await expect(page.locator("#comparison-bulk-status")).toContainText("1 comparison definitions staged");
    expect((await summary(request, headers)).revision).toBe(initial.revision);

    await page.locator("#tenant-check-input-cross_field").selectOption("flag");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await summary(request, headers);
    expect(saved.revision).toBe(initial.revision + 1);
    expect(saved.comparisons).toEqual([valid]);
    await connect(page);
    await expect(page.locator('#tenant-group-cross_field .rule-row[data-rule="business.lowercase_id_001"]')).toHaveCount(1);

    for (const [stage, action] of [["input", "flag"], ["output", "block"]]) {
      const response = await request.post("/v1/checks/" + stage, {headers, data: {
        application_id: "demo2", content_format: "json",
        content: JSON.stringify({left: 9, right: 2})
      }});
      expect(response.status()).toBe(200);
      const data = await response.json();
      expect(data.evaluation_status).toBe("complete");
      expect(data.effective_action).toBe(action);
      expect(data.findings.map(x => x.rule_id)).toContain("business.lowercase_id_001");
    }
  } finally {
    if (resetRequired) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status()).toBe(200);
    }
  }
});
