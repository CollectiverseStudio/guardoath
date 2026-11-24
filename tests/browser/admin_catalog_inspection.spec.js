"use strict";

const {test,expect} = require("@playwright/test");
const {installAdminFixture,openFixtureSession} = require("./admin_fixture");

const BASE = "/v1/admin/catalog/categories/privacy/checks/pii";
const json = (body,status=200) => ({status,contentType:"application/json",body:JSON.stringify(body)});

function detail(rule=false) {
  const fields = ["input_check_pii","output_check_pii","detect_labeled_sensitive_data","privacy_patterns",
    "disabled_privacy_entities","privacy_min_score","input_rule_actions","output_rule_actions"];
  return {
    status:"ok",read_only:true,source:"published GuardOath control registry",registry_version:"3",
    read_audit_event_id:"a".repeat(64),
    category:{category_id:"privacy",name:"Privacy",status:"active",description:"Published privacy description"},
    check:{check_id:"pii",name:"Personal data",use_case:"Inspect identifiers",limitations:"Names can be missed",
      outcome:"Declared identifier outcome",stages:["input","output"],supported_actions:["block","flag","allow","redact"],
      recommended_action:"redact",severity:"high",rule_count:1},
    rule:rule ? {rule_id:"pii.email",title:"Email address",use_case:"Declared email example",severity:"high",
      content_format:"text",example:JSON.stringify("synthetic@example.invalid"),look_alike:JSON.stringify("What is email?"),
      needs_setting:null,settings:[],needs_checks:[]} : null,
    examples:{positive_examples:[{label:"Identifier example",stage:"input",format:"text",
      escaped_text:JSON.stringify("synthetic@example.invalid")}],look_alikes:[{label:"Benign example",stage:"output",format:"text",
      escaped_text:JSON.stringify("Explain the return policy")}],known_false_positives:[],known_misses:[]},
    settings:{source:"TenantRecord.model_json_schema",field_names:fields,
      schema:{type:"object",additionalProperties:false,properties:Object.fromEntries(fields.map(name=>[name,{title:name}]))},
      limitations:"Data shape only. Tenant validators and saved Off settings remain authoritative."},
    test_evidence:{unit_tests:["tests/test_pii.py"],evaluations:["evals/privacy.json"],
      ci_verified_for_source:false,live_deployment_verified:null,basis:"registry declarations, not executed test results"},
    detector:{version:null,implementation:["guardoath/pii.py"]},
  };
}

async function setup(page) {
  const state = {mutate:value=>value,status:200};
  const fixture = await installAdminFixture(page,{features:{catalog_inspection:true},override:request=>{
    if (request.path === BASE || request.path === BASE + "/rules/pii.email")
      return json(state.mutate(detail(request.path.includes("/rules/"))),state.status);
    return null;
  }});
  await openFixtureSession(page);
  await expect(page.locator("#status")).toContainText("Administration loaded");
  await page.locator("#nav-catalog").click();
  const category = page.locator("#categories > details").first();
  await category.locator(":scope > summary").click();
  const check = category.locator(":scope > details").first();
  await check.locator(":scope > summary").click();
  return {fixture,state,check,
    inspectCheck:()=>check.getByRole("button",{name:"Inspect check",exact:true}).click(),
    inspectRule:()=>check.getByRole("button",{name:"Inspect rule",exact:true}).click()};
}

test("owner inspects check and rule definitions without mutating tenants or pending proposals",async ({page})=>{
  const {fixture,check,inspectCheck,inspectRule} = await setup(page);
  const before = structuredClone(fixture.tenants);
  await check.getByRole("button",{name:"Preview description",exact:true}).click();
  await page.locator("#proposal-title").fill("Unsubmitted owner proposal");
  await page.locator("#proposal-use-case").fill("Retain this pending owner description");
  const callsBefore = fixture.calls.length;
  await inspectCheck();
  await expect(page.locator("#catalog-inspector-status")).toContainText("Published pii · registry 3");
  await expect(page.locator("#catalog-inspector-content")).toContainText("input_check_pii");
  await expect(page.locator("#catalog-inspector-content")).toContainText("tests/test_pii.py");
  await expect(page.locator("#catalog-inspector-content")).toContainText("not current CI or live deployment evidence");
  await expect(page.locator("#catalog-inspector-content")).toContainText("Detector version: unavailable");
  await inspectRule();
  await expect(page.locator("#catalog-inspector-status")).toContainText("Published pii.email · registry 3");
  await expect(page.locator("#catalog-inspector-content")).toContainText("Rule benign look-alike");
  await expect(page.locator("#proposal-title")).toHaveValue("Unsubmitted owner proposal");
  await expect(page.locator("#proposal-use-case")).toHaveValue("Retain this pending owner description");
  expect(fixture.tenants).toEqual(before);
  const reads = fixture.calls.slice(callsBefore);
  expect(reads).toHaveLength(2);
  expect(reads.every(call=>call.method==="GET" && call.headers["x-guardoath-admin-session"]===fixture.token &&
    call.url.searchParams.get("expected_registry_version")==="3")).toBe(true);
});

for (const [name,mutate] of [
  ["category",value=>{value.category.category_id="other";return value;}],
  ["check",value=>{value.check.check_id="secrets";return value;}],
  ["rule",value=>{value.rule.rule_id="pii.other";return value;}],
  ["version",value=>({...value,registry_version:"4"})],
  ["missing audit receipt",value=>({...value,read_audit_event_id:null})],
  ["claimed CI success",value=>{value.test_evidence.ci_verified_for_source=true;return value;}],
  ["external evidence path",value=>{value.test_evidence.unit_tests=["https://private.invalid/test.py"];return value;}],
  ["raw directional example",value=>{value.rule.example='"hidden\u202etext"';return value;}],
  ...["payer_id","billing_mode","usage","updated_at","key_hashes"].map(name=>[name,value=>{
    value.settings.field_names.push(name);value.settings.schema.properties[name]={title:"private-account-detail"};return value;
  }]),
]) {
  test(`catalog inspector refuses malformed ${name}`,async ({page})=>{
    const {state,inspectRule} = await setup(page);
    state.mutate=mutate;
    await inspectRule();
    await expect(page.locator("#catalog-inspector-status")).toContainText("failed identity or source validation");
    await expect(page.locator("#catalog-inspector-content")).toBeEmpty();
    await expect(page.locator("body")).not.toContainText("private-account-detail");
  });
}

test("published markup and invisible text stay literal and fit the mobile page",async ({page})=>{
  await page.setViewportSize({width:390,height:844});
  const {state,inspectRule} = await setup(page);
  state.mutate=value=>{
    value.rule.example='"</pre><img id=source-injection src=https://private.invalid/x onerror=alert(1)>\\u202e\\n"';
    return value;
  };
  await inspectRule();
  await expect(page.locator("#catalog-inspector-status")).toContainText("Published pii.email");
  await expect(page.locator("#catalog-inspector-content")).toContainText("<img id=source-injection");
  await expect(page.locator("#catalog-inspector-content")).toContainText("\\u202e");
  await expect(page.locator("#source-injection")).toHaveCount(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("maximum bounded non-BMP example is accepted as escaped source text",async ({page})=>{
  const {state,inspectRule} = await setup(page);
  state.mutate=value=>{value.rule.example='"'+"\\ud83d\\ude00".repeat(16384)+'"';return value;};
  await inspectRule();
  await expect(page.locator("#catalog-inspector-status")).toContainText("Published pii.email");
  await expect(page.locator("#catalog-inspector-content")).toContainText("\\ud83d\\ude00");
});

for (const oldFails of [false,true]) {
  test(`late ${oldFails ? "failed" : "successful"} check inspection cannot overwrite a newer rule selection`,async ({page})=>{
    const {inspectCheck,inspectRule} = await setup(page);
    let release,started;
    const hold = new Promise(resolve=>release=resolve), observed = new Promise(resolve=>started=resolve);
    const delivered = page.waitForResponse(response=>new URL(response.url()).pathname===BASE);
    await page.route("**"+BASE+"?*",async route=>{
      started();await hold;
      if (oldFails) await route.fulfill(json({detail:"private-provider-failure"},503));
      else await route.fallback();
    });
    await inspectCheck();await observed;
    await inspectRule();
    await expect(page.locator("#catalog-inspector-status")).toContainText("Published pii.email");
    release();await (await delivered).finished();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>resolve())));
    await expect(page.locator("#catalog-inspector-status")).toContainText("Published pii.email");
    await expect(page.locator("#catalog-inspector-content")).toContainText("Rule positive example");
  });
}

for (const action of ["refresh","logout"]) {
  test(`pending catalog inspection cannot repopulate after ${action}`,async ({page})=>{
    const {inspectCheck} = await setup(page);
    let release,started;
    const hold = new Promise(resolve=>release=resolve), observed = new Promise(resolve=>started=resolve);
    const delivered = page.waitForResponse(response=>new URL(response.url()).pathname===BASE);
    await page.route("**"+BASE+"?*",async route=>{started();await hold;await route.fallback();});
    await inspectCheck();await observed;
    await page.locator("#"+action).click();
    if (action==="refresh") await expect(page.locator("#status")).toContainText("Administration loaded");
    release();await (await delivered).finished();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>resolve())));
    await expect(page.locator("#catalog-inspector-content")).toBeEmpty();
    await expect(page.locator("#catalog-inspector-status")).not.toContainText("Published pii");
    if (action==="logout") await expect(page.locator("#catalog-inspector")).toBeHidden();
  });
}

test("stale published version gives a fixed refresh explanation without private response bodies",async ({page})=>{
  const {state,inspectRule} = await setup(page);
  state.status=409;state.mutate=()=>({detail:"private-provider-failure"});
  await inspectRule();
  await expect(page.locator("#catalog-inspector-status")).toHaveText("Published catalog changed; refresh before inspection.");
  await expect(page.locator("body")).not.toContainText("private-provider-failure");
  await expect(page.locator("#catalog-inspector-content")).toBeEmpty();
});
