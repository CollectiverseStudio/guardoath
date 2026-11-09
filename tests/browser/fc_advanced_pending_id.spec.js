// Cross-family IDs are a tenant-wide namespace even before Save checks.
// Exercise the real authenticated dashboard, shared tenant and Engine.
const {test, expect} = require("@playwright/test");

async function connect(page) {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}
async function record(request, headers) {
  const response = await request.get("/v1/applications/current", {headers});
  expect(response.status(), (await response.text()).slice(0, 160)).toBe(200);
  return (await response.json()).tenant;
}

test("staged ordinary rule ID is rejected by all three advanced editors before Save, with real two-stage evaluation", async ({page, request}) => {
  const headers = await connect(page);
  let cleanup = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status(), (await reset.text()).slice(0, 160)).toBe(200);
    cleanup = true;
    const before = await record(request, headers);
    const clear = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: before.revision,
      changes: {comparisons: [], advanced_arithmetic: [], advanced_identity: [],
                advanced_conversion_time: [], advanced_statistical_drift: []}
    }});
    expect(clear.status(), (await clear.text()).slice(0, 160)).toBe(200);
    const baseline = await record(request, headers);
    await connect(page);

    // Ordinary is still an unsaved local draft, so the server catalog cannot
    // detect this reservation until the customer chooses Save checks.
    await page.locator("#comparison-bulk-panel summary").click();
    await page.locator("#comparison-bulk-json").fill(JSON.stringify([{
      rule_id: "pending_shared", left: "/order/spend", right: "/order/limit",
      operator: "le", value_type: "number", stages: ["input", "output"]
    }]));
    await page.locator("#comparison-bulk-confirm").check();
    await page.locator("#comparison-bulk-import").click();
    await expect(page.locator('[data-comparison-id="pending_shared"]')).toHaveCount(1);

    await page.locator("#arithmetic-id").fill("pending_shared");
    await page.locator("#arithmetic-kind").selectOption("invoice_line_subtotal");
    await page.locator("#arithmetic-field-lines").fill("/order/lines");
    await page.locator("#arithmetic-field-subtotal").fill("/order/subtotal");
    await page.locator("#arithmetic-apply").click();
    await expect(page.locator("#arithmetic-message")).toContainText("Rule ID already exists");
    await expect(page.locator('[data-arithmetic-id="pending_shared"]')).toHaveCount(0);

    await page.locator("#identity-id").fill("pending_shared");
    await page.locator("#identity-kind").selectOption("order_id_match");
    await page.locator("#identity-field-order_id").fill("/order/id");
    await page.locator("#identity-field-shipment_order_id").fill("/shipment/order_id");
    await page.locator("#identity-apply").click();
    await expect(page.locator("#identity-message")).toContainText("Rule ID already exists");
    await expect(page.locator('[data-identity-id="pending_shared"]')).toHaveCount(0);

    await page.locator("#conversion-id").fill("pending_shared");
    await page.locator("#conversion-kind").selectOption("unit_load_kg_cap");
    await page.locator("#conversion-field-load").fill("/load");
    await page.locator("#conversion-field-load_unit").fill("/load_unit");
    await page.locator("#conversion-field-max_kg").fill("/max_kg");
    await page.locator("#conversion-apply").click();
    await expect(page.locator("#conversion-message")).toContainText("Rule ID already exists");
    await expect(page.locator('[data-conversion-id="pending_shared"]')).toHaveCount(0);

    const unchanged = await record(request, headers);
    expect(unchanged.revision).toBe(baseline.revision);
    expect(unchanged.comparisons).toEqual([]);
    expect(unchanged.advanced_arithmetic).toEqual([]);
    expect(unchanged.advanced_identity).toEqual([]);
    expect(unchanged.advanced_conversion_time).toEqual([]);

    // A different advanced ID still works via the same tenant save, rule table,
    // authenticated Input/Output endpoints and shared validation Engine.
    await page.locator("#arithmetic-id").fill("unique_invoice");
    await page.locator("#arithmetic-apply").click();
    await expect(page.locator('[data-arithmetic-id="unique_invoice"]')).toHaveCount(1);
    // Pending advanced IDs are also exclusive across the remaining advanced
    // families, even before any tenant Save or server catalog registration.
    await page.locator("#identity-id").fill("unique_invoice");
    await page.locator("#identity-apply").click();
    await expect(page.locator("#identity-message")).toContainText("Rule ID already exists");
    await expect(page.locator('[data-identity-id="unique_invoice"]')).toHaveCount(0);
    await page.locator("#conversion-id").fill("unique_invoice");
    await page.locator("#conversion-apply").click();
    await expect(page.locator("#conversion-message")).toContainText("Rule ID already exists");
    await expect(page.locator('[data-conversion-id="unique_invoice"]')).toHaveCount(0);

    // Identity -> conversion is likewise blocked; remove the temporary unique
    // identity draft before saving the intended ordinary + arithmetic pair.
    await page.locator("#identity-id").fill("unique_identity");
    await page.locator("#identity-apply").click();
    await expect(page.locator('[data-identity-id="unique_identity"]')).toHaveCount(1);
    await page.locator("#conversion-id").fill("unique_identity");
    await page.locator("#conversion-apply").click();
    await expect(page.locator("#conversion-message")).toContainText("Rule ID already exists");
    await expect(page.locator('[data-conversion-id="unique_identity"]')).toHaveCount(0);
    const stillSaved = await record(request, headers);
    expect(stillSaved.revision).toBe(baseline.revision);
    await page.getByRole("button", {name: "Remove identity unique_identity"}).click();
    await expect(page.locator('[data-identity-id="unique_identity"]')).toHaveCount(0);

    await page.locator("#tenant-check-input-cross_field").selectOption("flag");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await record(request, headers);
    expect(saved.revision).toBe(baseline.revision + 1);
    expect(saved.comparisons.map(item => item.rule_id)).toEqual(["pending_shared"]);
    expect(saved.advanced_arithmetic.map(item => item.rule_id)).toEqual(["unique_invoice"]);

    await connect(page);
    for (const id of ["pending_shared", "unique_invoice"]) {
      await page.locator("#rule-search").fill(id);
      await expect(page.locator('tr.rule-row[data-rule="business.' + id + '"]')).toBeVisible();
    }

    for (const stage of ["input", "output"]) {
      for (const [order, expected] of [
        [{lines: [{quantity: 2, unit_price: 40}], subtotal: 90, spend: 120, limit: 100},
         ["business.pending_shared", "business.unique_invoice"]],
        [{lines: [{quantity: 2, unit_price: 40}], subtotal: 80, spend: 80, limit: 100}, []]
      ]) {
        const response = await request.post("/v1/checks/" + stage, {headers, data: {
          application_id: "demo2", content_format: "json", content: JSON.stringify({order})
        }});
        expect(response.status(), (await response.text()).slice(0, 160)).toBe(200);
        const result = await response.json();
        expect(result.evaluation_status).toBe("complete");
        expect(result.findings.map(f => f.rule_id)
          .filter(id => id === "business.pending_shared" || id === "business.unique_invoice")
          .sort()).toEqual(expected);
        expect(result.effective_action).toBe(expected.length
          ? (stage === "input" ? "flag" : "block") : "allow");
      }
    }
  } finally {
    if (cleanup) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status(), (await reset.text()).slice(0, 160)).toBe(200);
    }
  }
});
