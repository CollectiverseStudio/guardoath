const {test,expect} = require("@playwright/test");

test("conversion/time editor saves a mass rule, runs bad and benign examples, and removes it", async ({page}) => {
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button",{name:"Get demo API key",exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  const reset = async () => {
    const response = page.waitForResponse(r =>
      r.url().endsWith("/v1/tenants/current/reset") && r.request().method() === "POST");
    await page.getByRole("button",{name:"Reset to defaults",exact:true}).click();
    expect((await response).ok()).toBe(true);
    await expect(page.locator("#tenant-message")).toContainText("Reset to the demo defaults.");
  };
  await reset();
  const bearer = "Bearer " + await page.locator("#demo-key-value").inputValue();
  const read = async () => {
    const res = await page.request.get("/v1/applications/current",{headers:{Authorization:bearer}});
    expect(res.ok()).toBe(true);
    return (await res.json()).tenant;
  };
  await expect(page.locator("#conversion-form")).toBeVisible();
  await expect(page.locator("#conversion-reference-status")).toContainText("No FX reference-backed");
  await page.locator("#conversion-id").fill("ui_mass");
  await page.locator("#conversion-kind").selectOption("unit_load_kg_cap");
  await page.locator("#conversion-field-load").fill("/load");
  await page.locator("#conversion-field-load_unit").fill("/load_unit");
  await page.locator("#conversion-field-max_kg").fill("/max_kg");
  await page.locator("#conversion-apply").click();
  await expect(page.locator('[data-conversion-id="ui_mass"]')).toHaveCount(1);
  expect((await read()).advanced_conversion_time).toEqual([]);
  await page.locator("#tenant-check-input-cross_field").selectOption("block");
  await page.locator("#tenant-check-output-cross_field").selectOption("flag");
  await page.locator("#tenant-save").click();
  await expect(page.locator("#tenant-message")).toContainText("Saved");
  let rules = (await read()).advanced_conversion_time;
  expect(rules).toHaveLength(1);
  expect(rules[0]).toMatchObject({
    rule_id:"ui_mass",kind:"unit_load_kg_cap",
    fields:{load:"/load",load_unit:"/load_unit",max_kg:"/max_kg"},
    stages:["input","output"]
  });
  await page.locator("#rule-search").fill("ui_mass");
  await expect(page.locator('tr.rule-row[data-rule="business.ui_mass"]')).toBeVisible();
  await page.locator("#rule-search").fill("");

  await page.getByRole("button",{name:"Try violating input conversion ui_mass"}).click();
  await expect(page.locator("#input-format")).toHaveValue("json");
  const bad=JSON.parse(await page.locator("#input-content").inputValue());
  expect(bad.load).toBe(1500);
  const response=page.waitForResponse(r => r.url().endsWith("/v1/checks/input") &&
    r.request().method()==="POST");
  await page.locator("#run-input").click();
  const result=await (await response).json();
  expect(result.findings.map(f=>f.rule_id)).toContain("business.ui_mass");
  expect(result.effective_action).toBe("block");

  await page.getByRole("button",{name:"Try benign input conversion ui_mass"}).click();
  const benignResponse=page.waitForResponse(r => r.url().endsWith("/v1/checks/input") &&
    r.request().method()==="POST");
  await page.locator("#run-input").click();
  const benign=await (await benignResponse).json();
  expect(benign.findings.map(f=>f.rule_id)).not.toContain("business.ui_mass");

  await page.getByRole("button",{name:"Edit conversion ui_mass"}).click();
  await page.locator("#conversion-stage-output").uncheck();
  await page.locator("#conversion-apply").click();
  await page.locator("#tenant-check-output-cross_field").selectOption("");
  await page.locator("#tenant-save").click();
  await expect(page.locator("#tenant-message")).toContainText("Saved");
  rules=(await read()).advanced_conversion_time;
  expect(rules[0].stages).toEqual(["input"]);
  await page.getByRole("button",{name:"Remove conversion ui_mass"}).click();
  await page.locator("#tenant-check-input-cross_field").selectOption("");
  await page.locator("#tenant-save").click();
  await expect(page.locator("#tenant-message")).toContainText("Saved");
  expect((await read()).advanced_conversion_time).toEqual([]);
  await reset();
});

test("conversion editor blocks overlapping or empty paths and absent stages",async ({page})=>{
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button",{name:"Get demo API key",exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  await page.getByRole("button",{name:"Reset to defaults",exact:true}).click();
  await expect(page.locator("#tenant-message")).toContainText("Reset to the demo defaults.");
  await page.locator("#conversion-id").fill("bad_conversion");
  await page.locator("#conversion-field-load").fill("/same");
  await page.locator("#conversion-field-load_unit").fill("/same/sub");
  await page.locator("#conversion-field-max_kg").fill("/max");
  await page.locator("#conversion-apply").click();
  await expect(page.locator("#conversion-message")).toContainText("nonoverlapping");
  await page.locator("#conversion-field-load_unit").fill("/item//unit");
  await page.locator("#conversion-apply").click();
  await expect(page.locator("#conversion-message")).toContainText("nonempty segments");
  await page.locator("#conversion-field-load_unit").fill("/unit");
  await page.locator("#conversion-stage-input").uncheck();
  await page.locator("#conversion-stage-output").uncheck();
  await page.locator("#conversion-apply").click();
  await expect(page.locator("#conversion-message")).toContainText("Choose Input");
  await expect(page.locator('[data-conversion-id="bad_conversion"]')).toHaveCount(0);
});

test("conversion editor keeps private FX hidden while allowing public rule drafts",async ({page})=>{
  // API data is already protected in Python tenant tests; this replaces only the
  // reference status metadata to exercise the browser's fail-closed presentation.
  await page.route("**/v1/applications/current",async route=>{
    const response=await route.fetch();
    const body=await response.json();
    if(body.tenant) body.tenant.advanced_conversion_reference_status=[{
      rule_id:"signed_fx_test",kind:"currency_conversion_cap",
      stages:["input","output"],reference_configured:true
    }];
    await route.fulfill({response,json:body});
  });
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button",{name:"Get demo API key",exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  await expect(page.locator("#conversion-reference-status")).toContainText("signed_fx_test");
  await expect(page.locator("#conversion-reference-status")).toContainText("read-only");
  await expect(page.locator("#conversion-apply")).toBeEnabled();
  await expect(page.locator("#conversion-id")).toBeEnabled();
  await expect(page.locator("#conversion-field-load")).toBeEnabled();
  await expect(page.locator("#conversion-editor")).not.toContainText("trusted_rate_numerator");
  await expect(page.locator("#conversion-editor")).not.toContainText("rate_asof");
});


test("conversion bulk stages 125 public rules atomically, saves revision and runs shared Engine on Input and Output", async ({page, request}) => {
  await page.goto("/dashboard");
  await page.locator("#comparison-configuration").evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button", {name:"Get demo API key", exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  let headers = {Authorization:"Bearer " + await page.locator("#demo-key-value").inputValue()};
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
    await page.locator("#demo-user").selectOption("demo4");
    await page.getByRole("button", {name:"Get demo API key", exact:true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
    headers = {Authorization:"Bearer " + await page.locator("#demo-key-value").inputValue()};
    const initial = await read();
    expect(initial.advanced_conversion_time).toEqual([]);

    const rules = Array.from({length:125}, (_, i) => {
      const index = String(i).padStart(3,"0");
      return {rule_id:"bulk_conversion_" + index, kind:"unit_load_kg_cap",
        fields:{load:"/c" + index + "/load", load_unit:"/c" + index + "/load_unit",
          max_kg:"/c" + index + "/max_kg"}, stages:["input","output"]};
    });
    await page.locator("#conversion-bulk-panel").evaluate(el => { el.open = true; });
    const json = page.locator("#conversion-bulk-json");
    await json.fill(JSON.stringify(rules));
    await page.locator("#conversion-bulk-import").click();
    await expect(page.locator("#conversion-bulk-status")).toContainText("Confirm replacement");
    await expect(page.locator("#conversion-draft-summary")).toContainText("No pending changes");
    await page.locator("#conversion-bulk-confirm").check();
    await page.locator("#conversion-bulk-import").click();
    await expect(page.locator("#conversion-draft-summary")).toContainText("125 added, 0 updated, 0 removed");
    await expect(page.locator("#conversion-filter-status")).toContainText("Showing 125 of 125");
    expect((await read()).revision).toBe(initial.revision);
    expect((await read()).advanced_conversion_time).toEqual([]);

    // One malformed pointer or forged FX rate is an atomic rejection.
    for (const invalid of [
      (() => { const broken = structuredClone(rules); broken[7].fields.max_kg =
        broken[7].fields.load; return broken; })(),
      [...rules, {rule_id:"forged_fx", kind:"currency_conversion_cap",
        fields:{trusted_rate_numerator:"/secret"}, stages:["input"]}],
      [...rules, {rule_id:"invalid_lifecycle", kind:"lifecycle_chronology",
        fields:{order_at:"/order_at", shipment_at:"/shipment_at",
          delivery_at:"/delivery_at", return_at:"/returns/latest/returned_at"},
        return_event_pointer:"/order_at", stages:["input","output"]}]
    ]) {
      await json.fill(JSON.stringify(invalid));
      await page.locator("#conversion-bulk-confirm").check();
      await page.locator("#conversion-bulk-import").click();
      await expect(page.locator("#conversion-bulk-status")).toContainText("Conversion rule ");
      await expect(page.locator("#conversion-draft-summary")).toContainText("125 added, 0 updated, 0 removed");
      expect((await read()).revision).toBe(initial.revision);
    }
    // Valid optional return-event pointer is usable without granting trust to
    // customer-controlled FX rates, and never writes prior to Save checks.
    const lifecycle = {rule_id:"lifecycle_sample", kind:"lifecycle_chronology",
      fields:{order_at:"/order_at", shipment_at:"/shipment_at",
        delivery_at:"/delivery_at", return_at:"/returns/latest/returned_at"},
      return_event_pointer:"/returns/latest", stages:["input","output"]};
    await json.fill(JSON.stringify([lifecycle]));
    await page.locator("#conversion-bulk-confirm").check();
    await page.locator("#conversion-bulk-import").click();
    await expect(page.locator("#conversion-draft-summary")).toContainText("1 added");
    expect((await read()).revision).toBe(initial.revision);
    await json.fill(JSON.stringify(rules));
    await page.locator("#conversion-bulk-confirm").check();
    await page.locator("#conversion-bulk-import").click();
    await page.locator("#conversion-bulk-export").click();
    expect(JSON.parse(await json.inputValue())).toEqual(rules);
    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const saved = await read();
    expect(saved.revision).toBe(initial.revision + 1);
    expect(saved.advanced_conversion_time).toHaveLength(125);
    await expect(page.locator("#conversion-draft-summary"))
      .toContainText("Saved conversion/time rules: 125. No pending changes.");
    await expect(page.locator('tr.rule-row[data-rule^="business.bulk_conversion_"]'))
      .toHaveCount(125);

    const good = {};
    for (let i=0;i<125;i++) good["c"+String(i).padStart(3,"0")] =
      {load:10, load_unit:"kg", max_kg:20};
    const bad = structuredClone(good);
    bad.c124.load = 50;
    for (const stage of ["input","output"]) {
      const violating = await request.post("/v1/checks/"+stage, {headers, data:{
        application_id:"demo4", content_format:"json", content:JSON.stringify(bad)
      }});
      expect(violating.status()).toBe(200);
      const result = await violating.json();
      expect(result.evaluation_status).toBe("complete");
      expect(result.findings.map(f=>f.rule_id)).toContain("business.bulk_conversion_124");
      expect(result.effective_action).toBe("block");
      const benign = await request.post("/v1/checks/"+stage, {headers, data:{
        application_id:"demo4", content_format:"json", content:JSON.stringify(good)
      }});
      expect(benign.status()).toBe(200);
      const clean = await benign.json();
      expect(clean.evaluation_status).toBe("complete");
      expect(clean.findings.map(f=>f.rule_id)).not.toContain("business.bulk_conversion_124");
    }
  } finally {
    if (resetRequired) {
      const response = await request.post("/v1/tenants/current/reset", {headers, data:{}});
      expect(response.status()).toBe(200);
    }
  }
});
