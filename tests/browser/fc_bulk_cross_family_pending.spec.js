// A customer may stage one advanced rule and then import ordinary comparisons.
// The bulk editor must reject a duplicate ID before a destructive draft replace,
// while the authoritative save and one shared Engine remain final authority.
const {test, expect} = require("@playwright/test");

async function connect(page) {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}

async function current(request, headers) {
  const res = await request.get("/v1/applications/current", {headers});
  expect(res.status(), (await res.text()).slice(0, 200)).toBe(200);
  return (await res.json()).tenant;
}

test("bulk and individual ordinary drafts reject unsaved advanced IDs, then distinct tenant rules run both stages", async ({page, request}) => {
  const headers = await connect(page);
  let cleanup = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status(), (await reset.text()).slice(0, 200)).toBe(200);
    cleanup = true;
    const initial = await current(request, headers);
    const cleared = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: initial.revision,
      changes: {comparisons: [], advanced_arithmetic: [], advanced_identity: [],
                advanced_conversion_time: [], advanced_statistical_drift: []}
    }});
    expect(cleared.status(), (await cleared.text()).slice(0, 200)).toBe(200);
    const baseline = await current(request, headers);
    await connect(page);

    // Stage an advanced rule without persisting it. It is intentionally absent
    // from the server's saved record_rules until Save checks succeeds.
    await page.locator("#arithmetic-id").fill("pending_ledger");
    await page.locator("#arithmetic-kind").selectOption("invoice_line_subtotal");
    await page.locator("#arithmetic-field-lines").fill("/order/lines");
    await page.locator("#arithmetic-field-subtotal").fill("/order/subtotal");
    await page.locator("#arithmetic-apply").click();
    await expect(page.locator('[data-arithmetic-id="pending_ledger"]')).toHaveCount(1);

    await page.locator("#comparison-bulk-panel summary").click();
    const box = page.locator("#comparison-bulk-json");
    const importButton = page.locator("#comparison-bulk-import");
    const confirm = page.locator("#comparison-bulk-confirm");
    const duplicate = {rule_id: "pending_ledger", left: "/order/spend",
      right: "/order/limit", operator: "le", value_type: "number",
      stages: ["input", "output"]};
    await box.fill(JSON.stringify([duplicate]));
    await confirm.check();
    await importButton.click();
    await expect(page.locator("#comparison-bulk-status")).toContainText(
      "duplicate or reserved rule_id");
    await expect(page.locator('[data-comparison-id="pending_ledger"]')).toHaveCount(0);
    await expect(page.locator("#comparison-draft-summary")).toContainText(
      "0 comparisons configured");

    // The single-rule editor must enforce the same unsaved cross-family
    // namespace, without performing an authenticated Save.
    await page.locator("#comparison-id").fill("pending_ledger");
    await page.locator("#comparison-left").fill("/order/spend");
    await page.locator("#comparison-right").fill("/order/limit");
    await page.locator("#comparison-apply").click();
    await expect(page.locator("#comparison-message")).toContainText(
      "already used");
    await expect(page.locator('[data-comparison-id="pending_ledger"]')).toHaveCount(0);
    const unchanged = await current(request, headers);
    expect(unchanged.revision).toBe(baseline.revision);
    expect(unchanged.comparisons).toEqual([]);
    expect(unchanged.advanced_arithmetic).toEqual([]);

    // A unique ordinary ID still imports and persists alongside the advanced
    // rule through the normal customer Save, with both-stage Engine outcomes.
    const valid = {...duplicate, rule_id: "pending_limit"};
    await box.fill(JSON.stringify([valid]));
    await importButton.click();
    await expect(page.locator("#comparison-bulk-status")).toContainText(
      "1 comparison definitions staged");
    await page.locator("#tenant-check-input-cross_field").selectOption("flag");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await current(request, headers);
    expect(saved.revision).toBe(baseline.revision + 1);
    expect(saved.comparisons.map(r => r.rule_id)).toEqual(["pending_limit"]);
    expect(saved.advanced_arithmetic.map(r => r.rule_id)).toEqual(["pending_ledger"]);
    await connect(page);
    for (const id of ["pending_limit", "pending_ledger"]) {
      await page.locator("#rule-search").fill(id);
      await expect(page.locator('tr.rule-row[data-rule="business.' + id + '"]'))
        .toBeVisible();
    }

    const documents = [
      [{lines: [{quantity: 2, unit_price: 40}], subtotal: 90,
        spend: 120, limit: 100}, ["business.pending_ledger", "business.pending_limit"]],
      [{lines: [{quantity: 2, unit_price: 40}], subtotal: 80,
        spend: 80, limit: 100}, []]
    ];
    for (const stage of ["input", "output"]) {
      for (const [order, expected] of documents) {
        const response = await request.post("/v1/checks/" + stage, {headers, data: {
          application_id: "demo2", content_format: "json",
          content: JSON.stringify({order}),
        }});
        expect(response.status(), (await response.text()).slice(0, 200)).toBe(200);
        const result = await response.json();
        expect(result.evaluation_status).toBe("complete");
        expect(result.findings.map(f => f.rule_id)
          .filter(id => id.startsWith("business.pending_")).sort()).toEqual(expected);
        expect(result.effective_action).toBe(expected.length
          ? (stage === "input" ? "flag" : "block") : "allow");
      }
    }
  } finally {
    if (cleanup) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status(), (await reset.text()).slice(0, 200)).toBe(200);
    }
  }
});
