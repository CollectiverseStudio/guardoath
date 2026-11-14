const {test, expect} = require("@playwright/test");

// Real demo tenant, real dashboard and saved record; do not mock tenant APIs.
test("policy setup wizard shows Input, Output and review without saving until Save checks", async ({page, request}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo3");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo3");
  const token = await page.locator("#demo-key-value").inputValue();
  const current = async () => {
    const response = await request.get("/v1/applications/current",
      {headers: {Authorization: "Bearer " + token}});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  };
  const initial = await current();
  const input = page.locator("#tenant-check-input-lexical_guards");
  const output = page.locator("#tenant-check-output-lexical_guards");
  const initialInput = await input.inputValue();
  const initialOutput = await output.inputValue();
  await expect(page.getByRole("button", {name: "Start setup wizard"})).toBeVisible();
  await page.locator("#checks-wizard-start").click();
  await expect(page.locator("#checks-wizard-progress")).toContainText("Step 1 of 3 · Input protection");
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "input");
  await expect(input).toBeVisible();
  await expect(output).toBeHidden();
  const changedInput = initialInput === "block" ? "flag" : "block";
  await input.selectOption(changedInput);
  await page.locator("#checks-wizard-next").click();
  await expect(page.locator("#checks-wizard-progress")).toContainText("Step 2 of 3 · Output protection");
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "output");
  await expect(input).toBeHidden();
  await expect(output).toBeVisible();
  await expect(page.locator("#tenant-stage-hidden-draft")).toContainText("Unsaved Input");
  expect((await current()).revision).toBe(initial.revision);
  await page.locator("#checks-wizard-next").click();
  await expect(page.locator("#checks-wizard-progress")).toContainText("Step 3 of 3 · Review and save");
  await expect(page.locator("#checks-wizard-next")).toBeHidden();
  await expect(input).toBeVisible();
  await expect(output).toBeVisible();
  await expect(input).toHaveValue(changedInput);
  expect((await current()).revision).toBe(initial.revision);
  // Exit does not save. Restore the original draft without changing the shared demo record.
  await page.locator("#checks-wizard-exit").click();
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "both");
  await expect(page.locator("#checks-wizard-start")).toBeVisible();
  await input.selectOption(initialInput);
  await expect(output).toHaveValue(initialOutput);
  expect((await current()).revision).toBe(initial.revision);
});

test("the guided setup appears separately from conversation-testing steps", async ({page}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#checks-setup-wizard")).toBeVisible();
  await expect(page.locator("#conversation-wizard")).toBeVisible();
  await expect(page.locator("#checks-wizard-guidance")).toContainText("Input protection");
  await page.locator("#checks-wizard-start").click();
  await expect(page.locator("#checks-wizard-guidance")).toContainText("BEFORE sending it to AI");
  await page.locator("#checks-wizard-next").click();
  await expect(page.locator("#checks-wizard-guidance")).toContainText("BEFORE returning it to the user");
  await page.locator("#checks-wizard-back").click();
  await expect(page.locator("#checks-wizard-progress")).toContainText("Step 1 of 3");
});


test("switching demo tenants exits active setup and restores both columns", async ({page}) => {
  await page.goto("/dashboard");
  const connect = async tenant => {
    await page.locator("#demo-user").selectOption(tenant);
    await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to " + tenant);
  };
  await connect("demo3");
  await page.locator("#checks-wizard-start").click();
  await page.locator("#checks-wizard-next").click();
  await expect(page.locator("#checks-wizard-progress")).toContainText("Step 2 of 3");
  await connect("demo4");
  await expect(page.locator("#checks-wizard-progress")).toHaveText("Guided setup available");
  await expect(page.locator("#checks-wizard-start")).toBeVisible();
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "both");
  await expect(page.locator("#tenant-check-input-lexical_guards")).toBeVisible();
  await expect(page.locator("#tenant-check-output-lexical_guards")).toBeVisible();
});


test("wizard tracks manual stage tabs and review Save uses the existing revisioned tenant action", async ({page, request}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo3");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo3");
  const token = await page.locator("#demo-key-value").inputValue();
  const current = async () => {
    const response = await request.get("/v1/applications/current",
      {headers: {Authorization: "Bearer " + token}});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant.revision;
  };
  const initialRevision = await current();
  const input = page.locator("#tenant-check-input-lexical_guards");
  const initialInput = await input.inputValue();
  const nextInput = initialInput === "block" ? "flag" : "block";
  try {
    await page.locator("#checks-wizard-start").click();
    await input.selectOption(nextInput);
    await page.locator("#tenant-tab-output").click();
    await expect(page.locator("#checks-wizard-progress")).toContainText("Step 2 of 3 · Output protection");
    await expect(page.locator("#checks-wizard-save")).toBeHidden();
    await expect(page.locator("#tenant-stage-hidden-draft")).toContainText("Unsaved Input");
    await page.locator("#tenant-tab-input").click();
    await expect(page.locator("#checks-wizard-progress")).toContainText("Step 1 of 3 · Input protection");
    await expect(input).toHaveValue(nextInput);
    await page.locator("#tenant-tab-both").click();
    await expect(page.locator("#checks-wizard-progress")).toContainText("Step 3 of 3 · Review and save");
    await expect(page.locator("#checks-wizard-save")).toBeVisible();
    expect(await current()).toBe(initialRevision);
    await page.locator("#checks-wizard-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    expect(await current()).not.toBe(initialRevision);
    await page.reload();
    await page.locator("#demo-user").selectOption("demo3");
    await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
    await expect(page.locator("#tenant-check-input-lexical_guards")).toHaveValue(nextInput);
  } finally {
    await page.locator("#tenant-tab-both").click();
    await page.locator("#tenant-check-input-lexical_guards").selectOption(initialInput);
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
  }
});


test("review reveals current Field Comparisons stage actions and navigates to all existing editors without saving", async ({page, request}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  const token = await page.locator("#demo-key-value").inputValue();
  const revision = async () => {
    const response = await request.get("/v1/applications/current",
      {headers: {Authorization: "Bearer " + token}});
    expect(response.status(), (await response.text()).slice(0, 120)).toBe(200);
    return (await response.json()).tenant.revision;
  };
  const originalRevision = await revision();
  const input = page.locator("#tenant-check-input-cross_field");
  const output = page.locator("#tenant-check-output-cross_field");
  const originalInput = await input.inputValue();
  const originalOutput = await output.inputValue();
  const actionName = action => ({block: "Block", flag: "Flag", allow: "Allow", "": "Off"}[action]);
  try {
    await expect(page.locator("#checks-wizard-comparison-tools")).toBeHidden();
    await page.locator("#checks-wizard-start").click();
    await page.locator("#checks-wizard-next").click();
    await page.locator("#checks-wizard-next").click();
    const summary = page.locator("#checks-wizard-comparison-summary");
    await expect(summary).toContainText("Draft Field comparisons check");
    await expect(summary).toContainText("Input: " + actionName(originalInput));
    await expect(summary).toContainText("Output: " + actionName(originalOutput));
    await input.selectOption("");
    await expect(summary).toContainText("Input: Off");
    await expect(summary).toContainText("Rules do not execute");
    expect(await revision()).toBe(originalRevision);
    await page.locator("#checks-wizard-open-comparisons").click();
    await expect(page.locator("#comparison-configuration")).toHaveJSProperty("open", true);
    for (const editor of ["comparison", "arithmetic", "identity", "conversion"]) {
      await expect(page.locator("#" + editor + "-editor")).toBeVisible();
    }
    await expect(page.locator("#trusted-reference-status")).toHaveAttribute(
      "aria-label", "Host-attested Field Comparison reference status");
    // Navigation and changing a draft value do not publish new check policy.
    expect(await revision()).toBe(originalRevision);
  } finally {
    await page.locator("#tenant-tab-both").click();
    await input.selectOption(originalInput);
    await output.selectOption(originalOutput);
  }
});


test("Input wizard warns about Output-only comparison definitions, not synthetic unchanged rule actions", async ({page, request}) => {
  // Real demo tenant, real pending editor and authoritative API receipt. Never
  // Save: neither the wizard nor a warning may mutate another stage's policy.
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  const token = await page.locator("#demo-key-value").inputValue();
  const current = async () => {
    const response = await request.get("/v1/applications/current",
      {headers: {Authorization: "Bearer " + token}});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  };
  const original = await current();
  // This regression specifically requires an existing saved Output portfolio:
  // appending/reordering Input-only drafts must not mark that Output dirty.
  expect(original.comparisons.some(rule => rule.stages.includes("output"))).toBe(true);
  await page.locator("#comparison-configuration").evaluate(el => { el.open = true; });
  await page.locator("#checks-wizard-start").click();
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "input");
  const warning = page.locator("#tenant-stage-hidden-draft");
  const id = "wizard_stage_warning_rule";
  await page.locator("#comparison-id").fill(id);
  await page.locator("#comparison-left").fill("/example/left");
  await page.locator("#comparison-right").fill("/example/right");
  // A pending Input-only definition has no Output changes; editors may
  // generate unchanged Output rule-actions objects as an implementation detail.
  await page.locator("#comparison-stage-output").uncheck();
  await page.locator("#comparison-apply").click();
  await expect(page.locator('#comparison-drafts [data-comparison-id="' + id + '"]')).toHaveCount(1);
  await expect(warning).toBeHidden();
  expect((await current()).revision).toBe(original.revision);

  // Reordering newly added Input-only rules does not reorder the saved
  // Output membership, even when the shared portfolio has Output rules.
  const secondId = "wizard_stage_warning_input_two";
  await page.locator("#comparison-id").fill(secondId);
  await page.locator("#comparison-left").fill("/example/third");
  await page.locator("#comparison-right").fill("/example/fourth");
  await page.locator("#comparison-stage-output").uncheck();
  await page.locator("#comparison-apply").click();
  await expect(warning).toBeHidden();
  await page.locator("#comparison-bulk-panel summary").click();
  await page.locator("#comparison-bulk-export").click();
  const bulkDraft = page.locator("#comparison-bulk-json");
  const reordered = JSON.parse(await bulkDraft.inputValue());
  expect(reordered.slice(-2).map(rule => rule.rule_id)).toEqual([id, secondId]);
  reordered.splice(-2, 2, reordered[reordered.length - 1], reordered[reordered.length - 2]);
  await bulkDraft.fill(JSON.stringify(reordered));
  await page.locator("#comparison-bulk-confirm").check();
  await page.locator("#comparison-bulk-import").click();
  await expect(page.locator("#comparison-bulk-status")).toContainText("staged");
  await expect(warning).toBeHidden();
  expect((await current()).revision).toBe(original.revision);
  // The order regression has finished. Remove the second Input-only draft
  // before switching to Output so that a real hidden Input edit does not
  // incorrectly make the independent Output-stage assertion expect silence.
  await page.getByRole("button", {name: "Remove comparison " + secondId, exact: true}).click();
  await expect(warning).toBeHidden();

  // Change the *unsaved* rule to Output-only while Output settings are hidden.
  await page.getByRole("button", {name: "Edit comparison " + id, exact: true}).click();
  await page.locator("#comparison-stage-input").uncheck();
  await page.locator("#comparison-stage-output").check();
  await page.locator("#comparison-apply").click();
  await expect(warning).toBeVisible();
  await expect(warning).toContainText("Unsaved Output changes (field comparison definitions)");
  await page.locator("#tenant-tab-output").click();
  await expect(warning).toBeHidden(); // No Input change in the final draft.
  await page.locator("#tenant-tab-input").click();
  await expect(warning).toContainText("Unsaved Output changes (field comparison definitions)");

  // The advanced arithmetic family uses the same tenant-owned stage warning,
  // including its bulk editor; this does not save or create a second engine.
  await page.getByRole("button", {name: "Remove comparison " + id, exact: true}).click();
  await expect(warning).toBeHidden();
  await page.locator("#arithmetic-bulk-panel summary").click();
  await page.locator("#arithmetic-bulk-example").click();
  const bulk = page.locator("#arithmetic-bulk-json");
  const sample = JSON.parse(await bulk.inputValue());
  expect(sample.length).toBeGreaterThan(0);
  await bulk.fill(JSON.stringify(sample.map(rule => ({...rule, stages: ["output"]}))));
  await page.locator("#arithmetic-bulk-confirm").check();
  await page.locator("#arithmetic-bulk-import").click();
  await expect(page.locator("#arithmetic-bulk-status")).toContainText("staged");
  await expect(warning).toContainText("Unsaved Output changes (field comparison definitions)");
  const after = await current();
  expect(after.revision).toBe(original.revision);
  expect(after.comparisons).toEqual(original.comparisons);
  expect(after.advanced_arithmetic).toEqual(original.advanced_arithmetic);
});


test("Input wizard compares effective saved Output rules, not visible-only stage membership or JSON representation", async ({page, request}) => {
  // No mock API: reproduce through a signed-in demo tenant's ordinary editor,
  // its real bulk import and the revisioned application's saved portfolio.
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  const token = await page.locator("#demo-key-value").inputValue();
  const current = async () => {
    const response = await request.get("/v1/applications/current",
      {headers: {Authorization: "Bearer " + token}});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  };
  const saved = await current();
  expect(saved.comparisons.some(rule => rule.stages.includes("output"))).toBe(true);
  await page.locator("#comparison-configuration").evaluate(el => { el.open = true; });
  await page.locator("#checks-wizard-start").click();
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "input");
  await page.locator("#comparison-bulk-panel summary").click();
  await page.locator("#comparison-bulk-export").click();
  const input = page.locator("#comparison-bulk-json");
  const rules = JSON.parse(await input.inputValue());
  const outputRule = rules.find(rule => rule.stages.includes("output"));
  expect(outputRule).toBeTruthy();
  // Preserve the entire Output rule definition. Adding ONLY Input membership
  // to an Output-only rule does not alter Output, nor does reversing a dual-
  // stage rule's ["input","output"] order. A new Input-only rule guarantees
  // a real pending portfolio even for the dual-stage permutation case.
  outputRule.stages = outputRule.stages.includes("input")
    ? [...outputRule.stages].reverse() : ["input", "output"];
  rules.push({rule_id: "wizard_membership_scope_regression", left: "/synthetic/a",
    right: "/synthetic/b", operator: "le", value_type: "number", stages: ["input"]});
  const stageWarning = page.locator("#tenant-stage-hidden-draft");
  await input.fill(JSON.stringify(rules));
  await page.locator("#comparison-bulk-confirm").check();
  await page.locator("#comparison-bulk-import").click();
  await expect(page.locator("#comparison-bulk-status")).toContainText("staged");
  await expect(stageWarning).toBeHidden();
  expect((await current()).revision).toBe(saved.revision);

  // Actual Output definition changes must still warn while Output is hidden.
  outputRule.operator = outputRule.operator === "eq" ? "ne" : "eq";
  await input.fill(JSON.stringify(rules));
  await page.locator("#comparison-bulk-confirm").check();
  await page.locator("#comparison-bulk-import").click();
  await expect(page.locator("#comparison-bulk-status")).toContainText("staged");
  await expect(stageWarning).toContainText("Unsaved Output changes (field comparison definitions)");
  const after = await current();
  expect(after.revision).toBe(saved.revision);
  expect(after.comparisons).toEqual(saved.comparisons);
});


test("wizard uses owner-only PSI draft to avoid false hidden Output warning without exposing references", async ({page, request}) => {
  await page.goto("/dashboard");
  await page.locator("#comparison-configuration").evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo10");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo10");
  await expect(page.locator("#drift-editor-status")).toContainText("Owner-only reference configuration loaded");
  const token = await page.locator("#demo-key-value").inputValue();
  const read = async () => {
    const response = await request.get("/v1/tenants/current/statistical-drift",
      {headers: {Authorization: "Bearer " + token}});
    expect(response.status()).toBe(200);
    return response.json();
  };
  const original = await read();
  await page.locator("#checks-wizard-start").click();
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "input");
  const warning = page.locator("#tenant-stage-hidden-draft");
  await page.locator("#drift-synthetic-preset").click();
  await page.locator("#drift-stage-output").uncheck();
  await page.locator("#drift-apply").click();
  await expect(page.locator('#drift-drafts [data-drift-id="ui_psi_demo"]')).toHaveCount(1);
  await expect(warning).toBeHidden(); // Nothing in the hidden Output PSI portfolio changed.

  await page.getByRole("button", {name: "Edit PSI ui_psi_demo", exact: true}).click();
  await page.locator("#drift-stage-input").uncheck();
  await page.locator("#drift-stage-output").check();
  await page.locator("#drift-apply").click();
  await expect(warning).toContainText("Unsaved Output changes (field comparison definitions)");
  await expect(page.locator("#drift-editor-status")).toContainText("Owner-only reference configuration loaded");
  expect(await read()).toEqual(original); // Real reference and tenant revision remain unchanged.
});
