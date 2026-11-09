// Customer-operable cross-family rule namespace guard. Real tenant, Save and
// Input/Output Engine; no request mocks or hidden policy/DB mutations.
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
  expect(response.status(), (await response.text()).slice(0, 200)).toBe(200);
  return (await response.json()).tenant;
}

async function evaluate(request, headers, stage, content) {
  const response = await request.post("/v1/checks/" + stage, {headers, data: {
    application_id: "demo2", content_format: "json", content: JSON.stringify(content)
  }});
  expect(response.status(), (await response.text()).slice(0, 200)).toBe(200);
  return response.json();
}

test("cross-family pending duplicate is rejected in UI and server, then corrected Save executes both stages", async ({page, request}) => {
  const headers = await connect(page);
  let cleanup = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    cleanup = true;
    const baseline = await record(request, headers);
    const clear = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: baseline.revision, changes: {
        comparisons: [], advanced_arithmetic: [], advanced_identity: [],
        advanced_conversion_time: [], advanced_statistical_drift: []
      }
    }});
    expect(clear.status(), (await clear.text()).slice(0, 200)).toBe(200);
    await connect(page);
    const before = await record(request, headers);

    await page.locator("#comparison-id").fill("ui_same");
    await page.locator("#comparison-left").fill("/order/spend");
    await page.locator("#comparison-right").fill("/order/limit");
    await page.locator("#comparison-apply").click();
    await expect(page.locator('[data-comparison-id="ui_same"]')).toHaveCount(1);

    await page.locator("#arithmetic-id").fill("ui_same");
    await page.locator("#arithmetic-kind").selectOption("invoice_line_subtotal");
    await page.locator("#arithmetic-field-lines").fill("/order/lines");
    await page.locator("#arithmetic-field-subtotal").fill("/order/subtotal");
    await page.locator("#arithmetic-apply").click();
    // Immediate customer-side rejection is stricter than the former
    // Save-time-only duplicate detection. It leaves ordinary draft intact
    // and never stages a conflicting arithmetic rule.
    await expect(page.locator('[data-arithmetic-id="ui_same"]')).toHaveCount(0);
    await expect(page.locator("#arithmetic-message")).toContainText("Rule ID already exists");
    await expect(page.locator('[data-comparison-id="ui_same"]')).toHaveCount(1);
    const sent = [];
    page.on("request", request => {
      if (request.method() === "POST" && request.url().endsWith("/v1/tenants/current/checks"))
        sent.push(request.url());
    });
    expect(sent).toHaveLength(0);
    expect((await record(request, headers)).revision).toBe(before.revision);

    // Do not rely only on the browser guard: a direct authenticated attempt
    // to save duplicate IDs across two families must also be rejected by the
    // authoritative tenant model, leaving the revision and policy untouched.
    const illegal = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: before.revision,
      changes: {
        comparisons: [{
          rule_id: "ui_same", left: "/order/spend", right: "/order/limit",
          operator: "le", value_type: "number", stages: ["input", "output"]
        }],
        advanced_arithmetic: [{
          rule_id: "ui_same", kind: "invoice_line_subtotal",
          fields: {lines: "/order/lines", subtotal: "/order/subtotal"},
          stages: ["input", "output"]
        }],
        input_check_cross_field: "flag", output_check_cross_field: "block"
      }
    }});
    expect(illegal.status(), (await illegal.text()).slice(0, 200)).toBe(422);
    expect((await record(request, headers)).revision).toBe(before.revision);
    await expect(page.locator('[data-arithmetic-id="ui_same"]')).toHaveCount(0);
    expect(sent).toHaveLength(0);

    await page.locator("#arithmetic-id").fill("ui_subtotal");
    await page.locator("#arithmetic-field-lines").fill("/order/lines");
    await page.locator("#arithmetic-field-subtotal").fill("/order/subtotal");
    await page.locator("#arithmetic-apply").click();
    await expect(page.locator('[data-arithmetic-id="ui_subtotal"]')).toHaveCount(1);
    await expect(page.locator("#tenant-message")).not.toContainText("must be unique across all families");

    await page.locator("#tenant-check-input-cross_field").selectOption("flag");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    expect(sent).toHaveLength(1);
    const saved = await record(request, headers);
    expect(saved.revision).toBe(before.revision + 1);
    expect(saved.comparisons.map(x => x.rule_id)).toEqual(["ui_same"]);
    expect(saved.advanced_arithmetic.map(x => x.rule_id)).toEqual(["ui_subtotal"]);
    await connect(page);
    for (const id of ["business.ui_same", "business.ui_subtotal"]) {
      await page.locator("#rule-search").fill(id);
      await expect(page.locator('tr.rule-row[data-rule="' + id + '"]')).toBeVisible();
    }

    const violating = {order: {
      spend: 120, limit: 100, lines: [{quantity: 2, unit_price: 40}], subtotal: 90
    }};
    const benign = {order: {
      spend: 80, limit: 100, lines: [{quantity: 2, unit_price: 40}], subtotal: 80
    }};
    for (const stage of ["input", "output"]) {
      const bad = await evaluate(request, headers, stage, violating);
      expect(bad.evaluation_status).toBe("complete");
      expect(bad.findings.map(x => x.rule_id).filter(x => ["business.ui_same", "business.ui_subtotal"].includes(x)).sort())
        .toEqual(["business.ui_same", "business.ui_subtotal"]);
      expect(bad.effective_action).toBe(stage === "input" ? "flag" : "block");
      const good = await evaluate(request, headers, stage, benign);
      expect(good.evaluation_status).toBe("complete");
      expect(good.findings.map(x => x.rule_id).filter(x => ["business.ui_same", "business.ui_subtotal"].includes(x)))
        .toEqual([]);
    }
    expect((await record(request, headers)).revision).toBe(saved.revision);
  } finally {
    if (cleanup) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status(), (await reset.text()).slice(0, 200)).toBe(200);
    }
  }
});
