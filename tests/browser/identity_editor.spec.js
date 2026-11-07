const {test,expect} = require("@playwright/test");

test("identity editor saves, reloads, tests and removes an order match rule", async ({page}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo6");
  await page.getByRole("button", {name:"Get demo API key",exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo6");
  const reset = async () => {
    const response = page.waitForResponse(r =>
      r.url().endsWith("/v1/tenants/current/reset") && r.request().method() === "POST");
    await page.getByRole("button", {name:"Reset to defaults",exact:true}).click();
    expect((await response).ok()).toBe(true);
    await expect(page.locator("#tenant-message")).toContainText("Reset to the demo defaults.");
  };
  await reset();
  const token = await page.locator("#demo-key-value").inputValue();
  const read = async () => {
    const response = await page.request.get("/v1/applications/current",{
      headers:{Authorization:"Bearer "+token}
    });
    expect(response.ok()).toBe(true);
    return (await response.json()).tenant;
  };
  await expect(page.locator("#identity-form")).toBeVisible();
  await expect(page.locator("#identity-reference-status")).toContainText("No reference-backed");
  await page.locator("#identity-id").fill("ui_order_match");
  await page.locator("#identity-kind").selectOption("order_id_match");
  await page.locator("#identity-field-order_id").fill("/order_id");
  await page.locator("#identity-field-shipment_order_id").fill("/shipment_order_id");
  await page.locator("#identity-apply").click();
  await expect(page.locator('[data-identity-id="ui_order_match"]')).toHaveCount(1);
  expect((await read()).advanced_identity).toEqual([]);
  await page.locator("#tenant-check-input-cross_field").selectOption("block");
  await page.locator("#tenant-check-output-cross_field").selectOption("flag");
  await page.locator("#tenant-save").click();
  await expect(page.locator("#tenant-message")).toContainText("Saved");
  let saved = await read();
  expect(saved.advanced_identity).toEqual([{
    rule_id:"ui_order_match",kind:"order_id_match",
    fields:{order_id:"/order_id",shipment_order_id:"/shipment_order_id"},
    trusted_values:[],stages:["input","output"]
  }]);
  await page.locator("#rule-search").fill("ui_order_match");
  await expect(page.locator('tr.rule-row[data-rule="business.ui_order_match"]')).toBeVisible();
  await page.locator("#rule-search").fill("");

  await page.getByRole("button",{name:"Try violating input identity ui_order_match"}).click();
  const payload = JSON.parse(await page.locator("#input-content").inputValue());
  expect(payload.order_id).not.toBe(payload.shipment_order_id);
  await expect(page.locator("#input-format")).toHaveValue("json");
  const response = page.waitForResponse(r => r.url().endsWith("/v1/checks/input") &&
    r.request().method() === "POST");
  await page.locator("#run-input").click();
  const result = await (await response).json();
  expect(result.findings.map(f=>f.rule_id)).toContain("business.ui_order_match");
  expect(result.effective_action).toBe("block");

  await page.getByRole("button",{name:"Try benign input identity ui_order_match"}).click();
  const benignResponse = page.waitForResponse(r => r.url().endsWith("/v1/checks/input") &&
    r.request().method() === "POST");
  await page.locator("#run-input").click();
  const benign = await (await benignResponse).json();
  expect(benign.findings.map(f=>f.rule_id)).not.toContain("business.ui_order_match");

  await page.getByRole("button",{name:"Edit identity ui_order_match"}).click();
  await page.locator("#identity-stage-output").uncheck();
  await page.locator("#identity-apply").click();
  await page.locator("#tenant-check-output-cross_field").selectOption("");
  await page.locator("#tenant-save").click();
  await expect(page.locator("#tenant-message")).toContainText("Saved");
  saved = await read();
  expect(saved.advanced_identity[0].stages).toEqual(["input"]);
  await page.getByRole("button",{name:"Remove identity ui_order_match"}).click();
  await page.locator("#tenant-check-input-cross_field").selectOption("");
  await page.locator("#tenant-save").click();
  await expect(page.locator("#tenant-message")).toContainText("Saved");
  expect((await read()).advanced_identity).toEqual([]);
  await reset();
});

test("identity form rejects ambiguous or positional pointers and empty stage without saving", async ({page}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo6");
  await page.getByRole("button",{name:"Get demo API key",exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo6");
  await page.getByRole("button",{name:"Reset to defaults",exact:true}).click();
  await expect(page.locator("#tenant-message")).toContainText("Reset to the demo defaults.");
  await page.locator("#identity-id").fill("invalid_identity");
  await page.locator("#identity-field-order_id").fill("/same");
  await page.locator("#identity-field-shipment_order_id").fill("/same/child");
  await page.locator("#identity-apply").click();
  await expect(page.locator("#identity-message")).toContainText("nonoverlapping");
  await expect(page.locator('[data-identity-id="invalid_identity"]')).toHaveCount(0);
  await page.locator("#identity-field-shipment_order_id").fill("/records/0");
  await page.locator("#identity-apply").click();
  await expect(page.locator("#identity-message")).toContainText("named object");
  await page.locator("#identity-field-shipment_order_id").fill("/shipment");
  await page.locator("#identity-stage-input").uncheck();
  await page.locator("#identity-stage-output").uncheck();
  await page.locator("#identity-apply").click();
  await expect(page.locator("#identity-message")).toContainText("Choose Input");
  await expect(page.locator('[data-identity-id="invalid_identity"]')).toHaveCount(0);
});