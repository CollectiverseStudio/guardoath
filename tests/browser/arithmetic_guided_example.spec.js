const {test, expect} = require("@playwright/test");

test("authenticated customer can fill, save and evaluate the invoice arithmetic example on both stages", async ({page}) => {
  await page.goto("/dashboard");
  await page.locator("#comparison-configuration").evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo8");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo8");
  const headers = {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
  const current = async () => {
    const response = await page.request.get("/v1/applications/current", {headers});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  };
  const reset = async () => {
    const response = await page.request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(response.status()).toBe(200);
  };
  await reset();
  try {
    await page.reload();
    await page.locator("#comparison-configuration").evaluate(el => { el.open = true; });
    await page.locator("#demo-user").selectOption("demo8");
    await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo8");
    const before = await current();
    expect(before.advanced_arithmetic).toEqual([]);

    await page.locator("#arithmetic-sample").click();
    await expect(page.locator("#arithmetic-id")).toHaveValue("invoice_lines_match_subtotal");
    await expect(page.locator("#arithmetic-kind")).toHaveValue("invoice_line_subtotal");
    await expect(page.locator("#arithmetic-field-lines")).toHaveValue("/invoice/lines");
    await expect(page.locator("#arithmetic-field-subtotal")).toHaveValue("/invoice/subtotal");
    await expect(page.locator("#arithmetic-stage-input")).toBeChecked();
    await expect(page.locator("#arithmetic-stage-output")).toBeChecked();
    await expect(page.locator("#arithmetic-message")).toContainText("not added or saved");
    expect((await current()).advanced_arithmetic).toEqual(before.advanced_arithmetic);
    expect((await current()).revision).toBe(before.revision);

    await page.locator("#arithmetic-apply").click();
    await expect(page.locator("#arithmetic-draft-summary")).toContainText("1 added");
    expect((await current()).advanced_arithmetic).toEqual(before.advanced_arithmetic);
    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const saved = await current();
    expect(saved.revision).toBeGreaterThan(before.revision);
    expect(saved.advanced_arithmetic).toContainEqual({
      rule_id: "invoice_lines_match_subtotal",
      kind: "invoice_line_subtotal",
      fields: {lines: "/invoice/lines", subtotal: "/invoice/subtotal"},
      stages: ["input", "output"]
    });

    for (const stage of ["input", "output"]) {
      for (const [subtotal, violates] of [[100, true], [110, false]]) {
        const response = await page.request.post("/v1/checks/" + stage, {
          headers, data: {application_id: "demo8", content_format: "json", content: JSON.stringify({
            invoice: {lines: [{quantity: 2, unit_price: 40}, {quantity: 1, unit_price: 30}], subtotal}
          })}
        });
        expect(response.status()).toBe(200);
        const body = await response.json();
        expect(body.evaluation_status).toBe("complete");
        const hasRuleFinding = body.findings.some(f => f.rule_id === "business.invoice_lines_match_subtotal");
        expect(hasRuleFinding).toBe(violates);
        if (violates) expect(body.effective_action).toBe("block");
      }
    }
    await page.locator("#arithmetic-sample").click();
    await expect(page.locator("#arithmetic-message")).toContainText("already exists");
    expect((await current()).advanced_arithmetic).toEqual(saved.advanced_arithmetic);
  } finally {
    await reset();
  }
});
