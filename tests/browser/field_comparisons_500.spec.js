// Real authenticated 30/51/100/250/500 tenant AND browser acceptance.
// No mocked application or synthetic GET substitutions: definitions persist in
// the authoritative tenant, render in the dashboard, and run in the shared Engine.
// The browser does not create a separate policy/engine or trust client claims.
const { test, expect } = require("@playwright/test");

function comparisonRules(size) {
  return Array.from({length: size}, (_, i) => {
    const n = String(i + 1).padStart(3, "0");
    return {rule_id: "ui_scale_" + n, left: "/left_" + n,
            right: "/right_" + n, operator: "le", value_type: "number",
            stages: ["input", "output"]};
  });
}

function numericPayload(size, violating) {
  const data = {};
  for (let i = 1; i <= size; i++) {
    const n = String(i).padStart(3, "0");
    data["left_" + n] = 1;
    data["right_" + n] = 2;
  }
  if (violating) {
    data.left_001 = 9;
    data["left_" + String(size).padStart(3, "0")] = 9;
  }
  return JSON.stringify(data);
}

async function connect(page) {
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}

async function current(request, headers) {
  const response = await request.get("/v1/applications/current", {headers});
  expect(response.ok()).toBeTruthy();
  return (await response.json()).tenant;
}

for (const size of [30, 51, 100, 250, 500]) {
  test("real tenant and dashboard preserve " + size + " comparisons and both-stage findings", async ({page, request}) => {
    const headers = await connect(page);
    let resetRequired = false;
    try {
      // Explicitly clean the shared demo tenant before changing its authoritative
      // record; the next test does not inherit persisted drafts or revisions.
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.ok(), "Demo reset rejected (HTTP " + reset.status() + "): " + (await reset.text()).slice(0,200)).toBeTruthy();
      resetRequired = true;
      const before = await current(request, headers);
      const rules = comparisonRules(size);
      const saved = await request.post("/v1/tenants/current/checks", {headers, data: {
        revision: before.revision,
        changes: {comparisons: rules,
                  input_check_cross_field: "flag",
                  output_check_cross_field: "block"},
      }});
      expect(saved.status()).toBe(200);
      const after = await current(request, headers);
      expect(after.revision).toBe(before.revision + 1);
      expect(after.comparisons).toEqual(rules);

      // Reconnect so the full 30-500 rule table and editor actually load from
      // the server, rather than a fabricated browser response.
      await connect(page);
      const large = page.locator('#tenant-group-cross_field .rule-row[data-rule^="business.ui_scale_"]');
      await expect(large).toHaveCount(size);
      await expect(page.locator("#comparison-draft-summary")).toContainText(
        size + " comparisons configured; " + size + " Input, " + size + " Output.");
      await expect(page.locator("#comparison-draft-summary")).toContainText("No pending changes.");

      const firstId = "business.ui_scale_001";
      const lastId = "business.ui_scale_" + String(size).padStart(3, "0");
      await page.locator("#rule-search").fill("ui_scale_" + String(size).padStart(3, "0"));
      const last = page.locator('tr.rule-row[data-rule="' + lastId + '"]');
      await expect(last).toBeVisible();
      await expect(page.locator('tr.rule-row[data-rule="' + firstId + '"]')).toBeHidden();
      await expect(last.locator(".effect-input")).toHaveText("Input: Flag (from check)");
      await expect(last.locator(".effect-output")).toHaveText("Output: Block (from check)");

      // One *real* dashboard edit, revisioned Save, and reload, not merely
      // direct API provisioning. Every other rule must retain inherited action.
      await last.locator('[data-stage="input"]').selectOption("block");
      await page.getByRole("button", {name: "Save checks", exact: true}).click();
      await expect(page.locator("#tenant-message")).toContainText("Saved.");
      const persisted = await current(request, headers);
      expect(persisted.comparisons).toEqual(rules);
      expect(persisted.input_rule_actions[lastId]).toBe("block");
      await connect(page);
      await page.locator("#rule-search").fill("ui_scale_" + String(size).padStart(3, "0"));
      await expect(page.locator('tr.rule-row[data-rule="' + lastId + '"] .effect-input'))
        .toHaveText("Input: Block (own setting)");
      await page.locator("#rule-search").fill("ui_scale_001");
      await expect(page.locator('tr.rule-row[data-rule="' + firstId + '"] .effect-input'))
        .toHaveText("Input: Flag (from check)");

      for (const stage of ["input", "output"]) {
        for (const [violating, expected] of [[true, [firstId, lastId]], [false, []]]) {
          const response = await request.post("/v1/checks/" + stage, {headers, data: {
            application_id: "demo2", content_format: "json",
            content: numericPayload(size, violating)
          }});
          expect(response.status()).toBe(200);
          const result = await response.json();
          expect(result.evaluation_status).toBe("complete");
          const matches = result.findings.filter(f => f.rule_id.startsWith("business.ui_scale_"))
            .map(f => f.rule_id).sort();
          expect(matches).toEqual(expected.slice().sort());
          expect(result.effective_action).toBe(violating ?
            (stage === "input" ? "block" : "block") : "allow");
        }
      }
    } finally {
      // Keep other browser suites' shared memory tenant clean even if an
      // assertion fails. Use the independent API request fixture, not a page
      // navigation in teardown that can hide the original browser failure.
      if (resetRequired) {
        const cleanup = await request.post("/v1/tenants/current/reset", {headers, data: {}});
        expect(cleanup.ok(), "Demo cleanup rejected (HTTP " + cleanup.status() + "): " + (await cleanup.text()).slice(0,200)).toBeTruthy();
      }
    }
  });
}


test("customer can import 500 JSON comparison definitions, save and execute them without a mocked tenant", async ({page, request}) => {
  const headers = await connect(page);
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status(), (await reset.text()).slice(0,200)).toBe(200);
    resetRequired = true;
    await connect(page);
    await page.locator("#comparison-bulk-panel summary").click();
    const jsonBox = page.locator("#comparison-bulk-json");
    const importButton = page.locator("#comparison-bulk-import");
    const confirmation = page.locator("#comparison-bulk-confirm");
    const summary = page.locator("#comparison-draft-summary");
    const before = await current(request, headers);
    const rules = comparisonRules(500);
    await jsonBox.fill(JSON.stringify(rules));
    // No implicit wholesale replacement: user confirmation is mandatory.
    await importButton.click();
    await expect(page.locator("#comparison-bulk-status")).toContainText("Confirm replacement");
    await expect(summary).toContainText(before.comparisons.length + " comparisons configured");
    await confirmation.check();
    await jsonBox.fill(JSON.stringify([rules[0], rules[0]]));
    await importButton.click();
    await expect(page.locator("#comparison-bulk-status")).toContainText("duplicate");
    await expect(summary).toContainText(before.comparisons.length + " comparisons configured");
    await jsonBox.fill(JSON.stringify([{...rules[0], stages: ["input", "other"]}]));
    await importButton.click();
    await expect(page.locator("#comparison-bulk-status")).toContainText("stages must include");
    await expect(summary).toContainText(before.comparisons.length + " comparisons configured");
    // UI and authoritative API agree: repeating a valid stage cannot produce
    // a misleading staged portfolio or bypass the tenant stage contract.
    for (const repeated of [["input", "input"], ["output", "output"]]) {
      await jsonBox.fill(JSON.stringify([{...rules[0], stages: repeated}]));
      await importButton.click();
      await expect(page.locator("#comparison-bulk-status")).toContainText("without duplicates");
      await expect(summary).toContainText(before.comparisons.length + " comparisons configured");
    }
    await jsonBox.fill(JSON.stringify(rules));
    await importButton.click();
    await expect(page.locator("#comparison-bulk-status")).toContainText(
      "500 comparison definitions staged");
    await expect(summary).toContainText("500 comparisons configured; 500 Input, 500 Output.");
    await expect(summary).toContainText("Unsaved changes");
    expect((await current(request, headers)).comparisons).toEqual(before.comparisons);

    await page.locator("#tenant-check-input-cross_field").selectOption("flag");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const after = await current(request, headers);
    expect(after.comparisons).toEqual(rules);
    expect(after.revision).toBe(before.revision + 1);
    await connect(page);
    await expect(page.locator("#comparison-draft-summary")).toContainText(
      "500 comparisons configured; 500 Input, 500 Output.");
    await expect(page.locator("#comparison-draft-summary")).toContainText("No pending changes.");

    for (const stage of ["input", "output"]) {
      const response = await request.post("/v1/checks/" + stage, {headers, data: {
        application_id: "demo2", content_format: "json",
        content: numericPayload(500, true),
      }});
      expect(response.status(), (await response.text()).slice(0,200)).toBe(200);
      const evaluated = await response.json();
      expect(evaluated.evaluation_status).toBe("complete");
      const matches = evaluated.findings.filter(f => f.rule_id.startsWith("business.ui_scale_"))
        .map(f => f.rule_id).sort();
      expect(matches).toEqual(["business.ui_scale_001", "business.ui_scale_500"]);
      expect(evaluated.effective_action).toBe(stage === "input" ? "flag" : "block");
      const benign = await request.post("/v1/checks/" + stage, {headers, data: {
        application_id: "demo2", content_format: "json",
        content: numericPayload(500, false),
      }});
      expect(benign.status(), (await benign.text()).slice(0,200)).toBe(200);
      const benignResult = await benign.json();
      expect(benignResult.evaluation_status).toBe("complete");
      expect(benignResult.findings.filter(f => f.rule_id.startsWith("business.ui_scale_")))
        .toEqual([]);
      expect(benignResult.effective_action).toBe("allow");
    }
  } finally {
    if (resetRequired) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status(), (await reset.text()).slice(0,200)).toBe(200);
    }
  }
});
