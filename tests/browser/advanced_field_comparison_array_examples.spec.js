// FC-031–050: customers must be able to save, reload and run an advanced rule
// using a *real* JSON array index, rather than an object key named "0".
const {test, expect} = require("@playwright/test");

test("advanced conversion array-index example flows through saved tenant, dashboard and both Engine stages", async ({page, request}) => {
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  const headers = {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
  const getTenant = async () => {
    const response = await request.get("/v1/applications/current", {headers});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  };
  const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
  expect(reset.status()).toBe(200);
  try {
    // The demo baseline contains two legitimate, unrelated ordinary comparisons.
    // This scenario must explicitly isolate the advanced rule instead of silently
    // ignoring their real findings or weakening the all-findings assertion.
    const baseline = await getTenant();
    expect(baseline.comparisons.map(rule => rule.rule_id).sort())
      .toEqual(["date_order", "refund_limit"]);
    const isolate = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: baseline.revision, changes: {comparisons: []}
    }});
    expect(isolate.status()).toBe(200);
    const isolated = await getTenant();
    expect(isolated.revision).toBe(baseline.revision + 1);
    expect(isolated.comparisons).toEqual([]);
    await page.reload();
    await page.locator("#demo-user").selectOption("demo4");
    await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
    await expect(page.locator("#conversion-form")).toBeVisible();
    await page.locator("#conversion-id").fill("array_mass");
    await page.locator("#conversion-kind").selectOption("unit_load_kg_cap");
    await page.locator("#conversion-field-load").fill("/loads/0/load");
    await page.locator("#conversion-field-load_unit").fill("/loads/0/load_unit");
    await page.locator("#conversion-field-max_kg").fill("/loads/0/max_kg");
    await page.locator("#conversion-apply").click();
    await expect(page.locator('[data-conversion-id="array_mass"]')).toHaveCount(1);
    expect((await getTenant()).advanced_conversion_time).toEqual([]);

    await page.locator("#tenant-check-input-cross_field").selectOption("flag");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    const before = await getTenant();
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const saved = await getTenant();
    expect(saved.revision).toBe(before.revision + 1);
    expect(saved.advanced_conversion_time).toHaveLength(1);
    expect(saved.advanced_conversion_time[0]).toMatchObject({
      rule_id: "array_mass", kind: "unit_load_kg_cap",
      fields: {
        load: "/loads/0/load", load_unit: "/loads/0/load_unit",
        max_kg: "/loads/0/max_kg"
      },
      stages: ["input", "output"]
    });
    const row = saved.record_rules.find(r => r.rule_id === "business.array_mass");
    expect(row).toBeTruthy();
    expect(row.example_unavailable).toBeNull();
    expect(row.look_alike_unavailable).toBeNull();
    const violating = JSON.parse(row.example);
    const safe = JSON.parse(row.look_alike);
    expect(Array.isArray(violating.loads)).toBe(true);
    expect(Array.isArray(safe.loads)).toBe(true);
    expect(violating.loads[0].load).toBe(1500);
    expect(safe.loads[0].load).toBe(1000);

    await page.reload();
    await page.locator("#demo-user").selectOption("demo4");
    await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
    await expect(page.locator('[data-conversion-id="array_mass"]')).toHaveCount(1);
    await page.getByRole("button", {name: "Try violating input conversion array_mass"}).click();
    expect(JSON.parse(await page.locator("#input-content").inputValue())).toEqual(violating);
    const inputResponse = page.waitForResponse(r => r.url().endsWith("/v1/checks/input") &&
      r.request().method() === "POST");
    await page.locator("#run-input").click();
    const input = await (await inputResponse).json();
    expect(input.evaluation_status).toBe("complete");
    expect(input.effective_action).toBe("flag");
    expect(input.findings.filter(f => f.rule_id.startsWith("business.")).map(f => f.rule_id))
      .toEqual(["business.array_mass"]);
    await expect(page.locator('#input-results button[aria-label="Review input rule settings for business.array_mass"]')).toHaveCount(1);

    await page.getByRole("button", {name: "Try violating output conversion array_mass"}).click();
    expect(JSON.parse(await page.locator("#output-content").inputValue())).toEqual(violating);
    const outputResponse = page.waitForResponse(r => r.url().endsWith("/v1/checks/output") &&
      r.request().method() === "POST");
    await page.locator("#run-output").click();
    const output = await (await outputResponse).json();
    expect(output.evaluation_status).toBe("complete");
    expect(output.effective_action).toBe("block");
    expect(output.findings.filter(f => f.rule_id.startsWith("business.")).map(f => f.rule_id))
      .toEqual(["business.array_mass"]);
    await expect(page.locator('#output-results button[aria-label="Review output rule settings for business.array_mass"]')).toHaveCount(1);
    await expect(page.locator("#answer")).toBeHidden();

    const benign = await request.post("/v1/checks/input", {headers, data: {
      application_id: "demo4", content_format: "json", content: JSON.stringify(safe)
    }});
    expect(benign.status()).toBe(200);
    const clean = await benign.json();
    expect(clean.evaluation_status).toBe("complete");
    expect(clean.effective_action).toBe("allow");
    expect(clean.findings.filter(f => f.rule_id.startsWith("business."))).toEqual([]);
    const after = await getTenant();
    expect(after.advanced_conversion_time).toEqual(saved.advanced_conversion_time);
    expect(after.revision).toBe(saved.revision);
  } finally {
    const response = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(response.status()).toBe(200);
  }
});
