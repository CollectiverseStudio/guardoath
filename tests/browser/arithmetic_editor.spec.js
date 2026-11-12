const {test, expect} = require("@playwright/test");

test("FC arithmetic editor creates, saves, reloads, edits and evaluates an actual tenant rule", async ({page}) => {
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo8");
  await page.getByRole("button", {name:"Get demo API key", exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo8");
  const reset = async () => {
    const response = page.waitForResponse(r =>
      r.url().endsWith("/v1/tenants/current/reset") && r.request().method() === "POST");
    await page.getByRole("button", {name:"Reset to defaults", exact:true}).click();
    expect((await response).ok()).toBe(true);
    await expect(page.locator("#tenant-message")).toContainText("Reset to the demo defaults.");
  };
  await reset();
    await expect(page.locator("#arithmetic-form")).toBeVisible();
    await page.locator("#arithmetic-id").fill("ui_invoice_math");
    await page.locator("#arithmetic-kind").selectOption("invoice_line_subtotal");
    await page.locator("#arithmetic-field-lines").fill("/lines");
    await page.locator("#arithmetic-field-subtotal").fill("/subtotal");
    await page.locator("#arithmetic-apply").click();
    await expect(page.locator('[data-arithmetic-id="ui_invoice_math"]')).toHaveCount(1);
    const bearer = "Bearer " + await page.locator("#demo-key-value").inputValue();
    const read = async () => {
      const response = await page.request.get("/v1/applications/current", {
        headers: {Authorization: bearer}
      });
      expect(response.ok()).toBe(true);
      return (await response.json()).tenant;
    };
    expect((await read()).advanced_arithmetic).toEqual([]);

    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("flag");
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    let data = (await read()).advanced_arithmetic;
    expect(data).toHaveLength(1);
    expect(data[0]).toMatchObject({rule_id:"ui_invoice_math",kind:"invoice_line_subtotal",
      fields:{lines:"/lines",subtotal:"/subtotal"},stages:["input","output"]});
    await page.locator("#rule-search").fill("ui_invoice_math");
    await expect(page.locator('tr.rule-row[data-rule="business.ui_invoice_math"]')).toBeVisible();
    await page.locator("#rule-search").fill("");

    await page.getByRole("button", {name:"Try violating input arithmetic ui_invoice_math"}).click();
    await expect(page.locator("#input-format")).toHaveValue("json");
    expect(JSON.parse(await page.locator("#input-content").inputValue())).toMatchObject({
      lines:[{quantity:2,unit_price:40},{quantity:1,unit_price:30}],subtotal:100
    });
    const response = page.waitForResponse(r =>
      r.url().endsWith("/v1/checks/input") && r.request().method() === "POST");
    await page.locator("#run-input").click();
    const result = await (await response).json();
    expect(result.findings.map(f=>f.rule_id)).toContain("business.ui_invoice_math");
    expect(result.effective_action).toBe("block");
    await expect(page.locator("#answer")).toBeHidden();
    await page.getByRole("button", {name:"Try benign input arithmetic ui_invoice_math"}).click();
    expect(JSON.parse(await page.locator("#input-content").inputValue()).subtotal).toBe(110);
    const cleanResponse = page.waitForResponse(r =>
      r.url().endsWith("/v1/checks/input") && r.request().method() === "POST");
    await page.locator("#run-input").click();
    const clean = await (await cleanResponse).json();
    expect(clean.findings.map(f=>f.rule_id)).not.toContain("business.ui_invoice_math");

    await page.getByRole("button", {name:"Edit arithmetic ui_invoice_math"}).click();
    await page.locator("#arithmetic-stage-output").uncheck();
    await page.locator("#arithmetic-apply").click();
    await page.locator("#tenant-check-output-cross_field").selectOption("");
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    data = (await read()).advanced_arithmetic;
    expect(data[0].stages).toEqual(["input"]);

    await page.getByRole("button", {name:"Remove arithmetic ui_invoice_math"}).click();
    await page.locator("#tenant-check-input-cross_field").selectOption("");
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    expect((await read()).advanced_arithmetic).toEqual([]);
  await reset();
});

test("FC arithmetic dashboard validates pointers, types and stages before saving", async ({page}) => {
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo8");
  await page.getByRole("button", {name:"Get demo API key", exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo8");
  await page.getByRole("button", {name:"Reset to defaults", exact:true}).click();
  await expect(page.locator("#tenant-message")).toContainText("Reset to the demo defaults.");

  await page.locator("#arithmetic-id").fill("bad_rule");
  await page.locator("#arithmetic-kind").selectOption("tax_tolerance");
  for (const key of ["tax","base","rate","tolerance"]) {
    await page.locator("#arithmetic-field-" + key).fill("/" + key);
  }
  await page.locator("#arithmetic-field-base").fill("/tax");
  await page.locator("#arithmetic-apply").click();
  await expect(page.locator("#arithmetic-message")).toContainText("distinct and nonoverlapping");
  await expect(page.locator('#arithmetic-drafts [data-arithmetic-id="bad_rule"]')).toHaveCount(0);

  await page.locator("#arithmetic-field-base").fill("/records//base");
  await page.locator("#arithmetic-apply").click();
  await expect(page.locator("#arithmetic-message")).toContainText("empty JSON Pointer segments");
  await expect(page.locator('#arithmetic-drafts [data-arithmetic-id="bad_rule"]')).toHaveCount(0);

  await page.locator("#arithmetic-field-base").fill("/base");
  await page.locator("#arithmetic-stage-input").uncheck();
  await page.locator("#arithmetic-stage-output").uncheck();
  await page.locator("#arithmetic-apply").click();
  await expect(page.locator("#arithmetic-message")).toContainText("Choose Input, Output or both");
  await expect(page.locator('#arithmetic-drafts [data-arithmetic-id="bad_rule"]')).toHaveCount(0);
});

test("bulk JSON replaces 125 arithmetic rules atomically through customer Save and shared Input/Output Engine", async ({page, request}) => {
  await page.goto("/dashboard");
  await page.locator("#comparison-configuration").evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo8");
  await page.getByRole("button", {name:"Get demo API key", exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo8");
  let headers = {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
  const read = async () => {
    const response = await request.get("/v1/applications/current", {headers});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  };
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data:{}});
    expect(reset.status()).toBe(200);
    resetRequired = true;
    await page.reload();
    await page.locator("#comparison-configuration").evaluate(el => { el.open = true; });
    await page.locator("#demo-user").selectOption("demo8");
    await page.getByRole("button", {name:"Get demo API key", exact:true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo8");
    headers = {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
    const initial = await read();
    expect(initial.advanced_arithmetic).toEqual([]);

    const rules = Array.from({length:125}, (_, i) => {
      const id = "bulk_arithmetic_" + String(i).padStart(3, "0");
      const root = "/ar" + String(i).padStart(3, "0");
      return {rule_id:id, kind:"discount_fraction_cap", fields:{
        discount:root + "/discount",
        eligible_subtotal:root + "/eligible_subtotal",
        max_fraction:root + "/max_fraction"
      }, stages:["input","output"]};
    });
    const bulk = page.locator("#arithmetic-bulk-panel");
    await bulk.evaluate(el => { el.open = true; });
    const jsonBox = page.locator("#arithmetic-bulk-json");
    await jsonBox.fill(JSON.stringify(rules));
    await page.locator("#arithmetic-bulk-import").click();
    await expect(page.locator("#arithmetic-bulk-status")).toContainText("Confirm replacement");
    await page.locator("#arithmetic-bulk-confirm").check();
    await page.locator("#arithmetic-bulk-import").click();
    await expect(page.locator("#arithmetic-draft-summary"))
      .toContainText("125 added, 0 updated, 0 removed");
    await expect(page.locator("#arithmetic-filter-status")).toContainText("Showing 125 of 125");
    expect((await read()).revision).toBe(initial.revision);
    expect((await read()).advanced_arithmetic).toEqual([]);

    // One malformed pointer is rejected atomically: no partial draft changes,
    // no tenant mutation, no revision increment and no customer value echo.
    const invalid = structuredClone(rules);
    invalid[7].fields.eligible_subtotal = invalid[7].fields.discount;
    await jsonBox.fill(JSON.stringify(invalid));
    await page.locator("#arithmetic-bulk-confirm").check();
    await page.locator("#arithmetic-bulk-import").click();
    await expect(page.locator("#arithmetic-bulk-status")).toContainText("distinct and nonoverlapping");
    await expect(page.locator("#arithmetic-draft-summary"))
      .toContainText("125 added, 0 updated, 0 removed");
    expect((await read()).revision).toBe(initial.revision);
    await page.locator("#arithmetic-bulk-export").click();
    expect(JSON.parse(await jsonBox.inputValue())).toEqual(rules);

    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const saved = await read();
    expect(saved.revision).toBe(initial.revision + 1);
    expect(saved.advanced_arithmetic).toHaveLength(125);
    await expect(page.locator("#arithmetic-draft-summary"))
      .toContainText("Saved arithmetic rules: 125. No pending changes.");
    await expect(page.locator('tr.rule-row[data-rule^="business.bulk_arithmetic_"]'))
      .toHaveCount(125);

    const good = {};
    for (let i = 0; i < 125; i++) {
      good["ar" + String(i).padStart(3, "0")] =
        {discount:10, eligible_subtotal:100, max_fraction:0.2};
    }
    const bad = structuredClone(good);
    bad.ar124.discount = 30;
    for (const stage of ["input", "output"]) {
      const violating = await request.post("/v1/checks/" + stage, {headers, data:{
        application_id:"demo8", content_format:"json", content:JSON.stringify(bad)
      }});
      expect(violating.status()).toBe(200);
      const result = await violating.json();
      expect(result.evaluation_status).toBe("complete");
      expect(result.findings.map(f => f.rule_id)).toContain("business.bulk_arithmetic_124");
      expect(result.effective_action).toBe("block");

      const benign = await request.post("/v1/checks/" + stage, {headers, data:{
        application_id:"demo8", content_format:"json", content:JSON.stringify(good)
      }});
      expect(benign.status()).toBe(200);
      const benignResult = await benign.json();
      expect(benignResult.evaluation_status).toBe("complete");
      expect(benignResult.findings.map(f => f.rule_id)).not.toContain("business.bulk_arithmetic_124");
    }
  } finally {
    if (resetRequired) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data:{}});
      expect(reset.status()).toBe(200);
    }
  }
});
