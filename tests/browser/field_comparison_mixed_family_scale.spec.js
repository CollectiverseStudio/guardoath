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
    // The editor must tell customers the saved Output rule is Off even when
    // offering synthetic examples to exercise the rest of their tenant policy.
    const identityEditorRow = page.locator('li[data-identity-id="mixed_ui_050"]');
    await expect(identityEditorRow).toContainText("Saved output rule action is Off.");
    await expect(identityEditorRow).not.toContainText("Saved input rule action is Off.");

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

    // Prove the customer's actual two-stage dashboard flow shows the filtered
    // findings, rather than merely trusting the tenant API result. Safe Input
    // allows a proposed Output; Output still blocks for the first rule.
    await page.locator("#input-format").selectOption("json");
    await page.locator("#input-content").fill(JSON.stringify(good));
    const browserInput = page.waitForResponse(r =>
      r.url().endsWith("/v1/checks/input") && r.request().method() === "POST");
    await page.locator("#run-input").click();
    const acceptedInput = await (await browserInput).json();
    expect(acceptedInput.effective_action).toBe("allow");
    await expect(page.locator("#run-output")).toBeEnabled();
    await page.locator("#output-example-check").selectOption("cross_field");
    const offSamples = page.locator("#output-example option")
      .filter({hasText: "business.mixed_ui_050"});
    await expect(offSamples).toHaveCount(2);
    expect((await offSamples.allTextContents()).every(x =>
      x.includes("(saved output rule Off; not evaluated)"))).toBe(true);
    await page.locator("#input-example-check").selectOption("cross_field");
    const inputSamples = page.locator("#input-example option")
      .filter({hasText: "business.mixed_ui_050"});
    await expect(inputSamples).toHaveCount(2);
    expect((await inputSamples.allTextContents()).every(x =>
      !x.includes("rule Off; not evaluated"))).toBe(true);
    await page.locator("#output-format").selectOption("json");
    await page.locator("#output-content").fill(JSON.stringify(bad));
    const browserOutput = page.waitForResponse(r =>
      r.url().endsWith("/v1/checks/output") && r.request().method() === "POST");
    await page.locator("#run-output").click();
    const blockedOutput = await (await browserOutput).json();
    expect(blockedOutput.effective_action).toBe("block");
    const displayIds = (await page.locator("#output-results .finding-rule-id").allTextContents())
      .filter(id => id.startsWith("business.mixed_ui_"));
    expect(displayIds).toEqual(["business.mixed_ui_000"]);
    await expect(page.locator("#answer")).toBeHidden();
    expect((await current(request, headers)).revision).toBe(outputOff.revision);

    // A staged check-wide Off is not the saved policy. The customer note
    // must identify the unsaved draft, then the real saved Off after Save.
    await page.locator("#tenant-check-input-cross_field").selectOption("");
    // A staged check-wide Off is NOT an authoritative saved Off. The ordinary
    // editor must not prematurely label the existing saved comparison disabled.
    const ordinaryFirst = page.locator('li[data-comparison-id="mixed_ui_000"]');
    await expect(ordinaryFirst).not.toContainText("Saved input Field comparisons check is Off.");
    // All advanced editors must also use the SAVED parent stage, not the
    // pending check selector. No premature "Off" while this edit is unsaved.
    const arithmeticFirst = page.locator('li[data-arithmetic-id="mixed_ui_001"]');
    const identityFirst = page.locator('li[data-identity-id="mixed_ui_050"]');
    const conversionFirst = page.locator('li[data-conversion-id="mixed_ui_003"]');
    for (const row of [arithmeticFirst, identityFirst, conversionFirst]) {
      await expect(row).not.toContainText("Saved input Field comparisons check is Off.");
    }
    await page.locator("#input-example-check").selectOption("general");
    await page.locator("#input-example-check").selectOption("cross_field");
    await expect(page.locator("#input-example-note")).toContainText("not saved yet");
    expect((await current(request, headers)).input_checks.cross_field).toBe("flag");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const checkWideOff = await current(request, headers);
    expect(checkWideOff.revision).toBe(outputOff.revision + 1);
    expect(checkWideOff.input_checks.cross_field || "").toBe("");
    expect(checkWideOff.output_rule_actions["business.mixed_ui_050"]).toBe("off");
    await connect(page);
    await page.locator("#input-example-check").selectOption("cross_field");
    await expect(page.locator("#input-example-note")).toContainText("off on this record");
    await expect(page.locator("#input-example-note")).not.toContainText("not saved yet");
    // After revisioned Save and reconnect, the customer sees why this
    // otherwise enabled ordinary rule cannot run on Input. Output's check
    // remains enabled and only the independently disabled identity rule is Off.
    const savedOrdinary = page.locator('li[data-comparison-id="mixed_ui_000"]');
    await expect(savedOrdinary).toContainText("Saved input Field comparisons check is Off.");
    await expect(savedOrdinary).toContainText("regardless of its own rule action");
    await expect(savedOrdinary).not.toContainText("Saved output Field comparisons check is Off.");
    // All four customer-editable comparison families share the same saved
    // cross_field check. The advanced editors must explain a parent Input Off,
    // independent of each rule's own Output action and its example buttons.
    for (const row of [arithmeticFirst, identityFirst, conversionFirst]) {
      await expect(row).toContainText("Saved input Field comparisons check is Off.");
      await expect(row).toContainText("regardless of its own rule action");
      await expect(row).not.toContainText("Saved output Field comparisons check is Off.");
    }
    const identityOff = page.locator('li[data-identity-id="mixed_ui_050"]');
    await expect(identityOff).toContainText("Saved output rule action is Off.");
    await expect(page.locator('li[data-identity-id="mixed_ui_002"]'))
      .not.toContainText("Saved output rule action is Off.");
    const finalSaved = await current(request, headers);
    expect(finalSaved.revision).toBe(checkWideOff.revision);
    expect(finalSaved.output_checks.cross_field).toBe("block");
    // The UI's two Off explanations must correspond to the REAL shared
    // Engine, not just strings rendered from tenant configuration. Disabling
    // the Input check suppresses every comparison on Input, while Output
    // continues evaluating the ordinary violation and ignores only its
    // explicitly Off advanced-identity rule.
    const inputAfterCheckOff = await check(request, headers, "input", bad);
    expect(inputAfterCheckOff.evaluation_status).toBe("complete");
    expect(inputAfterCheckOff.findings.filter(f =>
      f.rule_id.startsWith("business.mixed_ui_"))).toHaveLength(0);
    expect(inputAfterCheckOff.effective_action).toBe("allow");
    const outputAfterCheckOff = await check(request, headers, "output", bad);
    expect(outputAfterCheckOff.evaluation_status).toBe("complete");
    expect(outputAfterCheckOff.effective_action).toBe("block");
    expect(outputAfterCheckOff.findings.filter(f =>
      f.rule_id.startsWith("business.mixed_ui_")).map(f => f.rule_id))
      .toEqual(["business.mixed_ui_000"]);
    expect((await current(request, headers)).revision).toBe(checkWideOff.revision);
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
    // Customer can locate one ordinary rule inside a saved 500-rule,
    // four-family tenant without removing 124 other ordinary definitions.
    await expect(page.locator("#comparison-filter-status")).toContainText("Showing 125 of 125");
    await page.locator("#comparison-filter").fill("mixed_ui_000");
    await expect(page.locator('#comparison-drafts li[data-comparison-id]')).toHaveCount(1);
    await expect(page.locator('#comparison-drafts li[data-comparison-id="mixed_ui_000"]')).toBeVisible();
    await page.locator("#comparison-filter-stage").selectOption("input");
    await expect(page.locator("#comparison-filter-status")).toContainText("Showing 1 of 125");
    await page.locator("#comparison-filter").fill("/r004/");
    await expect(page.locator('#comparison-drafts li[data-comparison-id="mixed_ui_004"]')).toBeVisible();
    await expect(page.locator('#comparison-drafts li[data-comparison-id]')).toHaveCount(1);
    // Type and operator filtering search the pending customer-visible portfolio,
    // without changing any authoritative saved rule or revision.
    await page.locator("#comparison-filter").fill("NUMBER");
    await expect(page.locator("#comparison-filter-status")).toContainText("Showing 125 of 125");
    await page.locator("#comparison-filter").fill("le");
    await expect(page.locator("#comparison-filter-status")).toContainText("Showing 125 of 125");
    await page.locator("#comparison-filter").fill("no_such_rule");
    await expect(page.locator("#comparison-filter-status")).toContainText("Showing 0 of 125");
    await expect(page.locator("#comparison-drafts")).toContainText("No matching ordinary comparisons");
    await page.locator("#comparison-filter").fill("");
    await page.locator("#comparison-filter-stage").selectOption("all");
    await expect(page.locator('#comparison-drafts li[data-comparison-id]')).toHaveCount(125);
    // The revisioned Save path must retain existing ordinary actions and
    // unrelated advanced-family actions when a 500-rule draft is unchanged.
    const stageActions = await page.evaluate(() => comparisonEditor.actions("output", {
      "business.mixed_ui_000": "off", "business.mixed_ui_004": "flag",
      "business.mixed_ui_001": "block", "non_business_rule": "flag",
    }));
    expect(stageActions).toEqual({
      "business.mixed_ui_000": "off", "business.mixed_ui_004": "flag",
      "business.mixed_ui_001": "block", "non_business_rule": "flag",
    });
    // At a real 500-rule scale, all THREE advanced editors must also offer
    // customer-visible draft-only ID/pointer/stage filtering. Neither a filter
    // nor a no-match search can shrink the tenant's authoritative portfolio.
    for (const [family, id, pointer] of [
      ["arithmetic", "mixed_ui_001", "/r001/discount"],
      ["identity", "mixed_ui_002", "/r002/order_id"],
      ["conversion", "mixed_ui_003", "/r003/load"],
    ]) {
      const rows = page.locator('#' + family + '-drafts li[data-' + family + '-id]');
      const search = page.locator('#' + family + '-filter');
      const stage = page.locator('#' + family + '-filter-stage');
      const status = page.locator('#' + family + '-filter-status');
      await expect(status).toContainText("Showing 125 of 125");
      await search.fill(id);
      await expect(rows).toHaveCount(1);
      await expect(page.locator('li[data-' + family + '-id="' + id + '"]')).toBeVisible();
      await stage.selectOption("output");
      await expect(rows).toHaveCount(1);
      await search.fill(pointer);
      await expect(rows).toHaveCount(1);
      await search.fill("not_a_tenant_pointer");
      await expect(status).toContainText("Showing 0 of 125");
      await expect(page.locator('#' + family + '-drafts')).toContainText("No matching " + family + " rules");
      await search.fill("");
      await stage.selectOption("all");
      await expect(rows).toHaveCount(125);
    }
    expect((await current(request, headers)).revision).toBe(after.revision);
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
    // Regression: at a 500-rule FOUR-FAMILY scale, disabling just the
    // last conversion rule on Output must not disable its Input evaluation,
    // drop the other 499 definitions, or relax a different Output Block.
    // This is a real dashboard edit, authenticated revisioned Save and Engine,
    // not a synthetic policy or a second validation implementation.
    await page.locator("#rule-search").fill("mixed_ui_499");
    await page.locator('tr.rule-row[data-rule="' + lastId + '"] [data-stage="output"]')
      .selectOption("off");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const outputOff500 = await current(request, headers);
    expect(outputOff500.revision).toBe(persisted.revision + 1);
    expect(outputOff500.output_rule_actions[lastId]).toBe("off");
    expect(outputOff500.input_rule_actions[lastId]).toBe("block");
    expect(outputOff500.record_rules.filter(r => r.rule_id.startsWith("business.mixed_ui_")))
      .toHaveLength(size);
    for (const family of ["comparisons", "advanced_arithmetic", "advanced_identity",
                          "advanced_conversion_time"]) {
      expect(outputOff500[family], family).toHaveLength(125);
    }
    await connect(page);
    await page.locator("#rule-search").fill("mixed_ui_499");
    const restored = page.locator('tr.rule-row[data-rule="' + lastId + '"]');
    await expect(restored.locator('[data-stage="output"]')).toHaveValue("off");
    await expect(restored.locator(".effect-output")).toHaveText("Output: Off (own setting)");
    await expect(restored.locator(".effect-input")).toHaveText("Input: Block (own setting)");
    const conversionEditorRow = page.locator('li[data-conversion-id="mixed_ui_499"]');
    await expect(conversionEditorRow).toContainText("Saved output rule action is Off.");
    await expect(conversionEditorRow).not.toContainText("Saved input rule action is Off.");

    const inputAfterOff = await check(request, headers, "input", bad);
    expect(inputAfterOff.evaluation_status).toBe("complete");
    expect(inputAfterOff.effective_action).toBe("block");
    expect(inputAfterOff.findings.filter(f => f.rule_id.startsWith("business.mixed_ui_"))
      .map(f => f.rule_id).sort()).toEqual(["business.mixed_ui_000", lastId]);

    const outputAfterOff = await check(request, headers, "output", bad);
    expect(outputAfterOff.evaluation_status).toBe("complete");
    expect(outputAfterOff.effective_action).toBe("block");
    expect(outputAfterOff.findings.filter(f => f.rule_id.startsWith("business.mixed_ui_"))
      .map(f => f.rule_id)).toEqual(["business.mixed_ui_000"]);
    expect((await current(request, headers)).revision).toBe(outputOff500.revision);

    // The first rule is an ORDINARY comparison, not an advanced conversion.
    // Demonstrate the same saved Input/Output independence through its own
    // customer editor at a full 500-rule scale. Neither the existing advanced
    // Output-Off setting nor the other 498 definitions may be lost.
    await page.locator("#rule-search").fill("mixed_ui_000");
    await expect(page.locator('li[data-comparison-id="mixed_ui_000"]'))
      .not.toContainText("Saved output rule action is Off.");
    await page.locator('tr.rule-row[data-rule="business.mixed_ui_000"] [data-stage="output"]')
      .selectOption("off");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const ordinaryOff = await current(request, headers);
    expect(ordinaryOff.revision).toBe(outputOff500.revision + 1);
    expect(ordinaryOff.output_rule_actions["business.mixed_ui_000"]).toBe("off");
    expect(ordinaryOff.output_rule_actions[lastId]).toBe("off");
    expect(ordinaryOff.input_rule_actions[lastId]).toBe("block");
    for (const family of ["comparisons", "advanced_arithmetic", "advanced_identity",
                          "advanced_conversion_time"]) {
      expect(ordinaryOff[family]).toHaveLength(125);
    }
    await connect(page);
    await page.locator("#rule-search").fill("mixed_ui_000");
    const ordinaryEditorRow = page.locator('li[data-comparison-id="mixed_ui_000"]');
    await expect(ordinaryEditorRow).toContainText("Saved output rule action is Off.");
    await expect(ordinaryEditorRow).not.toContainText("Saved input rule action is Off.");
    await expect(page.locator('tr.rule-row[data-rule="business.mixed_ui_000"] [data-stage="input"]'))
      .not.toHaveValue("off");

    // Even if both violating Output rules are Off, the two Input findings
    // remain exact and independently enforced. No checker is globally disabled.
    const inputStillChecked = await check(request, headers, "input", bad);
    expect(inputStillChecked.evaluation_status).toBe("complete");
    expect(inputStillChecked.effective_action).toBe("block");
    expect(inputStillChecked.findings.filter(f => f.rule_id.startsWith("business.mixed_ui_"))
      .map(f => f.rule_id).sort()).toEqual(["business.mixed_ui_000", lastId]);
    const outputWithBothOff = await check(request, headers, "output", bad);
    expect(outputWithBothOff.evaluation_status).toBe("complete");
    expect(outputWithBothOff.effective_action).toBe("allow");
    expect(outputWithBothOff.findings.filter(f => f.rule_id.startsWith("business.mixed_ui_")))
      .toHaveLength(0);

    // A pending ordinary pointer edit must not present an old saved Off
    // setting as the state of the changed, unsaved rule.
    await ordinaryEditorRow.getByRole("button", {name: "Edit comparison mixed_ui_000"}).click();
    await page.locator("#comparison-left").fill("/r000/new_total");
    // Stage filter must follow the pending definition without implying a
    // saved tenant change. Output is removed only from this unsaved draft.
    await page.locator("#comparison-stage-output").uncheck();
    await page.locator("#comparison-apply").click();
    await page.locator("#comparison-filter").fill("mixed_ui_000");
    await page.locator("#comparison-filter-stage").selectOption("output");
    await expect(page.locator('#comparison-drafts li[data-comparison-id="mixed_ui_000"]')).toHaveCount(0);
    await page.locator("#comparison-filter-stage").selectOption("input");
    await expect(page.locator('#comparison-drafts li[data-comparison-id="mixed_ui_000"]')).toBeVisible();
    await expect(ordinaryEditorRow).toContainText("Unsaved comparison changes:");
    await expect(ordinaryEditorRow).not.toContainText("Saved output rule action is Off.");
    // Pending Output stage removal must delete only this original ordinary
    // override. Other ordinary/advanced actions survive the linear lookup.
    const pendingOutputActions = await page.evaluate(() => comparisonEditor.actions("output", {
      "business.mixed_ui_000": "off", "business.mixed_ui_004": "flag",
      "business.mixed_ui_499": "off", "non_business_rule": "block",
    }));
    expect(pendingOutputActions).toEqual({
      "business.mixed_ui_004": "flag",
      "business.mixed_ui_499": "off", "non_business_rule": "block",
    });
    expect((await current(request, headers)).revision).toBe(ordinaryOff.revision);
    // A pending advanced conversion stage edit changes the searchable draft,
    // not the persisted Output-Off action or the Engine's 500-rule policy.
    await page.locator("#conversion-filter").fill("mixed_ui_499");
    await page.locator("#conversion-filter-stage").selectOption("output");
    await expect(page.locator('li[data-conversion-id="mixed_ui_499"]')).toBeVisible();
    await page.getByRole("button", {name: "Edit conversion mixed_ui_499"}).click();
    await page.locator("#conversion-stage-output").uncheck();
    await page.locator("#conversion-apply").click();
    await expect(page.locator('#conversion-drafts li[data-conversion-id="mixed_ui_499"]')).toHaveCount(0);
    await page.locator("#conversion-filter-stage").selectOption("input");
    await expect(page.locator('li[data-conversion-id="mixed_ui_499"]')).toBeVisible();
    expect((await current(request, headers)).revision).toBe(ordinaryOff.revision);
    await connect(page); // reconnect discards both unsaved drafts, not tenant settings
    await expect(page.locator('li[data-comparison-id="mixed_ui_000"]'))
      .toContainText("Saved output rule action is Off.");
    expect((await current(request, headers)).revision).toBe(ordinaryOff.revision);
  } finally {
    if (resetRequired) {
      const cleanup = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(cleanup.status()).toBe(200);
    }
  }
});


test("FC tenant refuses malformed checker receipts before output", async ({page, request}) => {
  const headers = await connect(page);
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    resetRequired = true;
    const before = await current(request, headers);
    const {changes, good} = configured(4); // one of each real comparison family
    const saved = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: before.revision, changes,
    }});
    expect(saved.status(), (await saved.text()).slice(0, 120)).toBe(200);
    await connect(page);
    const persisted = await current(request, headers);
    expect(persisted.revision).toBe(before.revision + 1);
    for (const family of ["comparisons", "advanced_arithmetic", "advanced_identity",
                          "advanced_conversion_time"]) {
      expect(persisted[family]).toHaveLength(1);
    }
    // The customer actually runs all four families through the shared Engine,
    // rather than using a mocked API as proof of a clean Input decision.
    await page.locator("#input-format").selectOption("json");
    await page.locator("#input-content").fill(JSON.stringify(good));
    const inputResponse = page.waitForResponse(r =>
      r.url().endsWith("/v1/checks/input") && r.request().method() === "POST");
    await page.locator("#run-input").click();
    const receipt = await (await inputResponse).json();
    expect(receipt.evaluation_status).toBe("complete");
    expect(receipt.effective_action).toBe("allow");
    expect(receipt.checker_results.length).toBeGreaterThan(0);
    await expect(page.locator("#run-output")).toBeEnabled();

    // Corrupt only the browser's received envelope to exercise transport
    // fail-closed behavior; tenant checks and the actual Engine stay enabled.
    const first = receipt.checker_results[0];
    for (const corrupted of [
      {...receipt, checker_results: [{...first, status: "unverified"}]},
      {...receipt, checker_results: [{...first, checker_id: ""}]},
      {...receipt, checker_results: [null]},
      {...receipt, checker_results: [first, {...first}]}, // duplicated success hides a missing checker
      {...receipt, evaluation_status: "unverified"},
    ]) {
      const decision = await page.evaluate(received => {
        inputResult = received;
        buttons();
        return {
          permitted: canProceed(inputResult),
          receiptValid: receiptMatchesRequest(received, received.stage,
            received.application_id, received.policy_version),
        };
      }, corrupted);
      expect(decision).toEqual({permitted: false, receiptValid: false});
      await expect(page.locator("#run-output")).toBeDisabled();
    }
    const validAgain = await page.evaluate(received => {
      inputResult = received;
      buttons();
      return {
        permitted: canProceed(inputResult),
        receiptValid: receiptMatchesRequest(received, received.stage,
          received.application_id, received.policy_version),
      };
    }, receipt);
    expect(validAgain).toEqual({permitted: true, receiptValid: true});
    await expect(page.locator("#run-output")).toBeEnabled();
    expect((await current(request, headers)).revision).toBe(persisted.revision);
  } finally {
    if (resetRequired) {
      const cleanup = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(cleanup.status()).toBe(200);
    }
  }
});
