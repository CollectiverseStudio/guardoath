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
    expect(integrated.advanced_identity).toContainEqual(expect.objectContaining({
      rule_id: "shipment_order_matches", kind: "order_id_match",
      fields: {order_id: "/order/id", shipment_order_id: "/shipment/order_id"},
      stages: ["input", "output"]
    }));
    expect(integrated.advanced_conversion_time).toContainEqual(expect.objectContaining({
      rule_id: "cargo_mass_within_limit", kind: "unit_load_kg_cap",
      fields: {load: "/shipment/load", load_unit: "/shipment/load_unit",
               max_kg: "/shipment/max_kg"}, stages: ["input", "output"]
    }));
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

    // FC-032 and FC-036: two more customer-operable arithmetic examples,
    // saved in one tenant revision and enforced by the existing Engine.
    const beforeInvoiceStock = await current();
    await page.locator("#arithmetic-sample-invoice-total").click();
    await expect(page.locator("#arithmetic-id")).toHaveValue("invoice_total_matches");
    await expect(page.locator("#arithmetic-kind")).toHaveValue("invoice_total");
    for (const role of ["subtotal", "discount", "tax", "shipping", "total"]) {
      await expect(page.locator("#arithmetic-field-" + role)).toHaveValue("/billing/" + role);
    }
    await expect(page.locator("#arithmetic-stage-input")).toBeChecked();
    await expect(page.locator("#arithmetic-stage-output")).toBeChecked();
    await expect(page.locator("#arithmetic-message")).toContainText("not added or saved");
    expect((await current()).revision).toBe(beforeInvoiceStock.revision);
    await page.locator("#arithmetic-apply").click();
    await expect(page.locator("#arithmetic-draft-summary")).toContainText("1 added");

    await page.locator("#arithmetic-sample-inventory").click();
    await expect(page.locator("#arithmetic-id")).toHaveValue("inventory_balances");
    await expect(page.locator("#arithmetic-kind")).toHaveValue("inventory_conservation");
    for (const role of ["opening", "received", "shipped", "lost", "closing"]) {
      await expect(page.locator("#arithmetic-field-" + role)).toHaveValue("/inventory/" + role);
    }
    await expect(page.locator("#arithmetic-stage-input")).toBeChecked();
    await expect(page.locator("#arithmetic-stage-output")).toBeChecked();
    await expect(page.locator("#arithmetic-message")).toContainText("not added or saved");
    expect((await current()).advanced_arithmetic).toEqual(beforeInvoiceStock.advanced_arithmetic);
    expect((await current()).revision).toBe(beforeInvoiceStock.revision);
    await page.locator("#arithmetic-apply").click();
    await expect(page.locator("#arithmetic-draft-summary")).toContainText("2 added");
    const beforeSave = await current();
    expect(beforeSave.advanced_arithmetic).toEqual(beforeInvoiceStock.advanced_arithmetic);
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const afterSave = await current();
    expect(afterSave.revision).toBe(beforeInvoiceStock.revision + 1);
    expect(afterSave.advanced_arithmetic).toContainEqual({
      rule_id: "invoice_total_matches", kind: "invoice_total", stages: ["input", "output"],
      fields: {subtotal: "/billing/subtotal", discount: "/billing/discount",
               tax: "/billing/tax", shipping: "/billing/shipping", total: "/billing/total"}
    });
    expect(afterSave.advanced_arithmetic).toContainEqual({
      rule_id: "inventory_balances", kind: "inventory_conservation", stages: ["input", "output"],
      fields: {opening: "/inventory/opening", received: "/inventory/received",
               shipped: "/inventory/shipped", lost: "/inventory/lost", closing: "/inventory/closing"}
    });
    expect(afterSave.advanced_identity_reference_status).toEqual(integrated.advanced_identity_reference_status);
    expect(afterSave.advanced_conversion_reference_status).toEqual(integrated.advanced_conversion_reference_status);

    const accountingWitnesses = [
      ["invoice_total_matches",
       {billing: {subtotal: 100, discount: 10, tax: 8, shipping: 2, total: 99}},
       {billing: {subtotal: 100, discount: 10, tax: 8, shipping: 2, total: 100}}],
      ["inventory_balances",
       {inventory: {opening: 50, received: 20, shipped: 15, lost: 5, closing: 51}},
       {inventory: {opening: 50, received: 20, shipped: 15, lost: 5, closing: 50}}]
    ];
    for (const stage of ["input", "output"]) {
      for (const [id, invalid, valid] of accountingWitnesses) {
        for (const [payload, violation] of [[invalid, true], [valid, false]]) {
          const response = await page.request.post("/v1/checks/" + stage, {
            headers, data: {application_id: "demo8", content_format: "json",
                           content: JSON.stringify(payload)}
          });
          expect(response.status()).toBe(200);
          const result = await response.json();
          expect(result.evaluation_status).toBe("complete");
          expect(result.findings.some(f => f.rule_id === "business." + id)).toBe(violation);
          if (violation) expect(result.effective_action).toBe("block");
        }
      }
    }
    await page.locator("#arithmetic-sample-invoice-total").click();
    await expect(page.locator("#arithmetic-message")).toContainText("already exists");
    await page.locator("#arithmetic-sample-inventory").click();
    await expect(page.locator("#arithmetic-message")).toContainText("already exists");
    expect((await current()).revision).toBe(afterSave.revision);
    expect((await current()).advanced_arithmetic).toEqual(afterSave.advanced_arithmetic);

    // FC-037 shipment quantities and FC-046 carbon intensity are genuine
    // guided, tenant-owned saved rules, not frontend-only demonstration logic.
    const beforeShipmentIntensity = await current();
    await page.locator("#arithmetic-sample-shipment").click();
    await expect(page.locator("#arithmetic-id")).toHaveValue("shipment_quantity_balances");
    await expect(page.locator("#arithmetic-kind")).toHaveValue("shipment_quantity_sum");
    await expect(page.locator("#arithmetic-field-lines")).toHaveValue("/shipment/lines");
    await expect(page.locator("#arithmetic-field-total")).toHaveValue("/shipment/total");
    await expect(page.locator("#arithmetic-message")).toContainText("not added or saved");
    await expect(page.locator("#arithmetic-stage-input")).toBeChecked();
    await expect(page.locator("#arithmetic-stage-output")).toBeChecked();
    expect((await current()).revision).toBe(beforeShipmentIntensity.revision);
    await page.locator("#arithmetic-apply").click();
    await expect(page.locator("#arithmetic-draft-summary")).toContainText("1 added");
    await page.locator("#arithmetic-sample-intensity").click();
    await expect(page.locator("#arithmetic-id")).toHaveValue("carbon_intensity_within_limit");
    await expect(page.locator("#arithmetic-kind")).toHaveValue("carbon_intensity");
    for (const field of ["emissions", "output", "max_intensity"]) {
      await expect(page.locator("#arithmetic-field-" + field)).toHaveValue("/operations/" + field);
    }
    await expect(page.locator("#arithmetic-stage-input")).toBeChecked();
    await expect(page.locator("#arithmetic-stage-output")).toBeChecked();
    await expect(page.locator("#arithmetic-message")).toContainText("not added or saved");
    await page.locator("#arithmetic-apply").click();
    await expect(page.locator("#arithmetic-draft-summary")).toContainText("2 added");
    const beforeShipmentSave = await current();
    expect(beforeShipmentSave.revision).toBe(beforeShipmentIntensity.revision);
    expect(beforeShipmentSave.advanced_arithmetic).toEqual(beforeShipmentIntensity.advanced_arithmetic);
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const shipmentSaved = await current();
    expect(shipmentSaved.revision).toBe(beforeShipmentIntensity.revision + 1);
    expect(shipmentSaved.advanced_arithmetic).toContainEqual({
      rule_id: "shipment_quantity_balances", kind: "shipment_quantity_sum",
      stages: ["input", "output"],
      fields: {lines: "/shipment/lines", total: "/shipment/total"}
    });
    expect(shipmentSaved.advanced_arithmetic).toContainEqual({
      rule_id: "carbon_intensity_within_limit", kind: "carbon_intensity",
      stages: ["input", "output"],
      fields: {emissions: "/operations/emissions", output: "/operations/output",
               max_intensity: "/operations/max_intensity"}
    });
    expect(shipmentSaved.advanced_identity_reference_status)
      .toEqual(beforeShipmentIntensity.advanced_identity_reference_status);
    expect(shipmentSaved.advanced_conversion_reference_status)
      .toEqual(beforeShipmentIntensity.advanced_conversion_reference_status);

    const environmentalAndQuantityWitnesses = [
      ["shipment_quantity_balances",
       {shipment: {lines: [{quantity: 3}, {quantity: 2}], total: 4}},
       {shipment: {lines: [{quantity: 3}, {quantity: 2}], total: 5}}],
      ["carbon_intensity_within_limit",
       {operations: {emissions: 250, output: 100, max_intensity: 2}},
       {operations: {emissions: 200, output: 100, max_intensity: 2}}]
    ];
    for (const stage of ["input", "output"]) {
      for (const [id, invalid, valid] of environmentalAndQuantityWitnesses) {
        for (const [payload, violates] of [[invalid, true], [valid, false]]) {
          const response = await page.request.post("/v1/checks/" + stage, {
            headers, data: {application_id: "demo8", content_format: "json",
                           content: JSON.stringify(payload)}
          });
          expect(response.status()).toBe(200);
          const evaluation = await response.json();
          expect(evaluation.evaluation_status).toBe("complete");
          expect(evaluation.findings.some(f => f.rule_id === "business." + id)).toBe(violates);
          if (violates) expect(evaluation.effective_action).toBe("block");
        }
      }
    }
    await page.locator("#arithmetic-sample-shipment").click();
    await expect(page.locator("#arithmetic-message")).toContainText("already exists");
    await page.locator("#arithmetic-sample-intensity").click();
    await expect(page.locator("#arithmetic-message")).toContainText("already exists");
    expect((await current()).revision).toBe(shipmentSaved.revision);
    expect((await current()).advanced_arithmetic).toEqual(shipmentSaved.advanced_arithmetic);
  } finally {
    await reset();
  }
});


test("tax, discount and manufacturing guided forms persist three independent tenant arithmetic checks and block on both stages", async ({page}) => {
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

    const samples = [
      ["#arithmetic-sample-tax", "tax_within_tolerance", "tax_tolerance",
       {tax: "/tax/charged", base: "/tax/base", rate: "/tax/rate",
        tolerance: "/tax/tolerance"}],
      ["#arithmetic-sample-discount", "discount_within_fraction",
       "discount_fraction_cap",
       {discount: "/discount/amount", eligible_subtotal: "/discount/eligible_subtotal",
        max_fraction: "/discount/max_fraction"}],
      ["#arithmetic-sample-manufacturing", "measurement_within_tolerance",
       "manufacturing_tolerance",
       {measured: "/quality/measured", nominal: "/quality/nominal",
        tolerance: "/quality/tolerance"}],
    ];
    for (const [button, id, kind, fields] of samples) {
      await page.locator(button).click();
      await expect(page.locator("#arithmetic-id")).toHaveValue(id);
      await expect(page.locator("#arithmetic-kind")).toHaveValue(kind);
      for (const [field, pointer] of Object.entries(fields)) {
        await expect(page.locator("#arithmetic-field-" + field)).toHaveValue(pointer);
      }
      await expect(page.locator("#arithmetic-stage-input")).toBeChecked();
      await expect(page.locator("#arithmetic-stage-output")).toBeChecked();
      await expect(page.locator("#arithmetic-message")).toContainText("not added or saved");
      expect((await current()).revision).toBe(before.revision);
      await page.locator("#arithmetic-apply").click();
      await expect(page.locator("#arithmetic-message")).toContainText("pending");
    }
    await expect(page.locator("#arithmetic-draft-summary")).toContainText("3 added");
    const pending = await current();
    expect(pending.advanced_arithmetic).toEqual([]);
    expect(pending.revision).toBe(before.revision);
    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const saved = await current();
    expect(saved.revision).toBe(before.revision + 1);
    for (const [, id, kind, fields] of samples) {
      expect(saved.advanced_arithmetic).toContainEqual({
        rule_id: id, kind, fields, stages: ["input", "output"]
      });
    }
    expect(saved.advanced_identity_reference_status).toEqual(
      before.advanced_identity_reference_status);
    expect(saved.advanced_conversion_reference_status).toEqual(
      before.advanced_conversion_reference_status);

    const witnesses = [
      ["tax_within_tolerance",
       {tax: {charged: 7, base: 100, rate: 0.08, tolerance: 0.5}},
       {tax: {charged: 7.5, base: 100, rate: 0.08, tolerance: 0.5}}],
      ["discount_within_fraction",
       {discount: {amount: 25, eligible_subtotal: 100, max_fraction: 0.2}},
       {discount: {amount: 20, eligible_subtotal: 100, max_fraction: 0.2}}],
      ["measurement_within_tolerance",
       {quality: {measured: 10.4, nominal: 10, tolerance: 0.2}},
       {quality: {measured: 10.1, nominal: 10, tolerance: 0.2}}],
    ];
    for (const stage of ["input", "output"]) {
      for (const [id, violating, benign] of witnesses) {
        for (const [payload, expectedFinding] of [[violating, true], [benign, false]]) {
          const response = await page.request.post("/v1/checks/" + stage, {
            headers, data: {application_id: "demo8", content_format: "json",
                            content: JSON.stringify(payload)}
          });
          expect(response.status()).toBe(200);
          const result = await response.json();
          expect(result.evaluation_status).toBe("complete");
          expect(result.findings.some(f => f.rule_id === "business." + id))
            .toBe(expectedFinding);
          if (expectedFinding) expect(result.effective_action).toBe("block");
        }
      }
    }
    for (const [button] of samples) {
      await page.locator(button).click();
      await expect(page.locator("#arithmetic-message")).toContainText("already exists");
    }
    // Removing a saved rule is not an authorization to silently reuse the
    // saved identity before the tenant's revisioned Save commits its deletion.
    await page.getByRole("button", {name: "Remove arithmetic discount_within_fraction"}).click();
    await expect(page.locator("#arithmetic-draft-summary")).toContainText("1 removed");
    await page.locator("#arithmetic-sample-discount").click();
    await expect(page.locator("#arithmetic-message")).toContainText("already exists");
    const after = await current();
    expect(after.revision).toBe(saved.revision);
    expect(after.advanced_arithmetic).toEqual(saved.advanced_arithmetic);
  } finally {
    await reset();
  }
});
