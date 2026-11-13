// Real customer workflow spanning ordinary FC-001–030 and advanced FC-031–050.
// One authenticated tenant, one revisioned Save and one shared Input/Output Engine.
// No mocked endpoints, hidden policy switch, reference authority or synthetic results.
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

async function tenant(request, headers) {
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

const ids = ["business.ui_load", "business.ui_spend", "business.ui_subtotal"];
const violating = {
  order: {
    spend: 120, limit: 100, subtotal: 100,
    lines: [{quantity: 2, unit_price: 40}, {quantity: 1, unit_price: 30}]
  },
  shipment: {load: 1500, load_unit: "kg", max_kg: 1000}
};
const benign = {
  order: {
    spend: 80, limit: 100, subtotal: 110,
    lines: [{quantity: 2, unit_price: 40}, {quantity: 1, unit_price: 30}]
  },
  shipment: {load: 900, load_unit: "kg", max_kg: 1000}
};
function ownFindings(result) {
  return result.findings.map(item => item.rule_id).filter(id => ids.includes(id)).sort();
}

test("customer saves and executes ordinary, arithmetic and conversion rules together with per-rule stage authority", async ({page, request}) => {
  const headers = await connect(page);
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    resetRequired = true;
    const baseline = await tenant(request, headers);
    // A demo reset may carry ordinary baseline comparisons. Isolate the three
    // families through the same revision-controlled tenant API rather than
    // suppressing genuine extra findings or assuming an empty fixture.
    const cleared = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: baseline.revision,
      changes: {comparisons: [], advanced_arithmetic: [], advanced_conversion_time: []}
    }});
    expect(cleared.status(), (await cleared.text()).slice(0, 200)).toBe(200);
    await connect(page);
    await expect(page.locator("#comparison-form")).toBeVisible();
    await expect(page.locator("#arithmetic-form")).toBeVisible();
    await expect(page.locator("#conversion-form")).toBeVisible();
    const isolated = await tenant(request, headers);
    expect(isolated.comparisons).toEqual([]);
    expect(isolated.advanced_arithmetic).toEqual([]);
    expect(isolated.advanced_conversion_time).toEqual([]);

    await page.locator("#comparison-id").fill("ui_spend");
    await page.locator("#comparison-left").fill("/order/spend");
    await page.locator("#comparison-right").fill("/order/limit");
    await page.locator("#comparison-apply").click();
    await expect(page.locator('[data-comparison-id="ui_spend"]')).toHaveCount(1);

    await page.locator("#arithmetic-id").fill("ui_subtotal");
    await page.locator("#arithmetic-kind").selectOption("invoice_line_subtotal");
    await page.locator("#arithmetic-field-lines").fill("/order/lines");
    await page.locator("#arithmetic-field-subtotal").fill("/order/subtotal");
    await page.locator("#arithmetic-apply").click();
    await expect(page.locator('[data-arithmetic-id="ui_subtotal"]')).toHaveCount(1);

    await page.locator("#conversion-id").fill("ui_load");
    await page.locator("#conversion-kind").selectOption("unit_load_kg_cap");
    await page.locator("#conversion-field-load").fill("/shipment/load");
    await page.locator("#conversion-field-load_unit").fill("/shipment/load_unit");
    await page.locator("#conversion-field-max_kg").fill("/shipment/max_kg");
    await page.locator("#conversion-apply").click();
    await expect(page.locator('[data-conversion-id="ui_load"]')).toHaveCount(1);
    expect((await tenant(request, headers)).revision).toBe(isolated.revision);

    await page.locator("#tenant-check-input-cross_field").selectOption("flag");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await tenant(request, headers);
    expect(saved.revision).toBe(isolated.revision + 1);
    expect(saved.comparisons.map(item => item.rule_id)).toEqual(["ui_spend"]);
    expect(saved.advanced_arithmetic.map(item => item.rule_id)).toEqual(["ui_subtotal"]);
    expect(saved.advanced_conversion_time.map(item => item.rule_id)).toEqual(["ui_load"]);
    expect(ids.every(id => saved.record_rules.some(row => row.rule_id === id))).toBe(true);

    // Per-rule Allow is deliberately not Off: the ordinary finding must remain
    // visible, but the two other detections still Flag Input independently.
    await page.locator("#rule-search").fill("ui_spend");
    const row = page.locator('tr.rule-row[data-rule="business.ui_spend"]');
    await expect(row).toBeVisible();
    await row.locator('[data-stage="input"]').selectOption("allow");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const perRule = await tenant(request, headers);
    expect(perRule.revision).toBe(saved.revision + 1);
    expect(perRule.input_rule_actions["business.ui_spend"]).toBe("allow");
    await connect(page);
    for (const id of ids) {
      await page.locator("#rule-search").fill(id);
      await expect(page.locator('tr.rule-row[data-rule="' + id + '"]')).toBeVisible();
    }
    await page.locator("#rule-search").fill("ui_spend");
    await expect(page.locator('tr.rule-row[data-rule="business.ui_spend"] .effect-input'))
      .toHaveText("Input: Allow (own setting)");
    await page.locator("#rule-search").fill("");

    await page.locator("#input-format").selectOption("json");
    await page.locator("#input-content").fill(JSON.stringify(violating));
    const inputResponse = page.waitForResponse(r =>
      r.url().endsWith("/v1/checks/input") && r.request().method() === "POST");
    await page.locator("#run-input").click();
    const input = await (await inputResponse).json();
    expect(input.evaluation_status).toBe("complete");
    expect(input.effective_action).toBe("flag");
    expect(ownFindings(input)).toEqual(ids);
    // Actual dashboard must identify all three independently enforced rule IDs,
    // not just name the shared cross_field checker. No mock API/UI responses.
    await expect(page.locator("#input-results .finding-rule-id").filter({hasText: /^business\.ui_(load|spend|subtotal)$/})).toHaveCount(ids.length);
    const inputShownIds = (await page.locator("#input-results .finding-rule-id").allTextContents())
      .filter(value => ids.includes(value)).sort();
    expect(inputShownIds).toEqual(ids.slice().sort());
    for (const id of ids) {
      await expect(page.locator('#input-results button[aria-label="Review input rule settings for ' + id + '"]'))
        .toHaveCount(1);
    }

    await page.locator("#output-format").selectOption("json");
    await page.locator("#output-content").fill(JSON.stringify(violating));
    const outputResponse = page.waitForResponse(r =>
      r.url().endsWith("/v1/checks/output") && r.request().method() === "POST");
    await page.locator("#run-output").click();
    const output = await (await outputResponse).json();
    expect(output.evaluation_status).toBe("complete");
    expect(output.effective_action).toBe("block");
    expect(ownFindings(output)).toEqual(ids);
    await expect(page.locator("#output-results .finding-rule-id").filter({hasText: /^business\.ui_(load|spend|subtotal)$/})).toHaveCount(ids.length);
    const outputShownIds = (await page.locator("#output-results .finding-rule-id").allTextContents())
      .filter(value => ids.includes(value)).sort();
    expect(outputShownIds).toEqual(ids.slice().sort());
    for (const id of ids) {
      await expect(page.locator('#output-results button[aria-label="Review output rule settings for ' + id + '"]'))
        .toHaveCount(1);
    }
    await expect(page.locator("#answer")).toBeHidden();
    // The same cross_field checker emits all three rule families. Customers
    // must see the saved tenant family beside the stable finding ID on BOTH
    // stages, without exposing JSON pointers or reference values. This is
    // real dashboard -> tenant Save -> Engine evidence, not a mocked response.
    for (const stage of ["input", "output"]) {
      for (const [id, family] of [
        ["ui_spend", "Ordinary field comparison"],
        ["ui_subtotal", "Arithmetic field comparison"],
        ["ui_load", "Conversion/time field comparison"]
      ]) {
        const finding = page.locator("#" + stage + "-results .finding").filter({
          has: page.locator(".finding-rule-id", {
            hasText: new RegExp("^business\\." + id + "$")
          })
        });
        await expect(finding).toHaveCount(1);
        await expect(finding.locator(".finding-rule-name")).toHaveText(family + " · ");
        await expect(finding.locator(".finding-rule-id")).toHaveText("business." + id);
        expect(await finding.locator(".finding-rule-name").textContent()).not.toContain("/order/");
      }
    }

    // Findings must link to the correct EXISTING customer editor, not just
    // label the shared checker. Navigation clears display-only filters and
    // loads the saved rule into the pending form WITHOUT updating the tenant,
    // changing Input/Output actions or saving any definition.
    for (const [id, editor, field] of [
      ["ui_spend", "comparison", "comparisons"],
      ["ui_subtotal", "arithmetic", "advanced_arithmetic"],
      ["ui_load", "conversion", "advanced_conversion_time"]
    ]) {
      await page.locator("#" + editor + "-filter").fill("deliberately_no_matching_id");
      await page.locator("#" + editor + "-filter-stage").selectOption("input");
      const finding = page.locator("#output-results .finding").filter({
        has: page.locator(".finding-rule-id", {hasText: "business." + id})
      });
      await finding.getByRole("button", {
        name: "Edit saved definition for business." + id, exact: true
      }).click();
      await expect(page.locator("#comparison-configuration")).toHaveAttribute("open", "");
      await expect(page.locator("#" + editor + "-filter")).toHaveValue("");
      await expect(page.locator("#" + editor + "-filter-stage")).toHaveValue("all");
      await expect(page.locator("#" + editor + "-id")).toHaveValue(id);
      await expect(page.locator("#" + editor + "-id")).toBeDisabled();
      await expect(page.locator("#tenant-message")).toContainText("No settings were saved");
      await expect(page.locator("#" + editor + "-form")
        .getByRole("button", {name: "Cancel edit", exact: true})).toBeVisible();
      await page.locator("#" + editor + "-form")
        .getByRole("button", {name: "Cancel edit", exact: true}).click();
      const unchanged = await tenant(request, headers);
      expect(unchanged.revision).toBe(perRule.revision);
      expect(unchanged[field].some(rule => rule.rule_id === id)).toBe(true);
      expect(unchanged.input_rule_actions["business.ui_spend"]).toBe("allow");
    }

    for (const stage of ["input", "output"]) {
      const good = await evaluate(request, headers, stage, benign);
      expect(good.evaluation_status).toBe("complete");
      expect(ownFindings(good)).toEqual([]);
      const bad = await evaluate(request, headers, stage, violating);
      expect(bad.evaluation_status).toBe("complete");
      expect(ownFindings(bad)).toEqual(ids);
      expect(bad.effective_action).toBe(stage === "input" ? "flag" : "block");
    }
    expect((await tenant(request, headers)).revision).toBe(perRule.revision);
  } finally {
    if (resetRequired) {
      const cleanup = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(cleanup.status(), (await cleanup.text()).slice(0, 200)).toBe(200);
    }
  }
});
