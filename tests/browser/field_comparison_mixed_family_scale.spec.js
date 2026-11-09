// Browser acceptance for a real 51-rule tenant portfolio spanning ordinary,
// arithmetic, identity and conversion rules. No mocked server/Engine calls.
const {test, expect} = require("@playwright/test");

async function connect(page) {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}

async function current(request, headers) {
  const response = await request.get("/v1/applications/current", {headers});
  expect(response.status(), (await response.text()).slice(0, 150)).toBe(200);
  return (await response.json()).tenant;
}

const SIZE = 51;
function configured(size = SIZE) {
  const changes = {comparisons: [], advanced_arithmetic: [],
    advanced_identity: [], advanced_conversion_time: [],
    input_check_cross_field: "flag", output_check_cross_field: "block"};
  const good = {};
  for (let i = 0; i < size; i++) {
    const number = String(i).padStart(3, "0");
    const rule_id = "mixed_ui_" + number;
    const part = "r" + number;
    if (i % 4 === 0) {
      changes.comparisons.push({rule_id, left: "/" + part + "/a",
        right: "/" + part + "/b", operator: "le", value_type: "number",
        stages: ["input", "output"]});
      good[part] = {a: 1, b: 2};
    } else if (i % 4 === 1) {
      changes.advanced_arithmetic.push({rule_id, kind: "discount_fraction_cap",
        fields: {discount: "/" + part + "/discount",
          eligible_subtotal: "/" + part + "/eligible_subtotal",
          max_fraction: "/" + part + "/max_fraction"}, stages: ["input", "output"]});
      good[part] = {discount: 10, eligible_subtotal: 100, max_fraction: 0.2};
    } else if (i % 4 === 2) {
      changes.advanced_identity.push({rule_id, kind: "order_id_match",
        fields: {order_id: "/" + part + "/order_id",
          shipment_order_id: "/" + part + "/shipment_order_id"},
        stages: ["input", "output"]});
      good[part] = {order_id: "ORDER-1", shipment_order_id: "ORDER-1"};
    } else {
      changes.advanced_conversion_time.push({rule_id, kind: "unit_load_kg_cap",
        fields: {load: "/" + part + "/load", load_unit: "/" + part + "/load_unit",
          max_kg: "/" + part + "/max_kg"}, stages: ["input", "output"]});
      good[part] = {load: 10, load_unit: "kg", max_kg: 20};
    }
  }
  const bad = structuredClone(good);
  bad.r000.a = 9;
  const last = "r" + String(size - 1).padStart(3, "0");
  if ((size - 1) % 4 === 0) bad[last].a = 9;
  else if ((size - 1) % 4 === 1) bad[last].discount = 30;
  else if ((size - 1) % 4 === 2) bad[last].shipment_order_id = "ORDER-2";
  else bad[last].load = 50;
  return {changes, good, bad};
}

async function check(request, headers, stage, document) {
  const response = await request.post("/v1/checks/" + stage, {headers, data: {
    application_id: "demo2", content_format: "json", content: JSON.stringify(document),
  }});
  expect(response.status(), (await response.text()).slice(0, 200)).toBe(200);
  return response.json();
}

test("authenticated mixed 51-rule FC portfolio renders, saves UI override and runs both stages", async ({page, request}) => {
  const headers = await connect(page);
  let shouldReset = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    shouldReset = true;
    const before = await current(request, headers);
    const {changes, good, bad} = configured();
    const saved = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: before.revision, changes,
    }});
    expect(saved.status(), (await saved.text()).slice(0, 200)).toBe(200);
    await connect(page);
    const after = await current(request, headers);
    expect(after.revision).toBe(before.revision + 1);
    expect(after.comparisons).toHaveLength(13);
    expect(after.advanced_arithmetic).toHaveLength(13);
    expect(after.advanced_identity).toHaveLength(13);
    expect(after.advanced_conversion_time).toHaveLength(12);
    await expect(page.locator('tr.rule-row[data-rule^="business.mixed_ui_"]')).toHaveCount(SIZE);
    await expect(page.locator("#comparison-draft-summary")).toContainText("13 comparisons configured");

    // Change a rule through the REAL authenticated dashboard. Save must
    // persist it on the tenant record without deleting any other family.
    await page.locator("#rule-search").fill("mixed_ui_050");
    const last = page.locator('tr.rule-row[data-rule="business.mixed_ui_050"]');
    await expect(last).toBeVisible();
    await last.locator('[data-stage="input"]').selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const changed = await current(request, headers);
    expect(changed.revision).toBe(after.revision + 1);
    expect(changed.input_rule_actions["business.mixed_ui_050"]).toBe("block");
    expect(changed.record_rules.filter(x => x.rule_id.startsWith("business.mixed_ui_"))).toHaveLength(SIZE);

    await connect(page);
    await page.locator("#rule-search").fill("mixed_ui_050");
    await expect(page.locator('tr.rule-row[data-rule="business.mixed_ui_050"] [data-stage="input"]')).toHaveValue("block");
    const expected = ["business.mixed_ui_000", "business.mixed_ui_050"];
    for (const stage of ["input", "output"]) {
      const result = await check(request, headers, stage, bad);
      expect(result.evaluation_status).toBe("complete");
      expect(result.effective_action).toBe("block");
      const findings = result.findings.map(f => f.rule_id)
        .filter(id => id.startsWith("business.mixed_ui_")).sort();
      expect(findings).toEqual(expected);
      const benign = await check(request, headers, stage, good);
      expect(benign.evaluation_status).toBe("complete");
      expect(benign.effective_action).toBe("allow");
      expect(benign.findings.filter(f => f.rule_id.startsWith("business.mixed_ui_"))).toHaveLength(0);
    }

    // Customer can turn OFF one advanced identity rule on Output without
    // disabling its Input evaluation or any of the other 50 comparisons.
    // Exercise the normal per-rule stage select, revisioned Save and Engine,
    // not a mocked response or a cross-field check-wide bypass.
    await page.locator("#rule-search").fill("mixed_ui_050");
    await page.locator('tr.rule-row[data-rule="business.mixed_ui_050"] [data-stage="output"]')
      .selectOption("off");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const outputOff = await current(request, headers);
    expect(outputOff.revision).toBe(changed.revision + 1);
    expect(outputOff.output_rule_actions["business.mixed_ui_050"]).toBe("off");
    expect(outputOff.input_rule_actions["business.mixed_ui_050"]).toBe("block");
    expect(outputOff.record_rules.filter(x => x.rule_id.startsWith("business.mixed_ui_"))).toHaveLength(SIZE);
    await connect(page);
    await page.locator("#rule-search").fill("mixed_ui_050");
    const savedRow = page.locator('tr.rule-row[data-rule="business.mixed_ui_050"]');
    await expect(savedRow.locator('[data-stage="output"]')).toHaveValue("off");
    await expect(savedRow.locator(".effect-output")).toHaveText("Output: Off (own setting)");
    await expect(savedRow.locator(".effect-input")).toHaveText("Input: Block (own setting)");

    const stillInput = await check(request, headers, "input", bad);
    expect(stillInput.evaluation_status).toBe("complete");
    expect(stillInput.effective_action).toBe("block");
    expect(stillInput.findings.map(f => f.rule_id)
      .filter(id => id.startsWith("business.mixed_ui_")).sort()).toEqual(expected);
    const outputFiltered = await check(request, headers, "output", bad);
    expect(outputFiltered.evaluation_status).toBe("complete");
    // The remaining ordinary violation still blocks; the disabled identity
    // rule must not be reported as an independent Output finding.
    expect(outputFiltered.effective_action).toBe("block");
    expect(outputFiltered.findings.map(f => f.rule_id)
      .filter(id => id.startsWith("business.mixed_ui_"))).toEqual(["business.mixed_ui_000"]);
    expect((await current(request, headers)).revision).toBe(outputOff.revision);
  } finally {
    if (shouldReset) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status()).toBe(200);
    }
  }
});


test("authenticated 500-rule four-family dashboard renders, persists and evaluates the final rule", async ({page, request}) => {
  const headers = await connect(page);
  let resetRequired = false;
  const size = 500;
  const lastId = "business.mixed_ui_499";
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    resetRequired = true;
    const before = await current(request, headers);
    const {changes, good, bad} = configured(size);
    const saved = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: before.revision, changes,
    }});
    expect(saved.status(), (await saved.text()).slice(0, 200)).toBe(200);
    await connect(page);
    const after = await current(request, headers);
    expect(after.revision).toBe(before.revision + 1);
    for (const family of ["comparisons", "advanced_arithmetic", "advanced_identity",
                          "advanced_conversion_time"]) {
      expect(after[family], family).toHaveLength(125);
    }
    await expect(page.locator('tr.rule-row[data-rule^="business.mixed_ui_"]')).toHaveCount(size);
    await expect(page.locator("#comparison-draft-summary")).toContainText("125 comparisons configured");
    await page.locator("#rule-search").fill("mixed_ui_499");
    const last = page.locator('tr.rule-row[data-rule="' + lastId + '"]');
    await expect(last).toBeVisible();
    await last.locator('[data-stage="input"]').selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const persisted = await current(request, headers);
    expect(persisted.revision).toBe(after.revision + 1);
    expect(persisted.input_rule_actions[lastId]).toBe("block");
    expect(persisted.record_rules.filter(r => r.rule_id.startsWith("business.mixed_ui_")))
      .toHaveLength(size);
    await connect(page);
    await page.locator("#rule-search").fill("mixed_ui_499");
    await expect(page.locator('tr.rule-row[data-rule="' + lastId + '"] [data-stage="input"]'))
      .toHaveValue("block");
    for (const stage of ["input", "output"]) {
      const result = await check(request, headers, stage, bad);
      // Preserve exact fail-closed contract and expose ONLY checker IDs/status,
      // error codes and bounded timings when an intermittent 500-rule run fails.
      // Never echo customer content, findings, or configuration.
      const checkerEvidence = result.checker_results.map(({checker_id, status, error_code, latency_ms}) =>
        ({checker_id, status, error_code, latency_ms}));
      expect(result.evaluation_status,
        JSON.stringify({stage, total_latency_ms: result.total_latency_ms,
          checkerEvidence})).toBe("complete");
      expect(result.effective_action).toBe("block");
      const findings = result.findings.filter(f => f.rule_id.startsWith("business.mixed_ui_"))
        .map(f => f.rule_id).sort();
      expect(findings).toEqual(["business.mixed_ui_000", lastId]);
      const benign = await check(request, headers, stage, good);
      expect(benign.evaluation_status).toBe("complete");
      expect(benign.effective_action).toBe("allow");
      expect(benign.findings.filter(f => f.rule_id.startsWith("business.mixed_ui_")))
        .toHaveLength(0);
    }
  } finally {
    if (resetRequired) {
      const cleanup = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(cleanup.status()).toBe(200);
    }
  }
});
