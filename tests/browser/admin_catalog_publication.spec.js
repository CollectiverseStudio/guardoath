"use strict";
const {test,expect}=require("@playwright/test");
const {installAdminFixture,openFixtureSession}=require("./admin_fixture");

const DRAFT="d".repeat(64),REVIEW="e".repeat(64),BASE="b".repeat(64),PROPOSAL="c".repeat(64),CANDIDATE="f".repeat(64);
const PATH="/v1/admin/catalog/publications";
const target={category_id:"privacy",check_id:"pii",rule_id:"pii.email"};
const changes={title:{before:"Email rule",after:"Revised email rule"}};
const response=(value,status=200)=>({status,contentType:"application/json",body:JSON.stringify(value)});
const flags={published:false,remote_published:false,ci_verified:false,deployed:false,
  tenant_policies_changed:false,detector_behavior_changed:false};
function manifest() {
  return {status:"metadata_manifest_preview_only",review_id:REVIEW,draft_id:DRAFT,
    reviewed_source_sha256:BASE,reviewed_proposal_sha256:PROPOSAL,candidate_metadata_sha256:CANDIDATE,
    rollback_source_sha256:BASE,registry_version:"3",candidate_registry_version:"4",...target,
    changed_fields:["title"],changed_metadata:structuredClone(changes),published:false,ready_to_publish:false,
    tenant_policies_changed:false,detector_behavior_changed:false,source_current:true,acceptance_tests_run:false,
    reversible_patch:{kind:"reviewed_metadata_source_plan_only",target,
      forward:{expected_registry_version:"3",candidate_registry_version:"4",expected_metadata_sha256:BASE,
        candidate_metadata_sha256:CANDIDATE,changes:structuredClone(changes)},
      rollback:{expected_registry_version:"4",candidate_registry_version:"3",expected_metadata_sha256:CANDIDATE,
        candidate_metadata_sha256:BASE,changes:{title:{before:"Revised email rule",after:"Email rule"}}},
      round_trip_verified:true,executable_source_committed:false,published:false,tenant_policies_changed:false},
    release_kind:"review_only_projection_not_executable_source"};
}
function preview(writer=true) {
  return {status:"publication_preview",review_id:REVIEW,draft_id:DRAFT,base_source_sha:"a".repeat(40),
    operation:"publish_metadata",registry_version:"3",candidate_registry_version:"4",baseline_sha256:BASE,
    candidate_metadata_sha256:CANDIDATE,target:structuredClone(target),changed_metadata:structuredClone(changes),
    expected_control_hash:"0".repeat(64),control_available:true,writer_enabled:writer,
    confirmation:"PUBLISH "+REVIEW.slice(-8),...flags};
}
function receipt(body) {
  return {...preview(),status:"requested",request_id:"1".repeat(64),intent_sha256:"2".repeat(64),
    integrity_verified:true,reason:body.reason,created_at:1795478400,source_preparation:null,
    action_reference:body.action_reference};
}
async function setup(page,options={}) {
  const state={writer:options.writer!==false,mutatePreview:value=>value,mutateReceipt:value=>value,
    stored:null,previewStatus:200,recoveryStatus:200,...options};
  const fixture=await installAdminFixture(page,{features:{catalog_drafts:true,catalog_review_receipts:true,
    catalog_publication_preview:true,catalog_publication_history:true,catalog_publication_requests:state.writer},override:request=>{
    if(request.path==="/v1/admin/catalog/drafts/"+DRAFT && request.method==="GET")
      return response({draft:{draft_id:DRAFT,rule_id:"pii.email",published:false,baseline_sha256:BASE,proposal_sha256:PROPOSAL},stale:false,ready_to_publish:false});
    if(request.path==="/v1/admin/catalog/drafts/"+DRAFT+"/review")
      return response({status:"review_recorded",published:false,persisted:true,reviewed:true,ready_to_publish:false,
        review_id:REVIEW,draft_id:DRAFT,baseline_sha256:BASE,proposal_sha256:PROPOSAL});
    if(request.path==="/v1/admin/catalog/reviews/"+REVIEW)
      return response({review:{review_id:REVIEW,draft_id:DRAFT,baseline_sha256:BASE,proposal_sha256:PROPOSAL,
        status:"review_recorded",published:false},published:false,ready_to_publish:false,stale:false,source_current:true});
    if(request.path==="/v1/admin/catalog/reviews/"+REVIEW+"/manifest") return response(manifest());
    if(request.path===PATH+"/preview") return response(state.mutatePreview(preview(state.writer)),state.previewStatus);
    if(request.path===PATH && request.method==="POST") {
      state.stored=receipt(request.body);return response(state.mutateReceipt(structuredClone(state.stored)));
    }
    if(request.path===PATH+"/"+"1".repeat(64)) return response(state.mutateReceipt(structuredClone(state.stored)));
    if(request.path===PATH+"/recover") return state.recoveryStatus!==200 ? response({detail:"Unavailable"},state.recoveryStatus) :
      response(state.mutateReceipt({...structuredClone(state.stored),action_reference:request.body.action_reference}));
    return null;
  }});
  await openFixtureSession(page);
  await expect(page.locator("#status")).toContainText("Administration loaded");
  await page.locator("#nav-catalog").click();
  await page.locator("#draft-id").fill(DRAFT);
  await page.locator("#draft-inspect").click();
  await expect(page.locator("#draft-review")).toBeEnabled();
  page.on("dialog",async dialog=>{
    if(dialog.type()==="confirm") await dialog.accept();
    else await dialog.accept(dialog.message().includes("type REVIEW") ? "REVIEW "+DRAFT.slice(-8) : "Review the synthetic source evidence");
  });
  await page.locator("#draft-review").click();
  await expect(page.locator("#review-manifest")).toBeEnabled();
  await page.locator("#review-manifest").click();
  await expect(page.locator("#publication-preview")).toBeEnabled();
  return {fixture,state};
}
async function ready(page) {
  await page.locator("#publication-preview").click();
  await expect(page.locator("#publication-target")).toContainText("Type PUBLISH ");
  await page.locator("#publication-confirm").fill("PUBLISH "+REVIEW.slice(-8));
  await page.locator("#publication-reason").fill("Prepare the reviewed source description");
  await expect(page.locator("#publication-request")).toBeEnabled();
}

test("explicit request retains its reference, independently rereads history and never claims deployment",async({page})=>{
  const {fixture,state}=await setup(page);
  await ready(page);
  const reference=await page.locator("#publication-reference").inputValue();
  await page.locator("#publication-request").click();
  await expect(page.locator("#publication-result")).toContainText("Request recorded; waiting for a release operator");
  expect(fixture.calls.filter(call=>call.path===PATH&&call.method==="POST")).toHaveLength(1);
  expect(fixture.calls.some(call=>call.path===PATH+"/"+"1".repeat(64))).toBe(true);
  await expect(page.locator("#publication-recover-reference")).toHaveValue(reference);
  await expect(page.locator("#publication-request")).toBeDisabled();
  state.stored={...state.stored,status:"source_prepared",source_preparation:{candidate_source_sha:"b".repeat(40),
    history_sha256:"c".repeat(64),recorded_at:1795478401}};
  await page.locator("#publication-recover").click();
  await expect(page.locator("#publication-recovery-result")).toContainText("Local candidate source prepared");
  await expect(page.locator("#publication-recovery-result")).toContainText("production deployment unverified");
  expect(fixture.calls.filter(call=>call.path===PATH&&call.method==="POST")).toHaveLength(1);
});

for(const [name,mutate] of [
  ["claimed deployment",value=>({...value,deployed:true})],
  ["different review",value=>({...value,review_id:"0".repeat(64)})],
  ["different draft",value=>({...value,draft_id:"0".repeat(64)})],
  ["bad source",value=>({...value,base_source_sha:"x".repeat(40)})],
  ["nonsequential version",value=>({...value,candidate_registry_version:"5"})],
  ["wrong baseline",value=>({...value,baseline_sha256:"0".repeat(64)})],
  ["wrong target",value=>({...value,target:{...target,rule_id:"secret.wrong"}})],
  ["wrong changes",value=>({...value,changed_metadata:{title:{before:"Email rule",after:"Unreviewed title"}}})],
  ["mismatched writer",value=>({...value,writer_enabled:false})],
  ["wrong confirmation",value=>({...value,confirmation:"PUBLISH 00000000"})],
  ["invalid control",value=>({...value,expected_control_hash:"f".repeat(64)})],
]) test(`publication preview rejects ${name}`,async({page})=>{
  const {fixture}=await setup(page,{mutatePreview:mutate});
  await page.locator("#publication-preview").click();
  await expect(page.locator("#publication-target")).toContainText("Publication review integrity unavailable");
  await expect(page.locator("#publication-request")).toBeDisabled();
  expect(fixture.calls.filter(call=>call.path===PATH)).toHaveLength(0);
});

for(const [name,mutate] of [
  ["remote publication",value=>({...value,remote_published:true})],
  ["CI acceptance",value=>({...value,ci_verified:true})],
  ["deployment",value=>({...value,deployed:true})],
  ["missing integrity",value=>({...value,integrity_verified:false})],
  ["wrong reference",value=>({...value,action_reference:"other-action-reference-123"})],
  ["wrong reason",value=>({...value,reason:"Some different release reason"})],
  ["invented stage",value=>({...value,status:"deployed"})],
  ["unproven preparation",value=>({...value,status:"source_prepared"})],
  ["prepared source before intent",value=>({...value,status:"source_prepared",source_preparation:{
    candidate_source_sha:"b".repeat(40),history_sha256:"c".repeat(64),recorded_at:1795478399}})],
  ["unchanged candidate source",value=>({...value,status:"source_prepared",source_preparation:{
    candidate_source_sha:"a".repeat(40),history_sha256:"c".repeat(64),recorded_at:1795478401}})],
]) test(`publication receipt refuses ${name} and retains recovery reference`,async({page})=>{
  const {fixture}=await setup(page,{mutateReceipt:mutate});
  await ready(page);
  const reference=await page.locator("#publication-reference").inputValue();
  await page.locator("#publication-request").click();
  await expect(page.locator("#publication-result")).toContainText("Request outcome unavailable");
  await expect(page.locator("#publication-recover-reference")).toHaveValue(reference);
  await expect(page.locator("#publication-request")).toBeDisabled();
  expect(fixture.calls.filter(call=>call.path===PATH&&call.method==="POST")).toHaveLength(1);
});

test("lost response and refresh cannot submit another release",async({page})=>{
  const {fixture,state}=await setup(page);
  await ready(page);
  let calls=0;
  await page.route("**/v1/admin/catalog/publications",async route=>{
    calls++;state.stored=receipt(route.request().postDataJSON());await route.abort("failed");
  });
  const reference=await page.locator("#publication-reference").inputValue();
  await page.locator("#publication-request").click();
  await expect(page.locator("#publication-result")).toContainText("no automatic second submission");
  await page.locator("#refresh").click();
  await expect(page.locator("#status")).toContainText("Administration loaded");
  await page.locator("#nav-catalog").click();
  await expect(page.locator("#publication-recover-reference")).toHaveValue(reference);
  await expect(page.locator("#publication-preview")).toBeDisabled();
  await page.locator("#publication-request").evaluate(button=>button.click());
  expect(calls).toBe(1);
  await page.locator("#publication-recover").click();
  await expect(page.locator("#publication-recovery-result")).toContainText("Request recorded");
  expect(calls).toBe(1);
  expect(fixture.calls.filter(call=>call.path===PATH+"/recover")).toHaveLength(1);
});

test("unknown history is a read and never authorizes a replacement request",async({page})=>{
  const {fixture}=await setup(page,{recoveryStatus:404});
  await page.locator("#publication-recover-reference").fill("unknown-publication-reference");
  await page.locator("#publication-recover").click();
  await expect(page.locator("#publication-recovery-result")).toContainText("No verified request found");
  expect(fixture.calls.filter(call=>call.path===PATH)).toHaveLength(0);
});

test("late preview cannot revive a changed operation selection",async({page})=>{
  await setup(page);
  let release,started;
  const held=new Promise(resolve=>release=resolve),seen=new Promise(resolve=>started=resolve);
  await page.route("**/v1/admin/catalog/publications/preview",async route=>{started();await held;await route.fulfill(response(preview()));});
  await page.locator("#publication-preview").click();
  await seen;
  await page.locator("#publication-operation").selectOption("reverse_latest");
  const delivered=page.waitForResponse(result=>result.url().endsWith(PATH+"/preview"));
  release();
  await delivered;
  await expect(page.locator("#publication-confirm")).toBeDisabled();
  await expect(page.locator("#publication-target")).not.toContainText("Type PUBLISH");
});

test("late recovery cannot revive a withdrawn workspace snapshot",async({page})=>{
  await setup(page);
  const reference="retained-publication-reference";
  let release,started;
  const held=new Promise(resolve=>release=resolve),seen=new Promise(resolve=>started=resolve);
  await page.route("**/v1/admin/catalog/publications/recover",async route=>{
    started();await held;await route.fulfill(response(receipt({reason:"Reviewed release reason",action_reference:reference})));
  });
  await page.locator("#publication-recover-reference").fill(reference);
  await page.locator("#publication-recover").click();await seen;
  await page.locator("#refresh").click();await expect(page.locator("#status")).toContainText("Administration loaded");
  const delivered=page.waitForResponse(result=>result.url().endsWith(PATH+"/recover"));
  release();await delivered;
  await expect(page.locator("#publication-recovery-result")).toBeEmpty();
  await expect(page.locator("#publication-recover-reference")).toHaveValue(reference);
});

test("request stays single-flight across refresh and discards a late signed-out response",async({page})=>{
  await setup(page);await ready(page);
  let release,started,calls=0;
  const held=new Promise(resolve=>release=resolve),seen=new Promise(resolve=>started=resolve);
  await page.route("**/v1/admin/catalog/publications",async route=>{
    calls++;started();const value=receipt(route.request().postDataJSON());await held;await route.fulfill(response(value));
  });
  await page.locator("#publication-request").click();await seen;
  await page.locator("#refresh").click();await expect(page.locator("#status")).toContainText("Administration loaded");
  await page.locator("#nav-catalog").click();
  await page.locator("#publication-request").evaluate(button=>button.click());
  expect(calls).toBe(1);
  await page.locator("#logout").click();
  const delivered=page.waitForResponse(result=>result.url().endsWith(PATH));
  release();await delivered;
  await expect(page.locator("#app")).toBeHidden();
  await expect(page.locator("#publication-result")).toBeEmpty();
  await expect(page.locator("#publication-reference")).toHaveValue("");
  await expect(page.locator("#publication-recover-reference")).toHaveValue("");
});

test("baseline cannot enable requests",async({page})=>{
  const {fixture}=await setup(page,{writer:false});
  await page.locator("#publication-preview").click();
  await expect(page.locator("#publication-target")).toContainText("compatible deployment and the completed release workflow");
  await expect(page.locator("#publication-confirm")).toBeDisabled();
  await expect(page.locator("#publication-request")).toBeDisabled();
  expect(fixture.calls.filter(call=>call.path===PATH)).toHaveLength(0);
});

test("occupied global control cannot enable another request",async({page})=>{
  const {fixture}=await setup(page,{mutatePreview:value=>({...value,control_available:false,expected_control_hash:"9".repeat(64)})});
  await page.locator("#publication-preview").click();
  await expect(page.locator("#publication-target")).toContainText("A catalog request already owns source preparation");
  await expect(page.locator("#publication-confirm")).toBeDisabled();
  await expect(page.locator("#publication-request")).toBeDisabled();
  expect(fixture.calls.filter(call=>call.path===PATH)).toHaveLength(0);
});

test("publication form remains readable at mobile width and unsafe reason cannot be submitted",async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await setup(page);await ready(page);
  await page.locator("#publication-reason").fill("Hidden\u202erelease reason");
  await expect(page.locator("#publication-request")).toBeDisabled();
  await page.locator("#publication-reason").fill("  Misleading space release reason");
  await expect(page.locator("#publication-request")).toBeDisabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
