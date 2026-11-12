const {test,expect} = require("@playwright/test");

test("identity editor saves, reloads, tests and removes an order match rule", async ({page}) => {
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
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

test("identity form rejects overlapping or empty pointers and absent stages without saving", async ({page}) => {
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
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
  await page.locator("#identity-field-shipment_order_id").fill("/records//shipment");
  await page.locator("#identity-apply").click();
  await expect(page.locator("#identity-message")).toContainText("nonempty segments");
  await page.locator("#identity-field-shipment_order_id").fill("/shipment");
  await page.locator("#identity-stage-input").uncheck();
  await page.locator("#identity-stage-output").uncheck();
  await page.locator("#identity-apply").click();
  await expect(page.locator("#identity-message")).toContainText("Choose Input");
  await expect(page.locator('[data-identity-id="invalid_identity"]')).toHaveCount(0);
});

test("FC-048 empty requested-scopes use real tenant and authenticated Input/Output dashboard without leaking reference values", async ({page, request}) => {
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo6");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo6");
  const headers = {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
  const summary = async () => {
    const res = await request.get("/v1/applications/current", {headers});
    expect(res.status()).toBe(200);
    return (await res.json()).tenant;
  };
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    resetRequired = true;
    const prior = await summary();
    // A demo reset includes unrelated ordinary comparisons; remove those via
    // the ordinary settings route first. The protected scope set can only be
    // provisioned through its separate authenticated owner reference route.
    const cleared = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: prior.revision, changes: {comparisons: []}
    }});
    expect(cleared.status(), (await cleared.text()).slice(0, 200)).toBe(200);
    const isolated = await summary();
    expect(isolated.revision).toBe(prior.revision + 1);
    expect(isolated.comparisons).toEqual([]);
    const saved = await request.post("/v1/tenants/current/references", {headers, data: {
      revision: isolated.revision, changes: {
        advanced_identity: [{
          rule_id: "browser_scope_zero", kind: "authorized_scope_subset",
          fields: {requested_scopes: "/scopes"}, trusted_values: ["private:authorized"],
          stages: ["input", "output"]
        }],
        input_check_cross_field: "block", output_check_cross_field: "flag",
      }
    }});
    expect(saved.status(), (await saved.text()).slice(0, 200)).toBe(200);
    const live = await summary();
    expect(live.revision).toBe(isolated.revision + 1);
    // Only a status view belongs in the customer dashboard. The tenant's
    // trusted scope list must never leak to the public settings response.
    expect(live.advanced_identity).toEqual([]);
    expect(live.advanced_identity_reference_status).toEqual([{
      rule_id: "browser_scope_zero", kind: "authorized_scope_subset",
      stages: ["input", "output"], reference_configured: true
    }]);
    expect(JSON.stringify(live)).not.toContain("private:authorized");

    await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
    await page.locator("#demo-user").selectOption("demo6");
    await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo6");
    await expect(page.locator("#identity-reference-status")).toContainText("browser_scope_zero");
    await expect(page.locator("#identity-reference-status")).toContainText("trusted reference configured: yes");
    await expect(page.locator("#identity-reference-status")).not.toContainText("private:authorized");

    const noPermissions = JSON.stringify({scopes: []});
    await page.locator("#input-format").selectOption("json");
    await page.locator("#input-content").fill(noPermissions);
    const inputResponse = page.waitForResponse(res =>
      res.url().endsWith("/v1/checks/input") && res.request().method() === "POST");
    await page.locator("#run-input").click();
    const input = await (await inputResponse).json();
    expect(input.evaluation_status).toBe("complete");
    expect(input.findings.map(f => f.rule_id)).not.toContain("business.browser_scope_zero");
    expect(input.effective_action).toBe("allow");

    await page.locator("#output-format").selectOption("json");
    await page.locator("#output-content").fill(noPermissions);
    const outputResponse = page.waitForResponse(res =>
      res.url().endsWith("/v1/checks/output") && res.request().method() === "POST");
    await page.locator("#run-output").click();
    const output = await (await outputResponse).json();
    expect(output.evaluation_status).toBe("complete");
    expect(output.findings.map(f => f.rule_id)).not.toContain("business.browser_scope_zero");
    expect(output.effective_action).toBe("allow");

    for (const stage of ["input", "output"]) {
      for (const payload of [{}, {scopes: null}, {scopes: ["untrusted:scope"]}]) {
        const response = await request.post("/v1/checks/" + stage, {headers, data: {
          application_id: "demo6", content_format: "json",
          content: JSON.stringify(payload),
        }});
        expect(response.status()).toBe(200);
        const result = await response.json();
        expect(result.evaluation_status).toBe("complete");
        expect(result.findings.map(f => f.rule_id)).toContain("business.browser_scope_zero");
        expect(result.effective_action).toBe(stage === "input" ? "block" : "flag");
        expect(JSON.stringify(result)).not.toContain("private:authorized");
      }
    }
    expect((await summary()).revision).toBe(live.revision);
  } finally {
    if (resetRequired) {
      const clean = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(clean.status()).toBe(200);
    }
  }
});


test("identity bulk stages 125 public serial rules atomically and runs saved Input Output tenant Engine", async ({page, request}) => {
  await page.goto("/dashboard");
  await page.locator("#comparison-configuration").evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo6");
  await page.getByRole("button", {name:"Get demo API key", exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo6");
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
    await page.locator("#demo-user").selectOption("demo6");
    await page.getByRole("button", {name:"Get demo API key", exact:true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo6");
    headers = {Authorization:"Bearer " + await page.locator("#demo-key-value").inputValue()};
    const initial = await read();
    expect(initial.advanced_identity).toEqual([]);
    const rules = Array.from({length:125}, (_, i) => {
      const index = String(i).padStart(3,"0");
      return {rule_id:"bulk_identity_" + index, kind:"serial_unique",
        fields:{serials:"/i" + index + "/serials"}, stages:["input","output"]};
    });
    await page.locator("#identity-bulk-panel").evaluate(el => { el.open = true; });
    const json = page.locator("#identity-bulk-json");
    await json.fill(JSON.stringify(rules));
    await page.locator("#identity-bulk-import").click();
    await expect(page.locator("#identity-bulk-status")).toContainText("Confirm replacement");
    await expect(page.locator("#identity-draft-summary")).toContainText("No pending changes");
    await page.locator("#identity-bulk-confirm").check();
    await page.locator("#identity-bulk-import").click();
    await expect(page.locator("#identity-draft-summary")).toContainText("125 added, 0 updated, 0 removed");
    await expect(page.locator("#identity-filter-status")).toContainText("Showing 125 of 125");
    expect((await read()).revision).toBe(initial.revision);
    expect((await read()).advanced_identity).toEqual([]);

    // Any invalid member rejects the entire replacement. No cross-family or
    // private reference rule can be smuggled into this public draft.
    for (const invalid of [
      [...rules, structuredClone(rules[0])],
      [...rules, {rule_id:"forged_quarantine", kind:"quarantine_exclusion",
        fields:{serials:"/secret"}, stages:["input"]}],
      (() => { const broken = structuredClone(rules); broken[7].trusted_values = ["PRIVATE"];
        return broken; })(),
      (() => { const broken = structuredClone(rules); broken[7].fields.serials =
        "/i007//serials"; return broken; })(),
      (() => { const broken = structuredClone(rules); broken[7].stages =
        ["input","input"]; return broken; })()
    ]) {
      await json.fill(JSON.stringify(invalid));
      await page.locator("#identity-bulk-confirm").check();
      await page.locator("#identity-bulk-import").click();
      await expect(page.locator("#identity-bulk-status")).toContainText("Identity rule ");
      await expect(page.locator("#identity-draft-summary"))
        .toContainText("125 added, 0 updated, 0 removed");
      expect((await read()).revision).toBe(initial.revision);
    }
    await json.fill("{broken");
    await page.locator("#identity-bulk-confirm").check();
    await page.locator("#identity-bulk-import").click();
    await expect(page.locator("#identity-bulk-status")).toContainText("Invalid JSON");
    await json.fill(JSON.stringify(rules));
    await page.locator("#identity-bulk-confirm").check();
    await page.locator("#identity-bulk-import").click();
    await page.locator("#identity-bulk-export").click();
    const expected = rules.map(rule => ({
      ...rule, trusted_values:[]
    }));
    expect(JSON.parse(await json.inputValue())).toEqual(expected);

    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const saved = await read();
    expect(saved.revision).toBe(initial.revision + 1);
    expect(saved.advanced_identity).toEqual(expected);
    await expect(page.locator("#identity-draft-summary"))
      .toContainText("Saved identity rules: 125. No pending changes.");
    await expect(page.locator('tr.rule-row[data-rule^="business.bulk_identity_"]'))
      .toHaveCount(125);

    const good = {};
    for (let i=0;i<125;i++) good["i"+String(i).padStart(3,"0")] =
      {serials:["SN"+i+"A","SN"+i+"B"]};
    const bad = structuredClone(good);
    bad.i124.serials = ["REPEATED","REPEATED"];
    for (const stage of ["input","output"]) {
      const violating = await request.post("/v1/checks/"+stage, {headers, data:{
        application_id:"demo6",content_format:"json",content:JSON.stringify(bad)
      }});
      expect(violating.status()).toBe(200);
      const found = await violating.json();
      expect(found.evaluation_status).toBe("complete");
      expect(found.findings.map(f=>f.rule_id)).toContain("business.bulk_identity_124");
      expect(found.effective_action).toBe("block");
      const benign = await request.post("/v1/checks/"+stage, {headers, data:{
        application_id:"demo6",content_format:"json",content:JSON.stringify(good)
      }});
      expect(benign.status()).toBe(200);
      const clean = await benign.json();
      expect(clean.evaluation_status).toBe("complete");
      expect(clean.findings.map(f=>f.rule_id)).not.toContain("business.bulk_identity_124");
    }
  } finally {
    if (resetRequired) {
      const response = await request.post("/v1/tenants/current/reset", {headers, data:{}});
      expect(response.status()).toBe(200);
    }
  }
});
