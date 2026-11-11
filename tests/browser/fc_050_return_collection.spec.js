// FC-050: customer-authenticated optional return collection contract.
// Real tenant Save, record-rule table and Input/Output Engine; no route mocks.
const {test, expect} = require("@playwright/test");

async function connect(page) {
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}
async function tenant(request, headers) {
  const r = await request.get("/v1/applications/current", {headers});
  expect(r.status(), (await r.text()).slice(0, 150)).toBe(200);
  return (await r.json()).tenant;
}
const required = {
  order: {at: "2026-11-06T09:00:00Z"},
  shipment: {at: "2026-11-06T10:00:00Z"},
  delivery: {at: "2026-11-06T11:00:00Z"}
};

test("FC-050 optional indexed return is configured in dashboard and enforced on both stages", async ({page, request}) => {
  const headers = await connect(page);
  let needsReset = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    needsReset = true;
    // The default demo has unrelated ordinary comparison pointers. Keep all
    // privacy checks enabled, but flag normal timestamp PII findings so they
    // do not mask the Field Comparison action under test.
    const initial = await tenant(request, headers);
    const isolated = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: initial.revision, changes: {
        comparisons: [], input_check_pii: "flag", output_check_pii: "flag"
      }
    }});
    expect(isolated.status(), (await isolated.text()).slice(0, 160)).toBe(200);
    const before = await tenant(request, headers);
    await connect(page);

    await page.locator("#conversion-id").fill("fc050_return");
    await page.locator("#conversion-kind").selectOption("lifecycle_chronology");
    for (const [field, pointer] of Object.entries({
      order_at: "/order/at", shipment_at: "/shipment/at",
      delivery_at: "/delivery/at", return_at: "/returns/0/at"
    })) {
      await page.locator("#conversion-field-" + field).fill(pointer);
    }
    await page.locator("#conversion-return-event-pointer").fill("/returns/0");
    await page.locator("#conversion-apply").click();
    await expect(page.locator('[data-conversion-id="fc050_return"]')).toHaveCount(1);

    // Applying a draft does not mutate the authoritative tenant.
    const unsaved = await tenant(request, headers);
    expect(unsaved.revision).toBe(before.revision);
    expect(unsaved.advanced_conversion_time).toEqual(before.advanced_conversion_time);

    await page.locator("#tenant-check-input-cross_field").selectOption("flag");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await tenant(request, headers);
    expect(saved.revision).toBe(before.revision + 1);
    expect(saved.advanced_conversion_time).toContainEqual(expect.objectContaining({
      rule_id: "fc050_return", kind: "lifecycle_chronology",
      fields: {order_at: "/order/at", shipment_at: "/shipment/at",
        delivery_at: "/delivery/at", return_at: "/returns/0/at"},
      return_event_pointer: "/returns/0", stages: ["input", "output"]
    }));
    await connect(page);
    await page.locator("#rule-search").fill("fc050_return");
    await expect(page.locator('tr.rule-row[data-rule="business.fc050_return"]')).toBeVisible();

    for (const stage of ["input", "output"]) {
      const cases = [
        [{...required}, false],
        [{...required, returns: []}, false],
        [{...required, returns: [{at: "2026-11-06T12:00:00Z"}]}, false],
        [{...required, returns: [{}]}, true],
        [{...required, returns: [{at: null}]}, true],
        [{...required, returns: [{at: "2026-11-06T08:00:00Z"}]}, true],
        [{...required, returns: {}}, true],
        [{...required, returns: "malformed"}, true]
      ];
      for (const [payload, violation] of cases) {
        const response = await request.post("/v1/checks/" + stage, {headers, data: {
          application_id: "demo4", content_format: "json", content: JSON.stringify(payload)
        }});
        expect(response.status(), (await response.text()).slice(0, 160)).toBe(200);
        const result = await response.json();
        expect(result.evaluation_status).toBe("complete");
        const matches = result.findings.filter(f => f.rule_id === "business.fc050_return");
        expect(matches.length, stage + " " + JSON.stringify(Object.keys(payload))).toBe(violation ? 1 : 0);
        if (violation) expect(result.effective_action).toBe(stage === "input" ? "flag" : "block");
        expect(JSON.stringify(matches)).not.toContain("2026-11-06");
      }
    }

    // The saved rule is also directly usable from the dashboard example picker.
    await page.getByRole("button", {name: "Try no-return input conversion fc050_return"}).click();
    await expect(page.locator("#input-format")).toHaveValue("json");
    const inputCall = page.waitForResponse(r => r.url().endsWith("/v1/checks/input") &&
      r.request().method() === "POST");
    await page.locator("#run-input").click();
    const inputResult = await (await inputCall).json();
    expect(inputResult.evaluation_status).toBe("complete");
    expect(inputResult.findings.map(f => f.rule_id)).not.toContain("business.fc050_return");
    await expect(page.locator("#run-output")).toBeEnabled();
    await page.getByRole("button", {name: "Try no-return output conversion fc050_return"}).click();
    await expect(page.locator("#output-format")).toHaveValue("json");
    const outputCall = page.waitForResponse(r => r.url().endsWith("/v1/checks/output") &&
      r.request().method() === "POST");
    await page.locator("#run-output").click();
    const outputResult = await (await outputCall).json();
    expect(outputResult.evaluation_status).toBe("complete");
    expect(outputResult.findings.map(f => f.rule_id)).not.toContain("business.fc050_return");
  } finally {
    if (needsReset) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status(), (await reset.text()).slice(0, 150)).toBe(200);
    }
  }
});
