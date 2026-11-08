// Live authenticated browser acceptance: reference-backed identity/FX settings
// stay private while ordinary Field Comparison rules remain customer-operable.
const {test, expect} = require("@playwright/test");

test("public identity and conversion editors preserve hidden reference definitions", async ({page, request}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo9");
  await page.getByRole("button", {name:"Get demo API key",exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo9");
  const headers = {Authorization:"Bearer " + await page.locator("#demo-key-value").inputValue()};
  const summary = async () => {
    const res = await request.get("/v1/applications/current",{headers});
    expect(res.status()).toBe(200);
    return (await res.json()).tenant;
  };
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset",{headers,data:{}});
    expect(reset.status()).toBe(200);
    resetRequired = true;
    const initial = await summary();
    const plain = await request.post("/v1/tenants/current/checks",{headers,data:{
      revision:initial.revision, changes:{
        comparisons:[],input_check_cross_field:"block",output_check_cross_field:"flag"
      }
    }});
    expect(plain.status(),(await plain.text()).slice(0,150)).toBe(200);
    const current = await summary();
    const references = await request.post("/v1/tenants/current/references",{headers,data:{
      revision:current.revision, changes:{
        advanced_identity:[{rule_id:"private_lot",kind:"quarantine_exclusion",
          fields:{requested_lot:"/lot"},trusted_values:["SYNTHETIC_PRIVATE_LOT"],
          stages:["input","output"]}],
        advanced_conversion_time:[{rule_id:"private_fx",kind:"currency_conversion_cap",
          fields:{payment_amount:"/payment",payment_currency:"/payment_ccy",
            invoice_amount:"/invoice",invoice_currency:"/invoice_ccy"},
          source_currency:"EUR",destination_currency:"USD",trusted_rate_numerator:6,
          trusted_rate_denominator:5,rate_reference:"SYNTHETIC-PRIVATE-FX",
          rate_asof:"2026-11-01T09:00:00Z",stages:["input","output"]}]
      }
    }});
    expect(references.status(),(await references.text()).slice(0,180)).toBe(200);
    const protectedState = await summary();
    expect(protectedState.advanced_identity_reference_status.map(x=>x.rule_id)).toEqual(["private_lot"]);
    expect(protectedState.advanced_conversion_reference_status.map(x=>x.rule_id)).toEqual(["private_fx"]);
    expect(JSON.stringify(protectedState)).not.toContain("SYNTHETIC_PRIVATE_LOT");
    expect(JSON.stringify(protectedState)).not.toContain("SYNTHETIC-PRIVATE-FX");
    await page.reload();
    await expect(page.locator("#identity-reference-status")).toContainText("private_lot");
    await expect(page.locator("#conversion-reference-status")).toContainText("private_fx");
    await expect(page.locator("#identity-apply")).toBeEnabled();
    await expect(page.locator("#conversion-apply")).toBeEnabled();

    await page.locator("#identity-id").fill("ui_order");
    await page.locator("#identity-field-order_id").fill("/order_id");
    await page.locator("#identity-field-shipment_order_id").fill("/shipment_order_id");
    await page.locator("#identity-apply").click();
    await page.locator("#conversion-id").fill("ui_load");
    await page.locator("#conversion-field-load").fill("/load");
    await page.locator("#conversion-field-load_unit").fill("/load_unit");
    await page.locator("#conversion-field-max_kg").fill("/max_kg");
    await page.locator("#conversion-apply").click();
    await expect(page.locator('[data-identity-id="ui_order"]')).toHaveCount(1);
    await expect(page.locator('[data-conversion-id="ui_load"]')).toHaveCount(1);
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const saved = await summary();
    expect(saved.revision).toBe(protectedState.revision + 1);
    expect(saved.advanced_identity.map(x=>x.rule_id)).toEqual(["ui_order"]);
    expect(saved.advanced_conversion_time.map(x=>x.rule_id)).toEqual(["ui_load"]);
    expect(saved.advanced_identity_reference_status.map(x=>x.rule_id)).toEqual(["private_lot"]);
    expect(saved.advanced_conversion_reference_status.map(x=>x.rule_id)).toEqual(["private_fx"]);
    expect(JSON.stringify(saved)).not.toContain("SYNTHETIC_PRIVATE_LOT");
    expect(JSON.stringify(saved)).not.toContain("SYNTHETIC-PRIVATE-FX");

    for (const [stage,action] of [["input","block"],["output","flag"]]) {
      const res = await request.post("/v1/checks/"+stage,{headers,data:{
        application_id:"demo9",content_format:"json",content:JSON.stringify({
          lot:"ALLOWED_LOT",order_id:"O1",shipment_order_id:"O2",
          load:1500,load_unit:"kg",max_kg:1000,
          payment:10,payment_ccy:"EUR",invoice:12,invoice_ccy:"USD"
        })
      }});
      expect(res.status()).toBe(200);
      const output = await res.json();
      expect(output.evaluation_status).toBe("complete");
      expect(output.effective_action).toBe(action);
      const ids=output.findings.map(f=>f.rule_id);
      expect(ids).toEqual(expect.arrayContaining(["business.ui_order","business.ui_load"]));
      expect(ids).not.toContain("business.private_lot");
      expect(ids).not.toContain("business.private_fx");
    }

    await page.getByRole("button",{name:"Remove identity ui_order"}).click();
    await page.getByRole("button",{name:"Remove conversion ui_load"}).click();
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const removed = await summary();
    expect(removed.advanced_identity).toEqual([]);
    expect(removed.advanced_conversion_time).toEqual([]);
    expect(removed.advanced_identity_reference_status.map(x=>x.rule_id)).toEqual(["private_lot"]);
    expect(removed.advanced_conversion_reference_status.map(x=>x.rule_id)).toEqual(["private_fx"]);
  } finally {
    if (resetRequired) {
      const cleanup=await request.post("/v1/tenants/current/reset",{headers,data:{}});
      expect(cleanup.status()).toBe(200);
    }
  }
});
