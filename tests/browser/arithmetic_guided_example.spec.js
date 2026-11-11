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

    // Guide two more families through the customer-visible forms, with NO
    // tenant mutation before explicit Add + revisioned Save checks.
    const prior = await current();
    await page.locator("#identity-sample").click();
    await expect(page.locator("#identity-id")).toHaveValue("shipment_order_matches");
    await expect(page.locator("#identity-kind")).toHaveValue("order_id_match");
    await expect(page.locator("#identity-field-order_id")).toHaveValue("/order/id");
    await expect(page.locator("#identity-field-shipment_order_id")).toHaveValue("/shipment/order_id");
    await expect(page.locator("#identity-stage-input")).toBeChecked();
    await expect(page.locator("#identity-stage-output")).toBeChecked();
    await expect(page.locator("#identity-message")).toContainText("not added or saved");
    await page.locator("#conversion-sample").click();
    await expect(page.locator("#conversion-id")).toHaveValue("cargo_mass_within_limit");
    await expect(page.locator("#conversion-kind")).toHaveValue("unit_load_kg_cap");
    await expect(page.locator("#conversion-field-load")).toHaveValue("/shipment/load");
    await expect(page.locator("#conversion-field-load_unit")).toHaveValue("/shipment/load_unit");
    await expect(page.locator("#conversion-field-max_kg")).toHaveValue("/shipment/max_kg");
    await expect(page.locator("#conversion-stage-input")).toBeChecked();
    await expect(page.locator("#conversion-stage-output")).toBeChecked();
    await expect(page.locator("#conversion-message")).toContainText("not added or saved");
    const afterFill = await current();
    expect(afterFill.revision).toBe(prior.revision);
    expect(afterFill.advanced_identity).toEqual(prior.advanced_identity);
    expect(afterFill.advanced_conversion_time).toEqual(prior.advanced_conversion_time);
    expect(afterFill.advanced_identity_reference_status).toEqual(prior.advanced_identity_reference_status);
    expect(afterFill.advanced_conversion_reference_status).toEqual(prior.advanced_conversion_reference_status);

    await page.locator("#identity-apply").click();
    await page.locator("#conversion-apply").click();
    await expect(page.locator("#identity-draft-summary")).toContainText("1 added");
    await expect(page.locator("#conversion-draft-summary")).toContainText("1 added");
    expect((await current()).revision).toBe(prior.revision);
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const integrated = await current();
    expect(integrated.revision).toBe(prior.revision + 1);
    expect(integrated.advanced_identity).toContainEqual({
      rule_id: "shipment_order_matches", kind: "order_id_match",
      fields: {order_id: "/order/id", shipment_order_id: "/shipment/order_id"},
      stages: ["input", "output"], trusted_values: []
    });
    expect(integrated.advanced_conversion_time).toContainEqual({
      rule_id: "cargo_mass_within_limit", kind: "unit_load_kg_cap",
      fields: {load: "/shipment/load", load_unit: "/shipment/load_unit",
               max_kg: "/shipment/max_kg"}, stages: ["input", "output"]
    });
    expect(integrated.advanced_arithmetic).toEqual(saved.advanced_arithmetic);
    expect(integrated.advanced_identity_reference_status).toEqual(prior.advanced_identity_reference_status);
    expect(integrated.advanced_conversion_reference_status).toEqual(prior.advanced_conversion_reference_status);

    // Actual shared Engine evaluation on BOTH stages, not a synthetic
    // dashboard-only result. Each own-rule finding must discriminate a
    // violating payload from its benign counterpart.
    const witnesses = [
      ["shipment_order_matches",
       {order: {id: "A100"}, shipment: {order_id: "B200"}},
       {order: {id: "A100"}, shipment: {order_id: "A100"}}],
      ["cargo_mass_within_limit",
       {shipment: {load: 1500, load_unit: "kg", max_kg: 1000}},
       {shipment: {load: 1000, load_unit: "kg", max_kg: 1000}}]
    ];
    for (const stage of ["input", "output"]) {
      for (const [id, violation, benign] of witnesses) {
        for (const [payload, violates] of [[violation, true], [benign, false]]) {
          const response = await page.request.post("/v1/checks/" + stage, {
            headers, data: {application_id: "demo8", content_format: "json",
                           content: JSON.stringify(payload)}
          });
          expect(response.status()).toBe(200);
          const body = await response.json();
          expect(body.evaluation_status).toBe("complete");
          expect(body.findings.some(f => f.rule_id === "business." + id)).toBe(violates);
          if (violates) expect(body.effective_action).toBe("block");
        }
      }
    }
    await page.locator("#identity-sample").click();
    await expect(page.locator("#identity-message")).toContainText("already exists");
    await page.locator("#conversion-sample").click();
    await expect(page.locator("#conversion-message")).toContainText("already exists");
    expect((await current()).revision).toBe(integrated.revision);
    expect((await current()).advanced_identity).toEqual(integrated.advanced_identity);
    expect((await current()).advanced_conversion_time).toEqual(integrated.advanced_conversion_time);
  } finally {
    await reset();
  }
});
