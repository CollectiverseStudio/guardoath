const { test, expect } = require("@playwright/test");

test("tenant can create, reload, edit stages and remove a comparison from the dashboard", async ({ page }) => {
  async function connect() {
    await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
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


test("comparison IDs enforce native HTML v-pattern validation before a draft can be saved", async ({page}) => {
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name:"Get demo API key", exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  await page.locator("#comparison-id").fill("invalid space");
  await page.locator("#comparison-left").fill("/left");
  await page.locator("#comparison-right").fill("/right");
  expect(await page.locator("#comparison-id").evaluate(e => e.validity.patternMismatch)).toBe(true);
  await page.locator("#comparison-apply").click();
  await expect(page.locator('#comparison-drafts [data-comparison-id="invalid space"]')).toHaveCount(0);
  // Existing test previously treated a hyphen/dot ID as valid even though
  // the authoritative tenant record rejects it. Preserve native validation
  // and require full parity with the server's lowercase rule namespace.
  await page.locator("#comparison-id").fill("valid_rule-1.x");
  expect(await page.locator("#comparison-id").evaluate(e => e.validity.patternMismatch)).toBe(true);
  await page.locator("#comparison-id").fill("valid_rule_1");
  expect(await page.locator("#comparison-id").evaluate(e => e.validity.patternMismatch)).toBe(false);
});


test("guided refund example stays a draft until authenticated Save and blocks in shared Engine", async ({page, request}) => {
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  const headers = {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
  const current = async () => {
    const response = await request.get("/v1/applications/current", {headers});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  };
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
    await page.locator("#demo-user").selectOption("demo2");
    await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo2");

    const baseline = await current();
    await page.locator("#comparison-sample").click();
    await expect(page.locator("#comparison-id")).toHaveValue("refund_not_over_paid");
    await expect(page.locator("#comparison-left")).toHaveValue("/invoice/refund");
    await expect(page.locator("#comparison-right")).toHaveValue("/invoice/paid");
    await expect(page.locator("#comparison-message")).toContainText("not added or saved");
    expect((await current()).comparisons).toEqual(baseline.comparisons);
    expect((await current()).revision).toBe(baseline.revision);

    await page.locator("#comparison-apply").click();
    await expect(page.locator("#comparison-draft-summary")).toContainText("1 added");
    expect((await current()).comparisons).toEqual(baseline.comparisons);
    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await current();
    expect(saved.comparisons.find(x => x.rule_id === "refund_not_over_paid")).toEqual({
      rule_id: "refund_not_over_paid", left: "/invoice/refund", right: "/invoice/paid",
      operator: "le", value_type: "number", stages: ["input", "output"]
    });

    for (const stage of ["input", "output"]) {
      for (const [refund, shouldBlock] of [[75, true], [25, false]]) {
        const response = await request.post("/v1/checks/" + stage, {headers, data: {
          application_id: "demo2", content_format: "json",
          content: JSON.stringify({invoice: {refund, paid: 50}})
        }});
        expect(response.status()).toBe(200);
        const data = await response.json();
        expect(data.evaluation_status).toBe("complete");
        const found = data.findings.map(x => x.rule_id).includes("business.refund_not_over_paid");
        expect(found).toBe(shouldBlock);
        if (shouldBlock) expect(data.effective_action).toBe("block");
      }
    }
    // A second click must not replace or alter a saved definition.
    await page.locator("#comparison-sample").click();
    await expect(page.locator("#comparison-message")).toContainText("already exists");
    expect((await current()).comparisons).toEqual(saved.comparisons);
  } finally {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
  }
});



test("ordinary bulk guided number/date definitions require stage and Save before real both-stage enforcement", async ({page, request}) => {
  async function connect() {
    await page.goto("/dashboard");
    await page.locator("#comparison-configuration").evaluate(node => { node.open = true; });
    await page.locator("#demo-user").selectOption("demo2");
    await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  }
  await connect();
  const headers = {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
  async function current() {
    const response = await request.get("/v1/applications/current", {headers});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  }
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    await connect();
    const baseline = await current();
    await page.locator("#comparison-bulk-panel summary").click();
    await page.locator("#comparison-bulk-example").click();
    const jsonBox = page.locator("#comparison-bulk-json");
    const examples = JSON.parse(await jsonBox.inputValue());
    expect(examples).toEqual([
      {rule_id: "refund_not_over_paid", left: "/invoice/refund", right: "/invoice/paid",
       operator: "le", value_type: "number", stages: ["input", "output"]},
      {rule_id: "delivery_not_before_order", left: "/shipment/delivery_date",
       right: "/shipment/order_date", operator: "ge", value_type: "date",
       stages: ["input", "output"]}
    ]);
    await expect(page.locator("#comparison-bulk-confirm")).not.toBeChecked();
    await expect(page.locator("#comparison-bulk-status")).toContainText("not staged or saved");
    expect((await current()).comparisons).toEqual(baseline.comparisons);
    expect((await current()).revision).toBe(baseline.revision);

    await page.locator("#comparison-bulk-import").click();
    await expect(page.locator("#comparison-bulk-status")).toContainText("Confirm replacement");
    await expect(page.locator("#comparison-draft-summary")).toContainText("No pending changes");
    await page.locator("#comparison-bulk-confirm").check();
    await page.locator("#comparison-bulk-import").click();
    await expect(page.locator("#comparison-bulk-status")).toContainText("2 comparison definitions staged");
    await expect(page.locator("#comparison-draft-summary")).toContainText("2 added");
    expect((await current()).comparisons).toEqual(baseline.comparisons);

    // Bad JSON must never replace a correctly staged draft or persist changes.
    await jsonBox.fill("{");
    await page.locator("#comparison-bulk-confirm").check();
    await page.locator("#comparison-bulk-import").click();
    await expect(page.locator("#comparison-bulk-status")).toContainText("Invalid JSON");
    await expect(page.locator("#comparison-draft-summary")).toContainText("2 added");
    expect((await current()).revision).toBe(baseline.revision);

    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await current();
    expect(saved.revision).toBeGreaterThan(baseline.revision);
    for (const expected of examples) {
      expect(saved.comparisons.find(rule => rule.rule_id === expected.rule_id)).toEqual(expected);
    }
    const targets = ["business.refund_not_over_paid", "business.delivery_not_before_order"];
    for (const stage of ["input", "output"]) {
      for (const violating of [true, false]) {
        const content = JSON.stringify({
          invoice: {refund: violating ? 75 : 25, paid: 50},
          shipment: {delivery_date: violating ? "2026-11-01" : "2026-11-03",
                     order_date: "2026-11-02"}
        });
        const response = await request.post("/v1/checks/" + stage, {headers, data: {
          application_id: "demo2", content_format: "json", content
        }});
        expect(response.status()).toBe(200);
        const outcome = await response.json();
        expect(outcome.evaluation_status).toBe("complete");
        const findings = outcome.findings.map(f => f.rule_id);
        for (const rule of targets) {
          expect(findings.includes(rule), stage + " " + rule).toBe(violating);
        }
        if (violating) expect(outcome.effective_action).toBe("block");
        // A benign payload may still trigger an unrelated enabled tenant check.
      }
    }
    // Save checks re-renders the tenant editor and closes optional bulk details.
    // Reopen it as the customer must, then verify fill-only stays nonmutating.
    await page.locator("#comparison-bulk-panel summary").click();
    await page.locator("#comparison-bulk-example").click();
    await expect(page.locator("#comparison-bulk-confirm")).not.toBeChecked();
    expect((await current()).comparisons).toEqual(saved.comparisons);
  } finally {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
  }
});


test("single-family 500 ordinary comparison rules stage atomically, save, filter and enforce both stages", async ({page, request}) => {
  // Actual authenticated dashboard/API/Engine journey: this is NOT an API-only
  // or mocked component-scale claim. No extra rule-count or timeout allowance.
  async function connect() {
    await page.goto("/dashboard");
    await page.locator("#comparison-configuration").evaluate(node => { node.open = true; });
    await page.locator("#demo-user").selectOption("demo2");
    await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  }
  await connect();
  const headers = {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
  async function current() {
    const response = await request.get("/v1/applications/current", {headers});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  }
  const rules = Array.from({length: 500}, (_, i) => {
    const number = String(i).padStart(3, "0");
    return {rule_id: "ordinary_bulk_" + number, left: "/f" + number + "/left",
      right: "/f" + number + "/right", operator: "le", value_type: "number",
      stages: ["input", "output"]};
  });
  const valid = Object.fromEntries(rules.map((rule, i) => {
    const number = String(i).padStart(3, "0");
    return ["f" + number, {left: 1, right: 2}];
  }));
  const violating = structuredClone(valid);
  violating.f000.left = 4;
  violating.f499.left = 4;
  const targets = ["business.ordinary_bulk_000", "business.ordinary_bulk_499"];
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    await connect();
    const baseline = await current();
    await page.locator("#comparison-bulk-panel summary").click();
    const jsonBox = page.locator("#comparison-bulk-json");

    // A bad final rule must never partially replace 499 valid entries,
    // modify the authoritative tenant, or silently enable a check.
    const invalid = rules.map(rule => ({...rule}));
    invalid[499].left = "relative/not/a/json/pointer";
    await jsonBox.fill(JSON.stringify(invalid));
    await page.locator("#comparison-bulk-confirm").check();
    await page.locator("#comparison-bulk-import").click();
    await expect(page.locator("#comparison-bulk-status")).toContainText("Comparison 500:");
    await expect(page.locator("#comparison-draft-summary")).toContainText("No pending changes");
    expect((await current()).revision).toBe(baseline.revision);
    expect((await current()).comparisons).toEqual(baseline.comparisons);

    await jsonBox.fill(JSON.stringify(rules));
    await page.locator("#comparison-bulk-confirm").check();
    await page.locator("#comparison-bulk-import").click();
    await expect(page.locator("#comparison-bulk-status")).toContainText("500 comparison definitions staged");
    await expect(page.locator("#comparison-draft-summary")).toContainText("Unsaved changes");
    expect((await current()).revision).toBe(baseline.revision);
    expect((await current()).comparisons).toEqual(baseline.comparisons);
    await expect(page.locator("#comparison-drafts li[data-comparison-id]")).toHaveCount(500);
    await page.locator("#comparison-filter").fill("ordinary_bulk_499");
    await expect(page.locator("#comparison-filter-status")).toContainText("Showing 1 of 500");
    await expect(page.locator('#comparison-drafts li[data-comparison-id="ordinary_bulk_499"]')).toHaveCount(1);

    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    let saved = await current();
    expect(saved.revision).toBeGreaterThan(baseline.revision);
    expect(saved.comparisons).toEqual(rules);
    await connect();
    await page.locator("#comparison-filter").fill("ordinary_bulk_499");
    await expect(page.locator("#comparison-filter-status")).toContainText("Showing 1 of 500");
    for (const stage of ["input", "output"]) {
      for (const [document, expected] of [[violating, true], [valid, false]]) {
        const response = await request.post("/v1/checks/" + stage, {
          headers, data: {application_id: "demo2", content_format: "json",
            content: JSON.stringify(document)}
        });
        expect(response.status()).toBe(200);
        const result = await response.json();
        expect(result.evaluation_status).toBe("complete");
        const findings = result.findings.map(f => f.rule_id);
        for (const id of targets) {
          expect(findings.includes(id), stage + ": " + id).toBe(expected);
        }
        if (expected) expect(result.effective_action).toBe("block");
        // Unrelated enabled tenant checks can still flag/block benign input.
      }
    }
    saved = await current();
    expect(saved.comparisons).toEqual(rules);
  } finally {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
  }
});
