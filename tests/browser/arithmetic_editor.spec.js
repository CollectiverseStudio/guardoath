const {test, expect} = require("@playwright/test");

test("FC arithmetic editor creates, saves, reloads, edits and evaluates an actual tenant rule", async ({page}) => {
  await page.goto("/dashboard");
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

    await page.locator("#input-format").selectOption("json");
    await page.locator("#input-content").fill(JSON.stringify({
      lines:[{quantity:2,unit_price:40},{quantity:1,unit_price:30}],subtotal:100
    }));
    const response = page.waitForResponse(r =>
      r.url().endsWith("/v1/checks/input") && r.request().method() === "POST");
    await page.locator("#run-input").click();
    const result = await (await response).json();
    expect(result.findings.map(f=>f.rule_id)).toContain("business.ui_invoice_math");
    expect(result.effective_action).toBe("block");
    await expect(page.locator("#answer")).toBeHidden();

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

  await page.locator("#arithmetic-field-base").fill("/records/0");
  await page.locator("#arithmetic-apply").click();
  await expect(page.locator("#arithmetic-message")).toContainText("not array positions");
  await expect(page.locator('#arithmetic-drafts [data-arithmetic-id="bad_rule"]')).toHaveCount(0);

  await page.locator("#arithmetic-field-base").fill("/base");
  await page.locator("#arithmetic-stage-input").uncheck();
  await page.locator("#arithmetic-stage-output").uncheck();
  await page.locator("#arithmetic-apply").click();
  await expect(page.locator("#arithmetic-message")).toContainText("Choose Input, Output or both");
  await expect(page.locator('#arithmetic-drafts [data-arithmetic-id="bad_rule"]')).toHaveCount(0);
});