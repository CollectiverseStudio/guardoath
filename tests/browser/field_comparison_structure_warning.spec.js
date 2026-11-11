const { test, expect } = require("@playwright/test");

function rules(size, nested) {
  return Array.from({length: size}, (_, i) => {
    const n = String(i).padStart(3, "0");
    const prefix = nested ? "/" : "";
    return {
      rule_id: "shape_" + n,
      left: nested ? "/left/k_" + n : "/left_" + n,
      right: nested ? "/right/k_" + n : "/right_" + n,
      operator: "le",
      value_type: "number",
      stages: ["input", "output"]
    };
  });
}

test("field-comparison bulk editor warns on overfull flat and nested objects but not valid 501-rule split nested", async ({page}) => {
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");

  await page.locator("#comparison-bulk-panel > summary").click();
  const json = page.locator("#comparison-bulk-json");
  const confirm = page.locator("#comparison-bulk-confirm");
  const importButton = page.locator("#comparison-bulk-import");

  await json.fill(JSON.stringify(rules(501, false)));
  await confirm.check(); await importButton.click();
  await expect(page.locator("#comparison-draft-summary")).toContainText("501 comparisons configured");
  await expect(page.locator("#comparison-structure-warning")).toBeVisible();
  await expect(page.locator("#comparison-structure-warning")).toContainText("input references 1002 distinct top-level fields");
  await expect(page.locator("#comparison-structure-warning")).toContainText("output references 1002 distinct top-level fields");
  await expect(page.locator("#comparison-structure-warning")).toContainText("Use nested objects");

  // 501 rules can also be infeasible even with only one root object:
  // its immediate child map would need 1,002 distinct keys.
  const overloadedNested = rules(501, true).map(rule => ({
    ...rule,
    left: rule.left.replace("/left/k_", "/telemetry/l_"),
    right: rule.right.replace("/right/k_", "/telemetry/r_")
  }));
  await json.fill(JSON.stringify(overloadedNested));
  await confirm.check(); await importButton.click();
  await expect(page.locator("#comparison-structure-warning")).toBeVisible();
  await expect(page.locator("#comparison-structure-warning"))
    .toContainText("input references 1002 distinct fields within a nested object");
  await expect(page.locator("#comparison-structure-warning"))
    .toContainText("output references 1002 distinct fields within a nested object");
  await expect(page.locator("#comparison-structure-warning"))
    .toContainText("including nested objects");

  await json.fill(JSON.stringify(rules(501, true)));
  await confirm.check(); await importButton.click();
  await expect(page.locator("#comparison-draft-summary")).toContainText("501 comparisons configured");
  await expect(page.locator("#comparison-structure-warning")).toBeHidden();
  await expect(page.locator("#comparison-draft-summary")).toContainText("Unsaved changes");
  // No Save checks action: draft import does not mutate authoritative tenant.
});


test("customer saves 501 split-nested comparisons in dashboard and runs both real tenant stages", async ({page, request}) => {
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  const headers = {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status(), (await reset.text()).slice(0,200)).toBe(200);
    resetRequired = true;
    await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
    await page.locator("#demo-user").selectOption("demo2");
    await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
    const before = await request.get("/v1/applications/current", {headers});
    expect(before.status()).toBe(200);
    const starting = (await before.json()).tenant;
    // Demo reset restores its published two-rule default; it is not an empty policy.
    expect(starting.comparisons.map(rule => rule.rule_id)).toEqual(["date_order", "refund_limit"]);

    const definitions = rules(501, true);
    await page.locator("#comparison-bulk-panel > summary").click();
    await page.locator("#comparison-bulk-json").fill(JSON.stringify(definitions));
    await page.locator("#comparison-bulk-confirm").check();
    await page.locator("#comparison-bulk-import").click();
    await expect(page.locator("#comparison-draft-summary")).toContainText("501 comparisons configured");
    await expect(page.locator("#comparison-structure-warning")).toBeHidden();

    // Draft import has not written anything; the existing Save is revisioned
    // and the current tenant record alone determines Input/Output actions.
    const preSave = await request.get("/v1/applications/current", {headers});
    expect((await preSave.json()).tenant.comparisons).toEqual(starting.comparisons);
    await page.locator("#tenant-check-input-cross_field").selectOption("flag");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await request.get("/v1/applications/current", {headers});
    expect(saved.status()).toBe(200);
    const tenant = (await saved.json()).tenant;
    expect(tenant.revision).toBe(starting.revision + 1);
    expect(tenant.comparisons).toEqual(definitions);
    expect(tenant.input_checks.cross_field).toBe("flag");
    expect(tenant.output_checks.cross_field).toBe("block");
    expect(tenant.record_rules.filter(r => r.rule_id.startsWith("business.shape_"))).toHaveLength(501);

    // Real reconnect rereads the tenant; no mocked GETs or shadow editor state.
    await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
    await page.locator("#demo-user").selectOption("demo2");
    await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
    await expect(page.locator("#comparison-draft-summary")).toContainText("No pending changes.");
    await expect(page.locator('#tenant-group-cross_field .rule-row[data-rule^="business.shape_"]')).toHaveCount(501);
    await expect(page.locator("#comparison-structure-warning")).toBeHidden();

    const body = {
      left: Object.fromEntries(Array.from({length:501}, (_, i) => ["k_" + String(i).padStart(3,"0"), 1])),
      right: Object.fromEntries(Array.from({length:501}, (_, i) => ["k_" + String(i).padStart(3,"0"), 2]))
    };
    for (const stage of ["input", "output"]) {
      for (const violating of [false, true]) {
        body.left.k_000 = violating ? 9 : 1;
        body.left.k_500 = violating ? 9 : 1;
        const result = await request.post("/v1/checks/" + stage, {headers, data: {
          application_id: "demo2", content_format: "json", content: JSON.stringify(body)
        }});
        expect(result.status(), (await result.text()).slice(0,200)).toBe(200);
        const checked = await result.json();
        expect(checked.evaluation_status).toBe("complete");
        expect(checked.findings.filter(f => f.rule_id.startsWith("business.shape_"))
          .map(f => f.rule_id).sort()).toEqual(violating
            ? ["business.shape_000", "business.shape_500"] : []);
        expect(checked.effective_action).toBe(violating
          ? (stage === "input" ? "flag" : "block") : "allow");
        expect(JSON.stringify(checked)).not.toContain('"k_000":9');
      }
    }
  } finally {
    if (resetRequired) {
      const cleanup = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(cleanup.status(), (await cleanup.text()).slice(0,200)).toBe(200);
    }
  }
});


test("comparison editor warns for 33-level JSON pointers per stage, but not 32-level paths", async ({page, request}) => {
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  const headers = {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
  const original = await request.get("/v1/applications/current", {headers});
  expect(original.status()).toBe(200);
  const before = (await original.json()).tenant;

  await page.locator("#comparison-bulk-panel > summary").click();
  const json = page.locator("#comparison-bulk-json");
  const warning = page.locator("#comparison-structure-warning");
  async function importDepth(depth, stages) {
    await json.fill(JSON.stringify([{
      rule_id: "deep_boundary",
      left: "/k".repeat(depth),
      right: "/reference",
      operator: "le", value_type: "number", stages
    }]));
    await page.locator("#comparison-bulk-confirm").check();
    await page.locator("#comparison-bulk-import").click();
    await expect(page.locator("#comparison-draft-summary")).toContainText("1 comparison configured");
  }

  await importDepth(33, ["input"]);
  await expect(warning).toBeVisible();
  await expect(warning).toContainText("input requires at least 33 nested JSON containers");
  await expect(warning).not.toContainText("output requires");
  await expect(warning).toContainText("at most 32 nested JSON containers");

  await importDepth(32, ["input"]);
  await expect(warning).toBeHidden();

  await importDepth(33, ["output"]);
  await expect(warning).toBeVisible();
  await expect(warning).toContainText("output requires at least 33 nested JSON containers");
  await expect(warning).not.toContainText("input requires");

  // Import only changes the draft: the tenant and its revision stay authoritative.
  const current = await request.get("/v1/applications/current", {headers});
  expect(current.status()).toBe(200);
  const after = (await current.json()).tenant;
  expect(after.revision).toBe(before.revision);
  expect(after.comparisons).toEqual(before.comparisons);
});
