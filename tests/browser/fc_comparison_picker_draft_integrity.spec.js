// Real authenticated dashboard proof: the common Input/Output example picker
// cannot serve saved tenant-generated comparison witnesses after unsaved edits.
// The four families share one tenant/Engine; no API or browser responses mocked.
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
  expect(res.status(), (await res.text()).slice(0, 150)).toBe(200);
  return (await res.json()).tenant;
}

const ids = ["picker_ordinary", "picker_math", "picker_identity", "picker_load"];
function definitions() {
  return {
    comparisons: [{
      rule_id: ids[0], left: "/ordinary/a", right: "/ordinary/b", operator: "le",
      value_type: "number", stages: ["input", "output"]
    }],
    advanced_arithmetic: [{
      rule_id: ids[1], kind: "invoice_total",
      fields: {subtotal: "/calc/subtotal", discount: "/calc/discount",
        tax: "/calc/tax", shipping: "/calc/shipping", total: "/calc/total"},
      stages: ["input", "output"]
    }],
    advanced_identity: [{
      rule_id: ids[2], kind: "order_id_match",
      fields: {order_id: "/ids/order_id", shipment_order_id: "/ids/shipment_order_id"},
      stages: ["input", "output"]
    }],
    advanced_conversion_time: [{
      rule_id: ids[3], kind: "unit_load_kg_cap",
      fields: {load: "/load/amount", load_unit: "/load/unit", max_kg: "/load/max_kg"},
      stages: ["input", "output"]
    }],
    input_check_cross_field: "flag",
    output_check_cross_field: "block"
  };
}

async function expectPickerRule(page, id, count) {
  await expect(page.locator("#input-example option").filter({hasText: "business." + id})).toHaveCount(count);
}

// Each saved example uses a positional <option> value in the shared picker.
// Removing pending rules must clear that selection rather than leaving the
// same index pointing to a different existing customer's rule.
async function selectSavedRuleExample(page, id) {
  const option = page.locator("#input-example option")
    .filter({hasText: "business." + id}).first();
  await expect(option).toHaveCount(1);
  const key = await option.getAttribute("value");
  await page.locator("#input-example").selectOption(key);
  await expect(page.locator("#input-example")).toHaveValue(key);
  expect(key).not.toBe("");
}

test("pending ordinary and advanced definitions hide stale common picker witnesses until tenant Save", async ({page, request}) => {
  const headers = await connect(page);
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    resetRequired = true;
    const baseline = await current(request, headers);
    const configured = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: baseline.revision, changes: definitions()
    }});
    expect(configured.status(), (await configured.text()).slice(0, 200)).toBe(200);
    const saved = await current(request, headers);
    expect(saved.revision).toBe(baseline.revision + 1);
    for (const id of ids) {
      const rule = saved.record_rules.find(row => row.rule_id === "business." + id);
      expect(typeof rule?.example, id).toBe("string");
      expect(typeof rule?.look_alike, id).toBe("string");
    }

    await connect(page);
    await page.locator("#input-example-check").selectOption("cross_field");
    for (const id of ids) await expectPickerRule(page, id, 2);

    await selectSavedRuleExample(page, ids[0]);
    await page.getByRole("button", {name: "Edit comparison " + ids[0]}).click();
    await page.locator("#comparison-left").fill("/ordinary/a_v2");
    await page.locator("#comparison-apply").click();
    await expectPickerRule(page, ids[0], 0);
    await expect(page.locator("#input-example")).toHaveValue("");
    for (const id of ids.slice(1)) await expectPickerRule(page, id, 2);
    await expect(page.locator("#input-example-note")).toContainText(
      "Saved comparison-rule examples are hidden");

    const edits = [
      ["arithmetic", ids[1], "#arithmetic-field-total", "/calc/total_v2"],
      ["identity", ids[2], "#identity-field-shipment_order_id", "/ids/shipment_order_id_v2"],
      ["conversion", ids[3], "#conversion-field-load", "/load/amount_v2"]
    ];
    for (const [family, id, field, pointer] of edits) {
      await selectSavedRuleExample(page, id);
      await page.getByRole("button", {name: "Edit " + family + " " + id}).click();
      await page.locator(field).fill(pointer);
      await page.locator("#" + family + "-apply").click();
      await expectPickerRule(page, id, 0);
      await expect(page.locator("#input-example")).toHaveValue("");
    }
    // The typed tenant policy remains unchanged until the revisioned Save.
    for (const id of ids) await expectPickerRule(page, id, 0);
    const beforeSave = await current(request, headers);
    expect(beforeSave.revision).toBe(saved.revision);
    for (const field of ["comparisons", "advanced_arithmetic", "advanced_identity", "advanced_conversion_time", "record_rules"]) {
      expect(beforeSave[field]).toEqual(saved[field]);
    }

    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const persisted = await current(request, headers);
    expect(persisted.revision).toBe(saved.revision + 1);
    expect(persisted.comparisons[0].left).toBe("/ordinary/a_v2");
    expect(persisted.advanced_arithmetic[0].fields.total).toBe("/calc/total_v2");
    expect(persisted.advanced_identity[0].fields.shipment_order_id).toBe("/ids/shipment_order_id_v2");
    expect(persisted.advanced_conversion_time[0].fields.load).toBe("/load/amount_v2");

    await connect(page);
    await page.locator("#input-example-check").selectOption("cross_field");
    for (const id of ids) await expectPickerRule(page, id, 2);
    const exampleOption = page.locator("#input-example option")
      .filter({hasText: "business." + ids[0]}).first();
    await page.locator("#input-example").selectOption(await exampleOption.getAttribute("value"));
    expect(JSON.parse(await page.locator("#input-content").inputValue()).ordinary)
      .toHaveProperty("a_v2");

    const violating = {ordinary: {a_v2: 5, b: 1},
      calc: {subtotal: 100, discount: 10, tax: 8, shipping: 5, total_v2: 110},
      ids: {order_id: "ORDER1", shipment_order_id_v2: "ORDER2"},
      load: {amount_v2: 1500, unit: "lb", max_kg: 600}};
    for (const stage of ["input", "output"]) {
      const response = await request.post("/v1/checks/" + stage, {headers, data: {
        application_id: "demo2", content_format: "json", content: JSON.stringify(violating)
      }});
      expect(response.status(), (await response.text()).slice(0, 200)).toBe(200);
      const result = await response.json();
      expect(result.evaluation_status).toBe("complete");
      const findings = result.findings.map(f => f.rule_id)
        .filter(ruleId => ruleId.startsWith("business.")).sort();
      expect(findings).toEqual(ids.map(id => "business." + id).sort());
      expect(result.effective_action).toBe(stage === "input" ? "flag" : "block");
    }
  } finally {
    if (resetRequired) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status()).toBe(200);
    }
  }
});
