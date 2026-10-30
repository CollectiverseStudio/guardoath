const { test, expect } = require("@playwright/test");
async function connect(page) {
  await page.goto("/dashboard");
  await page.getByLabel("Application API key").fill("playwright-test-key");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo");
  await page.locator("#advanced > summary").click();
  await page.locator(".batch-run > summary").click();
}
async function profile(page, version) {
  await page.locator("#policy").selectOption(version);
  await page.locator("#input-clean-sample").click();
  await page.locator("#output-clean-sample").click();
}
async function run(page) {
  await page.locator("#run").click();
  await expect(page.locator("#result-status")).not.toHaveText("Checking…");
}
test("clean content shows the answer after real local checks", async ({ page }) => {
  await connect(page); await run(page);
  await expect(page.locator("#result-status")).toHaveText("No findings");
  await expect(page.locator("#answer")).toBeVisible();
  await expect(page.locator("#answer")).toContainText("unused item");
});
test("output email finding holds the answer", async ({ page }) => {
  await connect(page); await page.locator("#output-email-sample").click(); await run(page);
  await expect(page.locator("#output-results")).toContainText("Potential email address");
  await expect(page.locator("#answer")).toBeHidden();
  await expect(page.locator("#answer-status")).toHaveText("Held for review");
});
test("input card finding is shown separately", async ({ page }) => {
  await connect(page); await page.locator("#input-content").fill("Card 4111 1111 1111 1111"); await run(page);
  await expect(page.locator("#input-results")).toContainText("Potential credit-card");
  await expect(page.locator("#answer")).toBeHidden();
});
test("editing content removes a previous result", async ({ page }) => {
  await connect(page); await run(page); await expect(page.locator("#answer")).toBeVisible();
  await page.locator("#output-content").fill("A different answer.");
  await expect(page.locator("#answer")).toBeHidden();
  await expect(page.locator("#answer-status")).toHaveText("Not checked");
});
test("answer markup is displayed as text", async ({ page }) => {
  await connect(page); await page.locator("#output-content").fill('<img src=x onerror="window.injected=true">'); await run(page);
  await expect(page.locator("#answer")).toContainText("<img");
  await expect(page.locator("#answer img")).toHaveCount(0);
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
});
test("key is not stored and disconnect clears results", async ({ page }) => {
  await connect(page); await run(page);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0,0]);
  await expect(page.locator("#api-key")).toHaveValue("");
  await page.getByRole("button", { name:"Disconnect", exact:true }).click();
  await expect(page.locator("#run")).toBeDisabled(); await expect(page.locator("#answer")).toBeHidden();
});
test("an output API failure cannot show a passed answer", async ({ page }) => {
  await connect(page); await page.route("**/v1/checks/output", route => route.fulfill({status:503,body:'{}'})); await run(page);
  await expect(page.locator("#result-status")).toHaveText("Check failed"); await expect(page.locator("#answer")).toBeHidden();
});
test("incomplete checks hold the answer", async ({ page }) => {
  await connect(page); await page.route("**/v1/checks/output", async route => {
    const response = await route.fetch(); const result = await response.json();
    result.evaluation_status = "incomplete"; result.recommended_action = "block";
    result.checker_results[0].status = "error"; result.checker_results[0].error_code = "checker_timeout";
    await route.fulfill({response,json:result});
  }); await run(page);
  await expect(page.locator("#result-status")).toHaveText("Incomplete"); await expect(page.locator("#answer")).toBeHidden();
});
test("simulated checks never look like verified coverage", async ({ page }) => {
  await connect(page); await page.route("**/v1/checks/*", async route => {
    const response = await route.fetch(); const result = await response.json();
    result.simulated = true; result.checker_results[0].simulated = true;
    await route.fulfill({response,json:result});
  }); await run(page);
  await expect(page.locator("#result-status")).toHaveText("Simulated coverage");
  await expect(page.locator("#answer-status")).toHaveText("Unverified preview");
});
test("mobile layout fits the viewport", async ({ page }) => {
  await page.setViewportSize({width:390,height:844}); await connect(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("grouped coverage does not mark planned checks as passed", async ({ page }) => {
  await connect(page); await run(page);
  await expect(page.locator('#output-results [data-group="sensitive_data"] summary')).toContainText("Passed");
  await expect(page.locator('#output-results [data-group="scope"] summary')).toContainText("Not implemented");
  await expect(page.locator('#output-results [data-group="structure"] summary')).toContainText("Disabled");
});

test("business policy reports cross-field failures and holds the preview", async ({ page }) => {
  await connect(page); await profile(page,"business-v1");
  await page.locator("#output-business-sample").click(); await run(page);
  await expect(page.locator('#input-results [data-group="business"] summary')).toContainText("Passed");
  await expect(page.locator('#output-results [data-group="business"] summary')).toContainText("Findings");
  await expect(page.locator("#output-results")).toContainText("date_order");
  await expect(page.locator("#output-results")).toContainText("refund_limit");
  await expect(page.locator("#answer")).toBeHidden();
});

test("invalid date formats appear in structural findings", async ({ page }) => {
  await connect(page); await profile(page,"business-v1");
  await page.locator("#output-business-sample").click();
  await page.locator("#output-content").fill(JSON.stringify({start_date:"2026-02-30",end_date:"2026-10-03",purchase_amount:100,refund_amount:25}));
  await run(page);
  await expect(page.locator('#output-results [data-group="structure"] summary')).toContainText("Findings");
  await expect(page.locator("#answer")).toBeHidden();
});

test("SSN candidates appear under sensitive data", async ({ page }) => {
  await connect(page); await page.locator("#output-ssn-sample").click(); await run(page);
  await expect(page.locator('#output-results [data-group="sensitive_data"]')).toContainText("Social Security number");
  await expect(page.locator("#answer")).toBeHidden();
});

test("redaction shows a rechecked preview without returning detected values", async ({ page }) => {
  await connect(page); await profile(page,"redaction-v1"); await page.locator("#output-redaction-sample").click(); await run(page);
  const preview = page.locator('#output-results .redacted-content');
  await expect(preview).toBeVisible();
  await expect(preview).toContainText("[REDACTED_EMAIL_ADDRESS]");
  await expect(preview).toContainText("[REDACTED_US_SSN]");
  await expect(preview).toContainText("[REDACTED_CREDIT_CARD]");
  await expect(preview).not.toContainText("jane@example.com");
  await expect(page.locator('#output-results .redaction-box')).toContainText("observe mode");
  await expect(page.locator("#answer")).toBeHidden();
});

test("redacted preview uses safe text rendering", async ({ page }) => {
  await connect(page); await profile(page,"redaction-v1"); await page.locator("#output-redaction-sample").click();
  await page.locator("#output-content").fill('<img src=x onerror="window.injected=true"> jane@example.com'); await run(page);
  await expect(page.locator('#output-results .redacted-content')).toContainText("<img");
  await expect(page.locator('#output-results .redacted-content img')).toHaveCount(0);
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
});

test("credential example shows a real secret finding and holds the answer", async ({ page }) => {
  await connect(page); await page.locator("#output-secret-sample").click(); await run(page);
  await expect(page.locator('#output-results [data-group="secrets"] summary')).toContainText("Findings");
  await expect(page.locator('#output-results [data-group="secrets"]')).toContainText("GitHub token pattern");
  await expect(page.locator("#output-results")).toContainText("Recommended: block");
  await expect(page.locator("#answer")).toBeHidden();
});

test("clean text passes the selected secret patterns only", async ({ page }) => {
  await connect(page); await page.locator("#policy").selectOption("secrets-v1"); await run(page);
  await expect(page.locator('#output-results [data-group="secrets"] summary')).toContainText("Passed");
  await expect(page.locator('#output-results [data-group="sensitive_data"] summary')).toContainText("Disabled");
  await expect(page.locator("#answer")).toBeVisible();
});

test("missing required output information holds the answer", async ({ page }) => {
  await connect(page); await profile(page,"completeness-v1"); await page.locator("#output-completeness-sample").click(); await run(page);
  await expect(page.locator('#input-results [data-group="business"] summary')).toContainText("Passed");
  await expect(page.locator('#output-results [data-group="business"] summary')).toContainText("Findings");
  await expect(page.locator("#output-results")).toContainText("next_steps");
  await expect(page.locator("#answer")).toBeHidden();
});
test("adding the required information releases the preview", async ({ page }) => {
  await connect(page); await profile(page,"completeness-v1"); await page.locator("#output-completeness-sample").click();
  await page.locator("#output-content").fill("Summary: Return instructions are available.\nNext steps: Contact the store for instructions.");
  await run(page);
  await expect(page.locator("#result-status")).toHaveText("No findings");
  await expect(page.locator("#answer")).toBeVisible();
  await expect(page.locator('#output-results [data-group="business"]')).toContainText("No assessment of truth");
});

test("hidden characters hold the sequence before output", async ({ page }) => {
  await connect(page); await page.locator("#input-hidden-sample").click(); await run(page);
  await expect(page.locator('#input-results [data-group="prompt_security"] summary')).toContainText("Findings");
  await expect(page.locator("#output-results")).toContainText("Waiting for");
  await expect(page.locator("#run-output")).toBeDisabled();
  await expect(page.locator("#input-results")).toContainText("invisible, bidirectional");
  await expect(page.locator("#answer")).toBeHidden();
});
test("labeled encoded text is held for review without exposing decoded text", async ({ page }) => {
  await connect(page); await page.locator("#input-encoded-sample").click(); await run(page);
  await expect(page.locator("#input-results")).toContainText("One layer was decoded locally");
  await expect(page.locator("#input-results")).not.toContainText("Hello");
  await expect(page.locator("#answer")).toBeHidden();
  await page.locator("#input-content").fill("Explain your return instructions."); await run(page);
  await expect(page.locator("#answer")).toBeVisible();
});

test("dashboard explains shared request allowance", async ({ page }) => {
  await connect(page);
  await expect(page.locator("#resource-limits")).toContainText("checks/minute per application");
  await expect(page.locator("#resource-limits")).toContainText("a full sequence uses two checks");
  await page.getByRole("button", {name:"Disconnect",exact:true}).click();
  await expect(page.locator("#resource-limits")).toBeEmpty();
});
test("rate limiting holds the preview and shows retry guidance", async ({ page }) => {
  await connect(page);
  await page.route("**/v1/checks/input", route => route.fulfill({status:429,headers:{"Retry-After":"17"},body:'{"detail":"private-server-detail"}'}));
  await run(page);
  await expect(page.locator("#result-status")).toHaveText("Check failed");
  await expect(page.locator("#run-error")).toHaveText("Application limit reached. Retry in 17 seconds.");
  await expect(page.locator("#run-error")).not.toContainText("private-server-detail");
  await expect(page.locator("#answer")).toBeHidden();
});
test("server capacity rejection holds the preview", async ({ page }) => {
  await connect(page);
  await page.route("**/v1/checks/output", route => route.fulfill({status:503,headers:{"Retry-After":"1"},body:'{}'}));
  await run(page);
  await expect(page.locator("#run-error")).toHaveText("Service capacity reached. Retry in 1 second.");
  await expect(page.locator("#answer")).toBeHidden();
});

test("external-provider routing and unknown cost are visible before and after checks", async ({ page }) => {
  // Local response fixtures exercise disclosure; this test never contacts Azure.
  await page.route("**/v1/applications/current", async route => {
    const response = await route.fetch(); const data = await response.json();
    data.policies[0].checks[0].external = true; data.policies[0].external_checks = "after_local_pass";
    await route.fulfill({response,json:data});
  });
  await page.route("**/v1/checks/input", async route => {
    const response = await route.fetch(); const data = await response.json();
    data.checker_results[0].external = true; data.cost_usd = null; data.cost_basis = "provider_unpriced";
    await route.fulfill({response,json:data});
  });
  await connect(page);
  await expect(page.locator("#execution-mode")).toHaveText("External provider gated");
  await expect(page.locator("#data-routing")).toContainText("all configured local checks pass");
  await run(page);
  await expect(page.locator("#input-results")).toContainText("Provider cost not estimated");
  await expect(page.locator("#input-results")).toContainText("external provider");
  await page.getByRole("button",{name:"Disconnect",exact:true}).click();
  await expect(page.locator("#data-routing")).toBeEmpty();
});

test("a withheld external check explains its local gate", async ({ page }) => {
  await page.route("**/v1/checks/input", async route => {
    const response = await route.fetch(); const data = await response.json();
    data.checker_results[0].status = "skipped"; data.checker_results[0].external = true;
    data.checker_results[0].error_code = "external_local_gate";
    data.evaluation_status = "incomplete"; data.cost_usd = null; data.cost_basis = "provider_unpriced";
    await route.fulfill({response,json:data});
  });
  await connect(page); await run(page);
  await expect(page.locator("#input-results")).toContainText("External check withheld: configured local checks must all pass");
  await expect(page.locator("#answer")).toBeHidden();
});

test("standalone regression report shows assertions separately from evaluations", async ({ page }, testInfo) => {
  const { execFileSync } = require("node:child_process");
  const { pathToFileURL } = require("node:url");
  const reportPath = testInfo.outputPath("regression-report.html");
  execFileSync("python", ["-m","guardoath.evaluate","--pii","--html",reportPath], {stdio:"pipe"});
  const networkRequests = [];
  page.on("request", request => { if (/^https?:/.test(request.url())) networkRequests.push(request.url()); });
  await page.goto(pathToFileURL(reportPath).href);
  await expect(page.getByRole("heading",{name:"GuardOath regression report"})).toBeVisible();
  await expect(page.locator("body")).toContainText("30 of 30 expectations matched");
  await expect(page.locator("body")).toContainText("Incomplete evaluations: 2");
  await expect(page.locator("body")).not.toContainText("jane@example.com");
  await expect(page.locator("tbody tr")).toHaveCount(30);
  expect(networkRequests).toEqual([]);
});

test("baseline comparison appears in the offline evaluation report", async ({ page }, testInfo) => {
  const { execFileSync } = require("node:child_process");
  const { pathToFileURL } = require("node:url");
  const baseline = testInfo.outputPath("baseline.json");
  const output = testInfo.outputPath("compared.html");
  execFileSync("python", ["-m","guardoath.evaluate","--group","secrets","--report",baseline], {stdio:"pipe"});
  execFileSync("python", ["-m","guardoath.evaluate","--group","secrets","--baseline",baseline,"--html",output], {stdio:"pipe"});
  const requests = [];
  page.on("request", request => { if (/^https?:/.test(request.url())) requests.push(request.url()); });
  await page.goto(pathToFileURL(output).href);
  await expect(page.locator("#baseline-comparison h2")).toHaveText("Changes from baseline");
  await expect(page.locator("#baseline-comparison")).toContainText("Unchanged: 5");
  await expect(page.locator("#baseline-comparison tbody tr")).toHaveCount(5);
  await expect(page.locator("#baseline-comparison")).toContainText("their equality is not verified");
  expect(requests).toEqual([]);
});

for (const [id,rule] of [["output-password-sample","quoted credential assignment"],["output-confidential-sample","configured confidential-term rule"],["output-encoded-secret-sample","GitHub token pattern"]]) {
  test("secrets extended example " + id + " holds the output", async ({ page }) => {
    await connect(page); await page.locator("#"+id).click(); await run(page);
    await expect(page.locator("#policy")).toHaveValue("conversation-v1");
    await expect(page.locator('#output-results [data-group="secrets"] summary')).toContainText("Findings");
    await expect(page.locator("#output-results")).toContainText(rule);
    await expect(page.locator("#output-results")).not.toContainText("PROJECT LANTERN");
    await expect(page.locator("#output-results")).not.toContainText("synthetic-example!42");
    await expect(page.locator("#answer")).toBeHidden();
  });
}
test("correcting an extended secret example releases the preview", async ({ page }) => {
  await connect(page); await page.locator("#output-password-sample").click();
  await page.locator("#output-content").fill("No credentials are included."); await run(page);
  await expect(page.locator("#answer")).toBeVisible();
  await expect(page.locator('#output-results [data-group="secrets"]')).toContainText("Quoted assignments: enabled");
});
test("secrets group batch report keeps expected incomplete cases visible", async ({ page }, testInfo) => {
  const { execFileSync } = require("node:child_process");
  const { pathToFileURL } = require("node:url");
  const file = testInfo.outputPath("secrets-batch.html");
  execFileSync("python",["-m","guardoath.evaluate","--cases","evals/secrets.json","--policies","policies/secrets-batch.json","--html",file],{stdio:"pipe"});
  await page.goto(pathToFileURL(file).href);
  await expect(page.locator("body")).toContainText("100 of 100 expectations matched");
  await expect(page.locator("body")).toContainText("Incomplete evaluations: 8");
  await expect(page.locator("tbody tr")).toHaveCount(100);
  await expect(page.locator("body")).not.toContainText("PROJECT LANTERN");
});

test("dashboard links to the maintained user guide", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page.getByRole("link",{name:"User guide"})).toHaveAttribute("href","https://github.com/CollectiverseStudio/guardoath/blob/main/docs/user-guide.md");
});


test("normal conversation needs no profile selection and follows four stages", async ({page}) => {
  await page.goto("/dashboard");
  await page.getByLabel("Application API key").fill("playwright-test-key");
  await page.getByRole("button",{name:"Connect",exact:true}).click();
  await expect(page.locator("#run-input")).toBeEnabled();
  await expect(page.locator("#advanced")).not.toHaveAttribute("open", "");
  await expect(page.locator("#policy")).toHaveValue("conversation-v1");
  await expect(page.locator("#run-output")).toBeDisabled();
  const calls=[]; page.on("request",r=>{if(r.url().includes("/v1/checks/")) calls.push(r.url().split("/").pop());});
  await page.locator("#run-input").click();
  await expect(page.locator("#run-output")).toBeEnabled();
  expect(calls).toEqual(["input"]);
  for(const group of ["sensitive_data","secrets","prompt_security"])
    await expect(page.locator(`#input-results [data-group="${group}"] summary`)).toContainText("Passed");
  await expect(page.locator("#answer")).toBeHidden();
  await page.locator("#output-content").fill("Please contact the store for return instructions.");
  await expect(page.locator("#run-output")).toBeEnabled();
  await page.locator("#run-output").click();
  await expect(page.locator("#answer")).toBeVisible(); expect(calls).toEqual(["input","output"]);
  for(const group of ["sensitive_data","secrets","prompt_security"])
    await expect(page.locator(`#output-results [data-group="${group}"] summary`)).toContainText("Passed");
  const positions=await page.locator(".flow-step").evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect().top));
  expect(positions).toEqual([...positions].sort((a,b)=>a-b));
  await page.locator("#input-content").fill("Changed input");
  await expect(page.locator("#run-output")).toBeDisabled(); await expect(page.locator("#answer")).toBeHidden();
});
test("input findings prevent output requests even in observe mode", async ({page}) => {
  await connect(page); const calls=[];
  page.on("request",r=>{if(r.url().includes("/v1/checks/")) calls.push(r.url().split("/").pop());});
  await page.locator("#input-content").fill('password = "synthetic-example!42"'); await run(page);
  await expect(page.locator("#input-results")).toContainText("quoted credential assignment");
  await expect(page.locator("#input-gate")).toContainText("Input held");
  expect(calls).toEqual(["input"]); await expect(page.locator("#run-output")).toBeDisabled();
});
test("an input edit during a pending check cannot unlock output", async ({page}) => {
  await connect(page); let release; const gate=new Promise(resolve=>release=resolve);
  await page.route("**/v1/checks/input",async route=>{const response=await route.fetch(); await gate; await route.fulfill({response});});
  await page.locator("#run-input").click();
  await expect(page.locator("#result-status")).toHaveText("Checking…");
  await page.locator("#input-content").fill("A new question that has not been checked."); release();
  await expect(page.locator("#run-input")).toBeEnabled();
  await expect(page.locator("#run-output")).toBeDisabled(); await expect(page.locator("#answer")).toBeHidden();
});
test("failed input never requests output",async({page})=>{
  await connect(page); let outputCalls=0;
  await page.route("**/v1/checks/input",route=>route.fulfill({status:503,body:'{}'}));
  page.on("request",r=>{if(r.url().endsWith("/v1/checks/output")) outputCalls++;});
  await run(page); expect(outputCalls).toBe(0); await expect(page.locator("#run-output")).toBeDisabled();
});

for (const [id,rule] of [["phone","phone number"],["birth-date","birth date"],["insurance-id","health-plan member"],["bank-account","bank account"]]) {
  test("combined privacy output example " + id,async({page})=>{
    await connect(page); await page.locator('#output-'+id+'-sample').click(); await run(page);
    await expect(page.locator('#policy')).toHaveValue('conversation-v1');
    await expect(page.locator('#output-results [data-group="sensitive_data"] summary')).toContainText('Findings');
    await expect(page.locator('#output-results')).toContainText(rule);
    await expect(page.locator('#answer')).toBeHidden();
  });
}
test("medical record example holds the input before output",async({page})=>{
  await connect(page); await page.locator('#input-medical-id-sample').click(); await run(page);
  await expect(page.locator('#input-results')).toContainText('medical record identifier');
  await expect(page.locator('#input-results')).not.toContainText('A1234');
  await expect(page.locator('#run-output')).toBeDisabled();
  await expect(page.locator('#output-results')).toContainText('Waiting for');
});
test("extended privacy redaction shows only rechecked replacements",async({page})=>{
  await connect(page); await profile(page,'redaction-v1'); await page.locator('#output-redaction-sample').click(); await run(page);
  const result=page.locator('#output-results .redacted-content');
  for(const entity of ['PHONE_NUMBER','DATE_OF_BIRTH','MEDICAL_RECORD_NUMBER','HEALTH_PLAN_ID','BANK_ACCOUNT'])
    await expect(result).toContainText('[REDACTED_'+entity+']');
  for(const value of ['202-555-0147','1980-02-29','A1234','M000123','76543210'])
    await expect(result).not.toContainText(value);
  await expect(page.locator('#output-results .redaction-box')).toContainText('Rechecked: pii · passed');
});
test("privacy batch report distinguishes incomplete cases from matched expectations",async({page},testInfo)=>{
  const {execFileSync}=require('node:child_process'); const {pathToFileURL}=require('node:url');
  const path=testInfo.outputPath('privacy.html');
  execFileSync('python',['-m','guardoath.evaluate','--pii','--cases','evals/privacy.json','--policies','policies/privacy-batch.json','--html',path],{stdio:'pipe'});
  await page.goto(pathToFileURL(path).href);
  await expect(page.locator('body')).toContainText('100 of 100 expectations matched');
  await expect(page.locator('body')).toContainText('Incomplete evaluations: 12');
  await expect(page.locator('tbody tr')).toHaveCount(100);
  for(const value of ['jane@example.com','1980-02-29','A1234']) await expect(page.locator('body')).not.toContainText(value);
});

for (const stage of ["input","output"]) {
  test(stage + " examples change only their own data and format",async({page})=>{
    test.setTimeout(60000);
    await connect(page); const other=stage==='input'?'output':'input'; const seen=new Set(); let checks=0;
    page.on('request',r=>{if(r.url().includes('/v1/checks/')) checks++;});
    for(const version of ['conversation-v1','redaction-v1','business-v1','completeness-v1','structure-v1','business-number-le-v1','text-security-v1','content-safety-v1','bias-matched-string-v1','bias-matched-boolean-v1','bias-matched-number-v1','evidence-v1','tool-calls-v1','privacy-encoded-v1','privacy-encoded-redact-v1','ledger-v1','timestamps-v1','graph-v1','integrity-v1']) {
      await page.locator('#policy').selectOption(version);
      const ids=await page.locator('#'+stage+'-samples button:enabled').evaluateAll(nodes=>nodes.map(n=>n.id));
      for(const id of ids) {
        if(seen.has(id)) continue; seen.add(id);
        await page.locator('#'+other+'-content').fill('{"untouched":"'+id+'"}');
        await page.locator('#'+other+'-format').selectOption('json');
        const untouched=await page.locator('#'+other+'-content').inputValue();
        await page.locator('#'+stage+'-content').fill('Replace this '+stage+' value.');
        await page.locator('#'+id).click();
        await expect(page.locator('#'+stage+'-content')).not.toHaveValue('Replace this '+stage+' value.');
        await expect(page.locator('#'+other+'-content')).toHaveValue(untouched);
        await expect(page.locator('#'+other+'-format')).toHaveValue('json');
        await expect(page.locator('#policy')).toHaveValue(version);
      }
    }
    expect(seen.size).toBe(stage==='input'?127:130);
    expect(checks).toBe(0);
  });
}
test("output examples preserve the input pass and can be checked independently",async({page})=>{
  await connect(page); const calls=[];
  page.on('request',r=>{if(r.url().includes('/v1/checks/')) calls.push(r.url().split('/').pop());});
  await page.locator('#run-input').click(); await expect(page.locator('#run-output')).toBeEnabled();
  const input=await page.locator('#input-content').inputValue();
  const findings=await page.locator('#input-results').textContent();
  await page.locator('#output-email-sample').click();
  await expect(page.locator('#input-content')).toHaveValue(input);
  await expect(page.locator('#input-results')).toHaveText(findings);
  await expect(page.locator('#run-output')).toBeEnabled();
  await page.locator('#run-output').click(); await expect(page.locator('#answer-status')).toHaveText('Held for review');
  await page.locator('#output-clean-sample').click();
  await expect(page.locator('#output-results')).toContainText('Waiting for');
  await expect(page.locator('#run-output')).toBeEnabled();
  await page.locator('#run-output').click(); await expect(page.locator('#answer')).toBeVisible();
  expect(calls).toEqual(['input','output','output']);
});
test("input examples invalidate both test results while preserving the proposed answer",async({page})=>{
  await connect(page); await run(page); const answer=await page.locator('#output-content').inputValue();
  await page.locator('#input-email-sample').click();
  await expect(page.locator('#input-content')).toHaveValue(/Can you help.*jane@example.com/);
  await expect(page.locator('#output-content')).toHaveValue(answer);
  await expect(page.locator('#input-results')).toContainText('Waiting for');
  await expect(page.locator('#output-results')).toContainText('Waiting for');
  await expect(page.locator('#run-output')).toBeDisabled(); await expect(page.locator('#answer')).toBeHidden();
});
test("output-only confidential example flags the answer after the same phrase passes input",async({page})=>{
  await connect(page);
  await page.locator('#input-content').fill('Can you explain the unreleased pricing plan?');
  await page.locator('#run-input').click(); await expect(page.locator('#run-output')).toBeEnabled();
  await page.locator('#output-output-confidential-sample').click();
  await page.locator('#run-output').click();
  await expect(page.locator('#output-results [data-group="secrets"] summary')).toContainText('Findings');
  await expect(page.locator('#answer')).toBeHidden();
});
test("multiple-issue answer exercises all three real local groups",async({page})=>{
  await connect(page); await page.locator('#output-multiple-sample').click(); await run(page);
  for(const group of ['sensitive_data','secrets','prompt_security'])
    await expect(page.locator('#output-results [data-group="'+group+'"] summary')).toContainText('Findings');
  await expect(page.locator('#answer')).toBeHidden();
});


for(const [kind,message] of [['missing-field','A required field is missing.'],['wrong-type','A value has the wrong data type.'],['extra-field','An unexpected field is present.'],['invalid-date','A value does not match a supported format.'],['invalid-email','A value does not match a supported format.'],['too-many-items','An array has too many items.'],['malformed-json','malformed JSON']]) {
  test('structured output example '+kind,async({page})=>{
    await connect(page); await profile(page,'structure-v1');
    await page.locator('#run-input').click(); await expect(page.locator('#run-output')).toBeEnabled();
    const input=await page.locator('#input-content').inputValue();
    await page.locator('#output-'+kind+'-sample').click();
    await expect(page.locator('#input-content')).toHaveValue(input);
    await page.locator('#run-output').click();
    await expect(page.locator('#output-results [data-group="structure"] summary')).toContainText('Findings');
    await expect(page.locator('#output-results')).toContainText(message);
    await expect(page.locator('#output-results')).not.toContainText('builder@example.com');
    await expect(page.locator('#answer')).toBeHidden();
  });
}
test('correcting a structured answer preserves input and releases the preview',async({page})=>{
  await connect(page); await profile(page,'structure-v1');
  await page.locator('#run-input').click(); await expect(page.locator('#run-output')).toBeEnabled();
  await page.locator('#output-missing-field-sample').click(); await page.locator('#run-output').click();
  await expect(page.locator('#answer')).toBeHidden();
  await page.locator('#output-clean-sample').click(); await page.locator('#run-output').click();
  await expect(page.locator('#answer')).toBeVisible();
  await expect(page.locator('#answer')).toContainText('The example request has been reviewed.');
  await expect(page.locator('#output-results [data-group="sensitive_data"] summary')).toContainText('Disabled');
});
test('structure group report keeps required and optional skips explicit',async({page},testInfo)=>{
  const {execFileSync}=require('node:child_process'); const {pathToFileURL}=require('node:url');
  const path=testInfo.outputPath('structure.html');
  execFileSync('python',['-m','guardoath.evaluate','--cases','evals/structure.json','--policies','policies/structure-batch.json','--html',path],{stdio:'pipe'});
  await page.goto(pathToFileURL(path).href);
  await expect(page.locator('body')).toContainText('100 of 100 expectations matched');
  await expect(page.locator('body')).toContainText('Incomplete evaluations: 4');
  await expect(page.locator('tbody tr')).toHaveCount(100);
  await expect(page.locator('body')).not.toContainText('builder@example.com');
});


for(const [id,reason] of [['numeric-exceeds','less than or equal to'],['comparison-missing','missing or invalid number'],['number-invalid','missing or invalid number'],['decimal-over','less than or equal to']]) {
  test('numeric output example '+id,async({page})=>{
    await connect(page); await profile(page,'business-number-le-v1');
    await page.locator('#run-input').click(); await expect(page.locator('#run-output')).toBeEnabled();
    await page.locator('#output-'+id+'-sample').click(); await page.locator('#run-output').click();
    await expect(page.locator('#output-results [data-group="business"] summary')).toContainText('Findings');
    await expect(page.locator('#output-results')).toContainText(reason);
    await expect(page.locator('#answer')).toBeHidden();
  });
}
test('decimal examples reach the API as exact text and corrected output releases preview',async({page})=>{
  await connect(page); await profile(page,'business-number-le-v1');
  await page.locator('#run-input').click(); await expect(page.locator('#run-output')).toBeEnabled();
  await page.locator('#output-decimal-over-sample').click();
  const request=page.waitForRequest(r=>r.url().endsWith('/v1/checks/output'));
  await page.locator('#run-output').click();
  expect((await request).postDataJSON().content).toContain('1.0000000000000002');
  await expect(page.locator('#answer')).toBeHidden();
  await page.locator('#output-decimal-within-sample').click(); await page.locator('#run-output').click();
  await expect(page.locator('#answer')).toBeVisible();
  await expect(page.locator('#answer')).toContainText('1.0000000000000001');
});
for(const [id,reason] of [['section-duplicate','is repeated'],['section-empty','is too short'],['section-short','is too short']]) {
  test('required information example '+id,async({page})=>{
    await connect(page); await profile(page,'completeness-v1');
    await page.locator('#output-'+id+'-sample').click(); await run(page);
    await expect(page.locator('#output-results')).toContainText(reason);
    await expect(page.locator('#answer')).toBeHidden();
    await page.locator('#output-section-multiline-sample').click(); await page.locator('#run-output').click();
    await expect(page.locator('#answer')).toBeVisible();
  });
}
test('only examples applicable to the active profile are shown',async({page})=>{
  await connect(page); await expect(page.locator('#input-email-sample')).toBeVisible();
  await expect(page.locator('#input-decimal-over-sample')).toBeHidden();
  await profile(page,'business-number-le-v1');
  await expect(page.locator('#input-email-sample')).toBeHidden();
  await expect(page.locator('#input-decimal-over-sample')).toBeVisible();
});
test('business batch report keeps skipped requirements visible',async({page},testInfo)=>{
  const {execFileSync}=require('node:child_process'); const {pathToFileURL}=require('node:url');
  const path=testInfo.outputPath('business.html');
  execFileSync('python',['-m','guardoath.evaluate','--cases','evals/business.json','--policies','policies/business-batch.json','--html',path],{stdio:'pipe'});
  await page.goto(pathToFileURL(path).href);
  await expect(page.locator('body')).toContainText('100 of 100 expectations matched');
  await expect(page.locator('body')).toContainText('Incomplete evaluations: 6');
  await expect(page.locator('tbody tr')).toHaveCount(100);
  await expect(page.locator('body')).not.toContainText('1.0000000000000001');
});

for(const [id,rule] of [['refund-exceeds','refund_limit'],['date-reversed','date_order']]) {
  test('isolated business relation example '+id,async({page})=>{
    await connect(page); await profile(page,'business-v1');
    await page.locator('#output-'+id+'-sample').click(); await run(page);
    await expect(page.locator('#output-results [data-group="business"] summary')).toContainText('Findings');
    await expect(page.locator('#output-results')).toContainText(rule);
    await expect(page.locator('#output-results [data-group="structure"] summary')).toContainText('Passed');
    await expect(page.locator('#answer')).toBeHidden();
  });
}


test('examples remain hidden until an application profile is loaded',async({page})=>{
  await page.goto('/dashboard');
  await expect(page.locator('#input-samples button:visible')).toHaveCount(0);
  await expect(page.locator('#output-samples button:visible')).toHaveCount(0);
  await page.getByLabel('Application API key').fill('playwright-test-key');
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await expect(page.locator('#input-clean-sample')).toBeVisible();
  await expect(page.locator('#output-clean-sample')).toBeVisible();
});


for(const [id,signal] of [['unicode-bidi','bidirectional formatting'],['unicode-invisible','invisible formatting'],['ascii-controls','control characters'],['encoded-controls','found inside a labeled encoded payload'],['invalid-base64','malformed Base64'],['invalid-hex','malformed hex'],['non-utf8','non-UTF-8 bytes']]) {
  test('local text-security output example '+id,async({page})=>{
    await connect(page); await profile(page,'text-security-v1');
    await page.locator('#run-input').click(); await expect(page.locator('#run-output')).toBeEnabled();
    await page.locator('#output-'+id+'-sample').click(); await page.locator('#run-output').click();
    await expect(page.locator('#output-results [data-group="prompt_security"] summary')).toContainText('Findings');
    await expect(page.locator('#output-results')).toContainText(signal);
    await expect(page.locator('#answer')).toBeHidden();
  });
}
test('inspection budget exhaustion holds input and never submits output',async({page})=>{
  await connect(page); await profile(page,'text-security-v1'); let output=0;
  page.on('request',r=>{if(r.url().endsWith('/v1/checks/output')) output++;});
  await page.locator('#input-encoding-limit-sample').click(); await run(page);
  await expect(page.locator('#input-results')).toContainText('text_inspection_budget_exceeded');
  await expect(page.locator('#result-status')).toHaveText('Incomplete');
  await expect(page.locator('#run-output')).toBeDisabled(); expect(output).toBe(0);
});
for(const id of ['plain-intent','joiner-safe']) {
  test('local text-security coverage limit example '+id,async({page})=>{
    await connect(page); await profile(page,'text-security-v1');
    await page.locator('#input-'+id+'-sample').click(); await page.locator('#output-'+id+'-sample').click();
    await run(page); await expect(page.locator('#result-status')).toHaveText('No findings');
    await expect(page.locator('#profile-status')).toContainText('Plain-language attacks, indirect instructions and multi-turn manipulation are not assessed');
    await expect(page.locator('#answer')).toBeVisible();
  });
}
test('local text-security batch keeps incomplete evaluations separate from matched cases',async({page},testInfo)=>{
  const {execFileSync}=require('node:child_process'); const {pathToFileURL}=require('node:url');
  const path=testInfo.outputPath('text-security.html');
  execFileSync('python',['-m','guardoath.evaluate','--cases','evals/text-security.json','--policies','policies/text-security-batch.json','--html',path],{stdio:'pipe'});
  await page.goto(pathToFileURL(path).href);
  await expect(page.locator('body')).toContainText('100 of 100 expectations matched');
  await expect(page.locator('body')).toContainText('Incomplete evaluations: 14');
  await expect(page.locator('tbody tr')).toHaveCount(100);
  await expect(page.locator('body')).not.toContainText('private-marker');
});

test('content safety is an optional provider check independent of hosting', async ({page}) => {
  await connect(page); await profile(page,'content-safety-v1');
  await expect(page.locator('#profile-status')).toContainText('Provider: not configured');
  await expect(page.locator('#profile-status')).toContainText('Hosting does not choose the provider');
  await expect(page.locator('#coverage')).toContainText('Harmful content and abuse · unavailable');
  await expect(page.locator('#data-routing')).toContainText('selected stage');
  await run(page);
  await expect(page.locator('#result-status')).toHaveText('Incomplete');
  await expect(page.locator('#input-results')).toContainText('checker_unavailable');
  await expect(page.locator('#answer')).toBeHidden();
  await expect(page.locator('#run-output')).toBeDisabled();
});

// API response fixtures exercise UI behavior only. No provider calls or local
// semantic classification are implied by these browser tests.
async function contentSafetyFixtures(page, outputFinding, failure=false) {
  await page.route('**/v1/applications/current',async route=>{
    const response=await route.fetch(); const data=await response.json();
    const check=data.policies.find(p=>p.version==='content-safety-v1').checks.find(c=>c.id==='content_safety');
    check.available=true; check.provider='synthetic-browser-fixture';
    await route.fulfill({response,json:data});
  });
  await page.route('**/v1/checks/*',async route=>{
    const response=await route.fetch(); const data=await response.json();
    if(data.policy_version!=='content-safety-v1') return route.fulfill({response,json:data});
    const output=data.stage==='output';
    const finding=output&&outputFinding?{rule_id:'harmful_content.fixture_'+outputFinding,category:'harmful_content',severity:'medium',explanation:'Synthetic browser fixture: '+outputFinding+' review signal.'}:null;
    const check=data.checker_results.find(c=>c.checker_id==='content_safety');
    check.status=output&&failure?'error':finding?'flagged':'passed'; check.error_code=output&&failure?'bedrock_rate_limited':null;
    check.checker_version='browser-fixture/1'; check.findings=finding?[finding]:[];
    data.findings=finding?[finding]:[]; data.evaluation_status=output&&failure?'incomplete':'complete';
    data.recommended_action=output&&(finding||failure)?'block':'allow';
    const group=data.groups.find(g=>g.group_id==='harmful_content'); group.status=output&&failure?'incomplete':finding?'findings':'passed';
    await route.fulfill({response,json:data});
  });
}
for(const category of ['hate','insults','sexual','violence','misconduct']) {
  test('content safety output holds a '+category+' provider finding',async({page})=>{
    await contentSafetyFixtures(page,category); await connect(page); await profile(page,'content-safety-v1');
    const outputResponse=page.waitForResponse(response=>response.url().endsWith('/v1/checks/output')&&response.request().method()==='POST');
    await run(page);
    const result=await (await outputResponse).json();
    expect(result.findings.map(f=>f.rule_id)).toContain('harmful_content.fixture_'+category);
    await expect(page.locator('#input-results [data-group="harmful_content"] summary')).toContainText('Passed');
    await expect(page.locator('#output-results')).toContainText('Synthetic browser fixture: '+category+' review signal.');
    await expect(page.locator('#output-results [data-group="harmful_content"] summary')).toContainText('Findings');
    await expect(page.locator('#answer')).toBeHidden();
  });
}
test('content safety provider failure holds the answer after a completed input',async({page})=>{
  await contentSafetyFixtures(page,null,true); await connect(page); await profile(page,'content-safety-v1'); await run(page);
  await expect(page.locator('#result-status')).toHaveText('Incomplete');
  await expect(page.locator('#output-results')).toContainText('bedrock_rate_limited');
  await expect(page.locator('#answer')).toBeHidden();
});
test('content safety clean fixture can complete the full sequence',async({page})=>{
  await contentSafetyFixtures(page,null); await connect(page); await profile(page,'content-safety-v1'); await run(page);
  await expect(page.locator('#result-status')).toHaveText('No findings');
  await expect(page.locator('#answer')).toBeVisible();
});


for(const type of ['string','boolean','number']) {
  test('matched '+type+' decisions are checked locally on both stages',async({page})=>{
    await connect(page); await profile(page,'bias-matched-'+type+'-v1'); await run(page);
    await expect(page.locator('#input-results [data-group="bias"] summary')).toContainText('Passed');
    await expect(page.locator('#output-results [data-group="bias"] summary')).toContainText('Passed');
    await expect(page.locator('#result-status')).toHaveText('No findings');
    await expect(page.locator('#answer')).toBeVisible();
    await expect(page.locator('#profile-status')).toContainText('does not classify free-text bias');
  });
  for(const stage of ['input','output']) {
    test('matched '+type+' difference is held at '+stage,async({page})=>{
      await connect(page); await profile(page,'bias-matched-'+type+'-v1');
      await page.locator('#'+stage+'-matched-difference-sample').click();
      const response=page.waitForResponse(r=>r.url().endsWith('/v1/checks/'+stage)&&r.request().method()==='POST');
      await run(page);
      expect((await (await response).json()).findings.map(f=>f.rule_id)).toContain('bias.matched_outcome_difference');
      await expect(page.locator('#'+stage+'-results [data-group="bias"] summary')).toContainText('Findings');
      await expect(page.locator('#'+stage+'-results')).toContainText('Submitted outcomes differ');
      await expect(page.locator('#answer')).toBeHidden();
    });
  }
}
for(const [sample,code] of [['matched-factors','matched_outcomes_insufficient_comparisons'],['matched-no-contrast','matched_outcomes_insufficient_comparisons'],['matched-uncovered','matched_outcomes_insufficient_comparisons'],['matched-missing','matched_outcomes_invalid_records'],['matched-overlap','matched_outcomes_invalid_records']]) {
  test('matched evidence gaps hold '+sample,async({page})=>{
    await connect(page); await profile(page,'bias-matched-string-v1');
    await page.locator('#input-'+sample+'-sample').click();await run(page);
    await expect(page.locator('#result-status')).toHaveText('Incomplete');
    await expect(page.locator('#input-results')).toContainText(code);
    await expect(page.locator('#run-output')).toBeDisabled();await expect(page.locator('#answer')).toBeHidden();
  });
}
test('a neutral demographic mention alone is not a matched-outcome finding',async({page})=>{
  await connect(page);await profile(page,'bias-matched-string-v1');
  await page.locator('#input-matched-mention-sample').click();await page.locator('#output-matched-mention-sample').click();await run(page);
  await expect(page.locator('#result-status')).toHaveText('No findings');await expect(page.locator('#answer')).toBeVisible();
});
test('exact matched scores are preserved and a small difference is reviewed',async({page})=>{
  await connect(page);await profile(page,'bias-matched-number-v1');await page.locator('#output-matched-precision-sample').click();
  await expect(page.locator('#output-content')).toHaveValue(/1\.0000000000000001/);await run(page);
  await expect(page.locator('#output-results')).toContainText('Submitted outcomes differ');await expect(page.locator('#answer')).toBeHidden();
});
test('the entire matched-outcome report is readable without private scenario values',async({page})=>{
  const {execFileSync}=require('node:child_process');const path=require('node:path').join(test.info().outputDir,'bias.html');
  require('node:fs').mkdirSync(test.info().outputDir,{recursive:true});
  execFileSync('python',['-m','guardoath.evaluate','--cases','evals/bias.json','--policies','policies/bias-batch.json','--html',path],{stdio:'pipe'});
  await page.goto('file://'+path);await expect(page.locator('body')).toContainText('100 of 100 expectations matched');
  await expect(page.locator('body')).toContainText('Incomplete evaluations: 50');await expect(page.locator('tbody tr')).toHaveCount(100);
  await expect(page.locator('body')).not.toContainText('qualification');
});


for(const [sample,rule] of [['evidence-unknown','unknown_source'],['evidence-missing','missing_reference'],['evidence-mismatch','quote_mismatch'],['evidence-short','quote_too_short'],['evidence-unused','unused_citation'],['evidence-duplicate','duplicate_citation'],['evidence-marker','invalid_marker']]) {
  test('citation evidence holds '+sample,async({page})=>{
    await connect(page);await profile(page,'evidence-v1');await page.locator('#output-'+sample+'-sample').click();
    const response=page.waitForResponse(r=>r.url().endsWith('/v1/checks/output')&&r.request().method()==='POST');await run(page);
    expect((await (await response).json()).findings.map(f=>f.rule_id)).toContain('evidence.'+rule);
    await expect(page.locator('#output-results [data-group="accuracy"] summary')).toContainText('Findings');
    await expect(page.locator('#answer')).toBeHidden();
  });
}
test('citation evidence clean documents complete both local stages',async({page})=>{
  await connect(page);await profile(page,'evidence-v1');await run(page);
  await expect(page.locator('#input-results [data-group="accuracy"] summary')).toContainText('Passed');
  await expect(page.locator('#output-results [data-group="accuracy"] summary')).toContainText('Passed');
  await expect(page.locator('#answer')).toBeVisible();
});
test('citation consistency explicitly does not verify an unrelated claim',async({page})=>{
  await connect(page);await profile(page,'evidence-v1');await page.locator('#output-evidence-unrelated-sample').click();await run(page);
  await expect(page.locator('#result-status')).toHaveText('No findings');await expect(page.locator('#answer')).toBeVisible();
  await expect(page.locator('#profile-status')).toContainText('support for each claim are not assessed');
});
test('caller-supplied sources cannot replace the configured evidence catalog',async({page})=>{
  await connect(page);await profile(page,'evidence-v1');await page.locator('#input-evidence-catalog-sample').click();await run(page);
  await expect(page.locator('#input-results')).toContainText('evidence_invalid_document');
  await expect(page.locator('#result-status')).toHaveText('Incomplete');await expect(page.locator('#run-output')).toBeDisabled();
});
test('the full citation-evidence report omits quotations and preserves incomplete counts',async({page},testInfo)=>{
  const {execFileSync}=require('node:child_process');const {pathToFileURL}=require('node:url');const path=testInfo.outputPath('evidence.html');
  execFileSync('python',['-m','guardoath.evaluate','--cases','evals/evidence.json','--policies','policies/evidence-batch.json','--html',path],{stdio:'pipe'});
  await page.goto(pathToFileURL(path).href);await expect(page.locator('body')).toContainText('100 of 100 expectations matched');
  await expect(page.locator('body')).toContainText('Incomplete evaluations: 36');await expect(page.locator('tbody tr')).toHaveCount(100);
  await expect(page.locator('body')).not.toContainText('Unused items may be returned within 30 days.');
});


for(const [sample,rule] of [['tool-unknown','unknown_tool'],['tool-invalid','invalid_arguments'],['tool-extra','invalid_arguments'],['tool-refund','invalid_arguments']]) {
  test('proposed tool validation holds '+sample,async({page})=>{
    await connect(page);await profile(page,'tool-calls-v1');await page.locator('#output-'+sample+'-sample').click();
    const response=page.waitForResponse(r=>r.url().endsWith('/v1/checks/output')&&r.request().method()==='POST');await run(page);
    expect((await (await response).json()).findings.map(f=>f.rule_id)).toContain('actions.'+rule);
    await expect(page.locator('#output-results [data-group="actions"] summary')).toContainText('Findings');
    await expect(page.locator('#output-results')).toContainText('No tool was executed or authorized');await expect(page.locator('#answer')).toBeHidden();
  });
}
for(const sample of ['tool-missing','tool-limit','tool-catalog','tool-lossy']) {
  test('incomplete tool validation holds '+sample,async({page})=>{
    await connect(page);await profile(page,'tool-calls-v1');await page.locator('#input-'+sample+'-sample').click();await run(page);
    await expect(page.locator('#result-status')).toHaveText('Incomplete');await expect(page.locator('#input-results')).toContainText('tool_calls_invalid_document');
    await expect(page.locator('#run-output')).toBeDisabled();await expect(page.locator('#answer')).toBeHidden();
  });
}
test('clean proposed tool calls are locally checked without execution',async({page})=>{
  await connect(page);await profile(page,'tool-calls-v1');await run(page);
  await expect(page.locator('#input-results [data-group="actions"] summary')).toContainText('Passed');
  await expect(page.locator('#output-results [data-group="actions"] summary')).toContainText('Passed');await expect(page.locator('#answer')).toBeVisible();
  await expect(page.locator('#profile-status')).toContainText('No tool is executed or authorized');
});
test('an empty proposed-call list is handled explicitly',async({page})=>{
  await connect(page);await profile(page,'tool-calls-v1');await page.locator('#input-tool-none-sample').click();await page.locator('#output-tool-none-sample').click();await run(page);
  await expect(page.locator('#result-status')).toHaveText('No findings');await expect(page.locator('#answer')).toContainText('tool_calls');
});
test('proposed action report omits argument values',async({page},testInfo)=>{
  const {execFileSync}=require('node:child_process');const {pathToFileURL}=require('node:url');const path=testInfo.outputPath('actions.html');
  execFileSync('python',['-m','guardoath.evaluate','--cases','evals/actions.json','--policies','policies/actions-batch.json','--html',path],{stdio:'pipe'});
  await page.goto(pathToFileURL(path).href);await expect(page.locator('body')).toContainText('100 of 100 expectations matched');
  await expect(page.locator('body')).toContainText('Incomplete evaluations: 28');await expect(page.locator('tbody tr')).toHaveCount(100);
  await expect(page.locator('body')).not.toContainText('ORD-1234');
});


test('all local regression suites have one index with working group report links',async({page},testInfo)=>{
  const {execFileSync}=require('node:child_process');const {pathToFileURL}=require('node:url');const path=testInfo.outputPath('all-local');
  execFileSync('python',['-m','guardoath.evaluate_all','--pii','--output',path],{stdio:'pipe'});
  await page.goto(pathToFileURL(require('node:path').join(path,'index.html')).href);
  await expect(page.locator('body')).toContainText('1430 of 1430 expectations matched');await expect(page.locator('tbody tr')).toHaveCount(15);
  await page.getByRole('link',{name:'bias',exact:true}).click();
  await expect(page.locator('body')).toContainText('100 of 100 expectations matched');await expect(page.locator('tbody tr')).toHaveCount(100);
});

for(const [sample,rule] of [['encoded-email','email_address'],['encoded-card','credit_card'],['encoded-ssn','us_ssn'],['encoded-field-json','phone_number']]) {
  test('encoded sensitive data is held for '+sample,async({page})=>{
    await connect(page);await profile(page,'privacy-encoded-v1');await page.locator('#output-'+sample+'-sample').click();
    const response=page.waitForResponse(r=>r.url().endsWith('/v1/checks/output')&&r.request().method()==='POST');await run(page);
    expect((await (await response).json()).findings.map(f=>f.rule_id)).toContain('pii.encoded_'+rule);
    await expect(page.locator('#output-results')).toContainText('inside a labeled encoded payload');await expect(page.locator('#answer')).toBeHidden();
  });
}
for(const [sample,code] of [['encoded-invalid','encoded_pii_invalid_encoding'],['encoded-nested','encoded_pii_nested_payload'],['encoded-budget','encoded_pii_budget_exceeded']]) {
  test('encoded privacy gaps remain incomplete for '+sample,async({page})=>{
    await connect(page);await profile(page,'privacy-encoded-v1');await page.locator('#input-'+sample+'-sample').click();await run(page);
    await expect(page.locator('#result-status')).toHaveText('Incomplete');await expect(page.locator('#input-results')).toContainText(code);await expect(page.locator('#run-output')).toBeDisabled();
  });
}
test('benign encoding can pass the focused identifier scan',async({page})=>{
  await connect(page);await profile(page,'privacy-encoded-v1');await page.locator('#input-encoded-benign-sample').click();await page.locator('#output-encoded-benign-sample').click();await run(page);
  await expect(page.locator('#result-status')).toHaveText('No findings');await expect(page.locator('#answer')).toBeVisible();
});
test('raw redaction does not release remaining encoded identifiers',async({page})=>{
  await connect(page);await profile(page,'privacy-encoded-redact-v1');await page.locator('#output-content').fill('Contact jane@example.com base64: '+Buffer.from('SSN: 219-09-9999').toString('base64'));await run(page);
  await expect(page.locator('#output-results')).toContainText('withheld');await expect(page.locator('#answer')).toBeHidden();
});
test('encoded privacy report preserves incomplete cases without decoded content',async({page},testInfo)=>{
  const {execFileSync}=require('node:child_process');const {pathToFileURL}=require('node:url');const path=testInfo.outputPath('encoded-privacy.html');
  execFileSync('python',['-m','guardoath.evaluate','--pii','--cases','evals/encoded-privacy.json','--policies','policies/encoded-privacy-batch.json','--html',path],{stdio:'pipe'});
  await page.goto(pathToFileURL(path).href);await expect(page.locator('body')).toContainText('100 of 100 expectations matched');await expect(page.locator('body')).toContainText('Incomplete evaluations: 28');
  await expect(page.locator('body')).not.toContainText('jane@example.com');await expect(page.locator('tbody tr')).toHaveCount(100);
});

for(const name of ['ledger','timestamps','graph','integrity']) {
  test(name+' clean sequence releases the proposed data',async({page})=>{
    await connect(page);await profile(page,name+'-v1');await run(page);
    await expect(page.locator('#result-status')).toHaveText('No findings');
    await expect(page.locator('#answer')).toBeVisible();
  });
  for(const stage of ['input','output']) test(name+' '+stage+' finding holds the answer',async({page})=>{
    await connect(page);await profile(page,name+'-v1');await page.locator('#'+stage+'-'+name+'-1-sample').click();await run(page);
    await expect(page.locator('#'+stage+'-results')).toContainText('Findings');
    await expect(page.locator('#answer')).toBeHidden();
  });
}

for(const [name,sample,code] of [['ledger','4','ledger_invalid_document'],['timestamps','4','timestamps_invalid_document'],['graph','6','graph_duplicate_node'],['integrity','limit','integrity_byte_limit']]) {
  test(name+' malformed or over-limit input stays incomplete',async({page})=>{
    await connect(page);await profile(page,name+'-v1');
    if(sample==='limit') await page.locator('#input-content').fill('x'.repeat(16385));
    else await page.locator('#input-'+name+'-'+sample+'-sample').click();
    const response=page.waitForResponse(r=>r.url().endsWith('/v1/checks/input'));
    await run(page);const data=await(await response).json();
    expect(data.evaluation_status).toBe('incomplete');expect(data.checker_results[0].error_code).toBe(code);
    await expect(page.locator('#run-output')).toBeDisabled();await expect(page.locator('#answer')).toBeHidden();
  });
}
test("proposed answer starts empty and a held input keeps its explanation", async ({page}) => {
  await connect(page); const calls=[];
  page.on("request",r=>{if(r.url().includes("/v1/checks/")) calls.push(r.url().split("/").pop());});
  await expect(page.locator("#output-content")).toHaveValue("");
  await page.locator("#run-input").click(); await expect(page.locator("#run-output")).toBeEnabled();
  await page.locator("#run-output").click();
  await expect(page.locator("#run-error")).toContainText("Paste your AI’s answer");
  expect(calls).toEqual(["input"]);
  await page.locator("#input-ssn-sample").click(); await page.locator("#run-input").click();
  await expect(page.locator("#input-gate")).toContainText("Input held");
  await page.locator("#output-content").fill("Edited answer.");
  await expect(page.locator("#input-gate")).toContainText("Input held");
  await expect(page.locator("#run-output")).toBeDisabled(); expect(calls).toEqual(["input","input"]);
});

for(const [sample,rule] of [['iban','iban_code'],['crypto','crypto'],['ip-address','ip_address'],['url','url'],['us-bank','us_bank_number'],['us-passport','us_passport'],['us-itin','us_itin'],['uk-nino','uk_nino'],['phone-text','phone_number']]) {
  test('default privacy pattern example '+sample+' is found and holds the input',async({page})=>{
    await connect(page);await page.locator('#input-'+sample+'-sample').click();
    const input=page.waitForResponse(r=>r.url().endsWith('/v1/checks/input')&&r.request().method()==='POST');
    await page.locator('#run-input').click();
    expect((await (await input).json()).findings.map(f=>f.rule_id)).toContain('pii.'+rule);
    await expect(page.locator('#policy')).toHaveValue('conversation-v1');
    await expect(page.locator('#input-results [data-group="sensitive_data"] summary')).toContainText('Findings');
    await expect(page.locator('#run-output')).toBeDisabled();await expect(page.locator('#answer')).toBeHidden();
  });
}
test("demo video loads with its full length and size", async ({ page }) => {
  await page.goto("/dashboard");
  const video = page.locator("#demo-video");
  await expect(video).toBeVisible();
  const meta = await video.evaluate(v => new Promise(resolve => {
    const done = () => resolve({ duration: v.duration, width: v.videoWidth, height: v.videoHeight, error: v.error ? v.error.code : null });
    if (v.readyState >= 1) return done();
    v.addEventListener("loadedmetadata", done, { once: true });
    v.addEventListener("error", done, { once: true });
  }));
  expect(meta.error).toBeNull();
  expect(Math.round(meta.duration)).toBe(136);
  expect([meta.width, meta.height]).toEqual([1280, 720]);
});
test("choosing a demo user shows its API key and connects", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page.locator("#demo-users")).toBeVisible();
  await expect(page.locator("#demo-user option")).toHaveCount(10);
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", { name: "Get demo API key", exact: true }).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  await expect(page.locator("#demo-key-value")).toHaveValue(/^gok_demo2_[A-Za-z0-9_-]{43}$/);
  await expect(page.locator("#api-key")).toHaveValue("");
  await expect(page.locator("#profile-status")).toContainText("pii, encoded_pii, secrets, text_security");
  await page.locator("#run-input").click();
  await expect(page.locator("#result-status")).toHaveText("Input checks passed");
  await page.locator("#input-content").fill("Please email jane@example.com about my order.");
  await page.locator("#run-input").click();
  await expect(page.locator("#input-results")).toContainText("Potential email address");
  await page.getByRole("button", { name: "Disconnect", exact: true }).click();
  await expect(page.locator("#demo-key-value")).toHaveValue("");
});
