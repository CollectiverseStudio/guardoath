"use strict";
const {expect}=require("@playwright/test");
const {execFileSync}=require("node:child_process");

async function reviewedPublication(page,title) {
  const answers=[];
  const handler=async dialog=>{
    if(dialog.type()==="confirm") await dialog.accept();
    else {expect(answers.length).toBeGreaterThan(0);await dialog.accept(answers.shift());}
  };
  page.on("dialog",handler);
  await page.locator("#nav-catalog").click();
  const entry=page.locator("#categories li").filter({has:page.locator("strong").filter({hasText:/^pii\.email_address$/})});
  await entry.evaluate(node=>{let parent=node.parentElement;while(parent){if(parent.tagName==="DETAILS")parent.open=true;parent=parent.parentElement;}});
  await entry.getByRole("button",{name:"Preview description",exact:true}).click();
  await page.locator("#proposal-title").fill(title);
  await page.locator("#proposal-go").click();
  await expect(page.locator("#proposal-save")).toBeEnabled();
  answers.push("Review the synthetic source release proposal");
  await page.locator("#proposal-save").click();
  await expect(page.locator("#proposal-result")).toContainText("Signed draft saved");
  await page.locator("#draft-inspect").click();
  await expect(page.locator("#draft-review")).toBeEnabled();
  const draftId=await page.locator("#draft-id").inputValue();
  answers.push("REVIEW "+draftId.slice(-8),"Confirm synthetic description review evidence");
  await page.locator("#draft-review").click();
  await expect(page.locator("#draft-result")).toContainText("Immutable signed review verified");
  const response=page.waitForResponse(r=>r.url().endsWith("/manifest"));
  await page.locator("#review-manifest").click();
  const manifest=await (await response).json();
  await expect(page.locator("#publication-preview")).toBeEnabled();
  expect(answers).toEqual([]);
  page.off("dialog",handler);
  return manifest;
}

function publicationState() {
  const program=[
    "import json,sys",
    "sys.path.insert(0,'tests')",
    "from admin_test_support import AdminTestDynamo,ADMIN_BROWSER_AUDIT_KEY",
    "from guardoath.admin_catalog_publication_state import DynamoCatalogPublicationState,document_hash",
    "from guardoath.admin_catalog_drafts import AdminCatalogDrafts",
    "db=AdminTestDynamo(sys.argv[1])",
    "state=DynamoCatalogPublicationState(db,'audit_table',ADMIN_BROWSER_AUDIT_KEY)",
    "drafts=AdminCatalogDrafts(db,'audit_table',ADMIN_BROWSER_AUDIT_KEY)",
    "control=state.read_control()",
    "intent,prepared=state.history(control['request_id']) if control else (None,None)",
    "review=drafts.read_review(intent['actor_sub'],intent['review_id']) if intent else None",
    "draft=drafts.read(intent['actor_sub'],intent['draft_id']) if intent else None",
    "assert intent is None or (document_hash(review)==intent['review_sha256'] and document_hash(draft)==intent['draft_sha256'])",
    "print(json.dumps({'control':control,'intent':intent,'prepared':prepared}))",
    "db.close()",
  ].join("\n");
  return JSON.parse(execFileSync(process.env.PYTHON||"python",["-c",program,process.env.GUARDOATH_ADMIN_TEST_DB],{encoding:"utf8"}));
}

function completePublication(requestId) {
  return JSON.parse(execFileSync(process.env.PYTHON||"python",[
    "tests/admin_publication_lifecycle_support.py",process.env.GUARDOATH_ADMIN_TEST_DB,requestId],{encoding:"utf8"}));
}
module.exports={reviewedPublication,publicationState,completePublication};
