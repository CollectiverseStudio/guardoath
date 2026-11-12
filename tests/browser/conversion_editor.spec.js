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


test("conversion bulk sample fills JSON only, then explicitly stages and saves two kinds for both Engine stages", async ({page, request}) => {
  await page.goto("/dashboard");
  await page.locator("#comparison-configuration").evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button", {name:"Get demo API key",exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  let headers = {Authorization:"Bearer " + await page.locator("#demo-key-value").inputValue()};
  const current = async () => {
    const response = await request.get("/v1/applications/current",{headers});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  };
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset",{headers,data:{}});
    expect(reset.status()).toBe(200); resetRequired = true;
    await page.reload();
    await page.locator("#comparison-configuration").evaluate(el => { el.open = true; });
    await page.locator("#demo-user").selectOption("demo4");
    await page.getByRole("button",{name:"Get demo API key",exact:true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
    headers = {Authorization:"Bearer " + await page.locator("#demo-key-value").inputValue()};
    const before = await current();
    expect(before.advanced_conversion_time).toEqual([]);

    await page.locator("#conversion-bulk-panel").evaluate(el => { el.open = true; });
    await page.locator("#conversion-bulk-sample").click();
    const example = JSON.parse(await page.locator("#conversion-bulk-json").inputValue());
    expect(example).toEqual([
      {rule_id:"example_cargo_kg_cap",kind:"unit_load_kg_cap",
        fields:{load:"/shipment/load",load_unit:"/shipment/load_unit",
          max_kg:"/shipment/max_kg"},stages:["input","output"]},
      {rule_id:"example_cold_chain_duration",kind:"cold_chain_excursion",
        fields:{excursion_start:"/shipment/excursion/start",
          excursion_end:"/shipment/excursion/end",
          allowed_minutes:"/shipment/excursion/allowed_minutes"},
        stages:["input","output"]}
    ]);
    await expect(page.locator("#conversion-bulk-status")).toContainText("No draft or tenant change");
    await expect(page.locator("#conversion-bulk-confirm")).not.toBeChecked();
    await expect(page.locator("#conversion-draft-summary")).toContainText("No pending changes");
    expect((await current()).revision).toBe(before.revision);
    await page.locator("#conversion-bulk-import").click();
    await expect(page.locator("#conversion-bulk-status")).toContainText("Confirm replacement");
    expect((await current()).advanced_conversion_time).toEqual([]);
    await page.locator("#conversion-bulk-confirm").check();
    await page.locator("#conversion-bulk-import").click();
    await expect(page.locator("#conversion-draft-summary")).toContainText("2 added, 0 updated, 0 removed");
    expect((await current()).revision).toBe(before.revision);
    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("flag");
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const saved = await current();
    expect(saved.revision).toBe(before.revision+1);
    expect(saved.advanced_conversion_time).toHaveLength(2);
    for (const [i,definition] of example.entries())
      expect(saved.advanced_conversion_time[i]).toMatchObject(definition);

    const good = {shipment:{load:1000,load_unit:"kg",max_kg:1000,
      excursion:{start:"2026-01-01T10:00:00Z",end:"2026-01-01T10:20:00Z",allowed_minutes:30}}};
    const bad = structuredClone(good);
    bad.shipment.load = 1500;
    bad.shipment.excursion.end = "2026-01-01T10:45:00Z";
    for (const stage of ["input","output"]) {
      for (const [payload, expected] of [
        [bad, ["business.example_cargo_kg_cap","business.example_cold_chain_duration"]],
        [good, []]
      ]) {
        const response = await request.post("/v1/checks/"+stage,{headers,data:{
          application_id:"demo4",content_format:"json",content:JSON.stringify(payload)
        }});
        expect(response.status()).toBe(200);
        const result = await response.json();
        expect(result.evaluation_status).toBe("complete");
        const found = result.findings.filter(f=>f.rule_id.startsWith("business.example_"))
          .map(f=>f.rule_id).sort();
        expect(found).toEqual(expected.slice().sort());
        if (expected.length) {
          expect(result.effective_action).toBe(stage==="input"?"block":"flag");
        } else if (result.effective_action!=="allow") {
          expect(result.findings.some(f=>!f.rule_id.startsWith("business.example_")),
            "Any global action for benign conversion data requires another finding.").toBe(true);
        }
      }
    }
  } finally {
    if (resetRequired) {
      const reset = await request.post("/v1/tenants/current/reset",{headers,data:{}});
      expect(reset.status()).toBe(200);
    }
  }
});

test("conversion customer bulk stages exactly 500 public rules and rejects 501 atomically", async ({page, request}) => {
  await page.goto("/dashboard");
  await page.locator("#comparison-configuration").evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button",{name:"Get demo API key",exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  let headers = {Authorization:"Bearer " + await page.locator("#demo-key-value").inputValue()};
  const current = async () => {
    const response = await request.get("/v1/applications/current",{headers});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  };
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset",{headers,data:{}});
    expect(reset.status()).toBe(200); resetRequired = true;
    await page.reload();
    await page.locator("#comparison-configuration").evaluate(el => { el.open = true; });
    await page.locator("#demo-user").selectOption("demo4");
    await page.getByRole("button",{name:"Get demo API key",exact:true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
    headers = {Authorization:"Bearer " + await page.locator("#demo-key-value").inputValue()};
    const before = await current();
    expect(before.advanced_conversion_time).toEqual([]);
    const rules = Array.from({length:500}, (_,i) => {
      const n=String(i).padStart(3,"0");
      return {rule_id:"max_conversion_"+n,kind:"unit_load_kg_cap",
        fields:{load:"/c"+n+"/load",load_unit:"/c"+n+"/load_unit",
          max_kg:"/c"+n+"/max_kg"},stages:["input","output"]};
    });
    await page.locator("#conversion-bulk-panel").evaluate(el => { el.open = true; });
    const json = page.locator("#conversion-bulk-json");
    await json.fill(JSON.stringify(rules));
    await page.locator("#conversion-bulk-confirm").check();
    await page.locator("#conversion-bulk-import").click();
    await expect(page.locator("#conversion-draft-summary")).toContainText("500 added, 0 updated, 0 removed");
    await expect(page.locator("#conversion-filter-status")).toContainText("Showing 500 of 500");
    expect((await current()).revision).toBe(before.revision);
    expect((await current()).advanced_conversion_time).toEqual([]);
    await json.fill(JSON.stringify([...rules,{
      rule_id:"max_conversion_500",kind:"unit_load_kg_cap",
      fields:{load:"/overflow/load",load_unit:"/overflow/load_unit",
        max_kg:"/overflow/max_kg"},stages:["input","output"]
    }]));
    await page.locator("#conversion-bulk-confirm").check();
    await page.locator("#conversion-bulk-import").click();
    await expect(page.locator("#conversion-bulk-status")).toContainText("At most 500 conversion rules");
    await expect(page.locator("#conversion-draft-summary")).toContainText("500 added, 0 updated, 0 removed");
    expect((await current()).revision).toBe(before.revision);
    await page.locator("#conversion-bulk-export").click();
    expect(JSON.parse(await json.inputValue())).toEqual(rules);

    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("flag");
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const saved = await current();
    expect(saved.revision).toBe(before.revision+1);
    expect(saved.advanced_conversion_time).toHaveLength(500);
    for (const i of [0,499]) expect(saved.advanced_conversion_time[i]).toMatchObject(rules[i]);
    await expect(page.locator("#conversion-draft-summary"))
      .toContainText("Saved conversion/time rules: 500. No pending changes.");
    await expect(page.locator('tr.rule-row[data-rule^="business.max_conversion_"]'))
      .toHaveCount(500);

    const good = {};
    for (let i=0;i<500;i++) {
      const n=String(i).padStart(3,"0");
      good["c"+n] = {load:1,load_unit:"kg",max_kg:2};
    }
    const bad = structuredClone(good);
    bad.c000.load=3; bad.c499.load=3;
    expect(JSON.stringify(bad).length).toBeLessThan(32000);
    for (const stage of ["input","output"]) {
      for (const [payload,expected] of [
        [bad,["business.max_conversion_000","business.max_conversion_499"]],
        [good,[]]
      ]) {
        const response = await request.post("/v1/checks/"+stage,{headers,data:{
          application_id:"demo4",content_format:"json",content:JSON.stringify(payload)
        }});
        expect(response.status()).toBe(200);
        const result=await response.json();
        expect(result.evaluation_status).toBe("complete");
        expect(result.findings.filter(f=>f.rule_id.startsWith("business.max_conversion_"))
          .map(f=>f.rule_id).sort()).toEqual(expected.slice().sort());
        if (expected.length) {
          expect(result.effective_action).toBe(stage==="input"?"block":"flag");
        } else if (result.effective_action!=="allow") {
          expect(result.findings.some(f=>!f.rule_id.startsWith("business.max_conversion_")),
            "An independent finding must explain any global action.").toBe(true);
        }
      }
    }
    expect((await current()).revision).toBe(saved.revision);
  } finally {
    if (resetRequired) {
      const reset = await request.post("/v1/tenants/current/reset",{headers,data:{}});
      expect(reset.status()).toBe(200);
    }
  }
});
