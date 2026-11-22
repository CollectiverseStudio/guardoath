"use strict";

const {test, expect} = require("@playwright/test");
const {createHash} = require("node:crypto");
const {execFileSync} = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");

async function connectDemo(page) {
  await page.goto("/dashboard");
  await expect(page.locator("#dashboard-app")).toBeVisible();
  await page.locator("#demo-user").selectOption("demo4");
  await page.locator("#demo-key").click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  await expect(page.locator("#api-copy-request")).toBeEnabled();
  return page.locator("#demo-key-value").inputValue();
}

function receipt(request, extra = {}) {
  const body = request.postDataJSON();
  const stage = new URL(request.url()).pathname.includes("/output") ? "output" : "input";
  const value = {application_id:body.application_id, policy_version:body.policy_version, stage,
    evaluation_status:"complete", recommended_action:"allow", effective_action:"allow",
    simulated:false, total_latency_ms:2.5, findings:[], groups:[],
    checker_results:[{checker_id:"synthetic-browser-check",status:"passed",simulated:false,findings:[]}]};
  if (body.attachments_b64) {
    value.attachments = body.attachments_b64.map((bytes,index) => ({
      index, kind:"csv", reason:"inspected", evaluation_status:"complete",
      byte_count:Buffer.from(bytes,"base64").length
    }));
    value.checker_results.push(...body.attachments_b64.map((_,index) => ({
      checker_id:"synthetic-file-check", attachment_index:index,
      status:"passed",simulated:false,findings:[]
    })));
  }
  return {...value,...extra};
}

// This test executes the actual emitted examples against local capture stubs.
// It does not need a browser, a network connection, or any real credential.
test("generated cURL Python and Node examples preserve adversarial body bytes and hash without code execution", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(),"guardoath-codegen-"));
  try {
    const marker = path.join(dir,"content-was-executed");
    const backtick = String.fromCharCode(96);
    const content = [
      "Quotes: \"double\" and 'single'; backslash: \\ and newline:",
      "$(touch " + marker + ")", backtick + "touch " + marker + backtick,
      '"; throw new Error("content must remain data"); //',
      "Café 東京 مرحبًا 🔒 \u2028 \u2029"
    ].join("\n");
    const source = fs.readFileSync(path.resolve(__dirname,"../../guardoath/static/dashboard.js"),"utf8");
    const builder = source.slice(source.indexOf("async function buildCheckRequest("),
      source.indexOf("async function execute("));
    const codegen = source.slice(source.indexOf("function inspectionText("),
      source.indexOf("function renderApiInspection("));
    const browserKey = "synthetic-browser-key-never-copied";
    const browserSession = "synthetic-browser-session-never-copied";
    const fields = {"input-content":content,"input-format":"text",policy:"tenant",
      "request-context":"Context is accepted but not checked.", "key-new":"synthetic-new-key-never-copied"};
    const sandbox = {
      application:{application_id:"sample-application"}, key:browserKey, SESSION:"session ",
      location:{origin:"https://guardoath.example.test"},
      sessionStorage:{getItem:() => browserSession},
      $:id => ({value:fields[id] || ""}),
      selectedAttachmentBytes:async () => [Buffer.from("name,value\nsafe,1\n").toString("base64")]
    };
    vm.createContext(sandbox);
    vm.runInContext(builder + "\n" + codegen,sandbox);
    const built = await sandbox.buildCheckRequest("input");
    const body = JSON.stringify(built.body);
    const preview = {path:built.path,bodyText:body};
    const syntheticKey = "synthetic-example-environment-key";
    const capture = path.join(dir,"capture.json");
    const env = {PATH:process.env.PATH, GUARDOATH_API_KEY:syntheticKey, CAPTURE:capture};
    const expectedHash = createHash("sha256").update(body,"utf8").digest("hex");
    const python = process.env.PYTHON || "python";

    for (const language of ["curl","python","node"]) {
      const example = sandbox.integrationExample(language,preview);
      expect(example).not.toContain(browserKey);
      expect(example).not.toContain(browserSession);
      expect(example).not.toContain(fields["key-new"]);
      expect(example).toContain("GUARDOATH_API_KEY");
      let captured;
      if (language === "node") {
        const prefix = [
          'import { writeFileSync } from "node:fs";',
          "globalThis.fetch = async (url, options) => {",
          "  writeFileSync(process.env.CAPTURE, JSON.stringify({url,...options}));",
          '  return {status:200,text:async () => "{}"};',
          "};"
        ].join("\n");
        execFileSync(process.execPath,["--input-type=module"],{input:prefix+"\n"+example,env,cwd:dir});
        captured = JSON.parse(fs.readFileSync(capture,"utf8"));
        expect(captured.redirect).toBe("error");
      } else if (language === "python") {
        const prefix = [
          "import json, os, pathlib, urllib.request",
          "class CapturedResponse:",
          "    status = 200",
          "    def __enter__(self): return self",
          "    def __exit__(self, *args): return False",
          "    def read(self): return b'{}'",
          "class CapturedOpener:",
          "    def open(self, request, timeout):",
          "        value = dict(url=request.full_url, body=request.data.decode('utf-8'), headers=dict(request.header_items()))",
          "        pathlib.Path(os.environ['CAPTURE']).write_text(json.dumps(value), encoding='utf-8')",
          "        return CapturedResponse()",
          "def capture_opener(*handlers):",
          "    assert len(handlers) == 1",
          "    assert handlers[0].redirect_request(None, None, 302, '', {}, 'https://other.invalid') is None",
          "    return CapturedOpener()",
          "urllib.request.build_opener = capture_opener"
        ].join("\n");
        execFileSync(python,["-c",prefix+"\n"+example],{env,cwd:dir});
        captured = JSON.parse(fs.readFileSync(capture,"utf8"));
      } else {
        const stub = path.join(dir,"curl");
        fs.writeFileSync(stub,[
          "#!/usr/bin/env python3", "import json, os, pathlib, sys",
          "pathlib.Path(os.environ['CAPTURE']).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')"
        ].join("\n"));
        fs.chmodSync(stub,0o755);
        execFileSync("/bin/sh",[],{input:example,env:{...env,PATH:dir+path.delimiter+env.PATH},cwd:dir});
        const args = JSON.parse(fs.readFileSync(capture,"utf8"));
        expect(args).not.toContain("--location");
        expect(args).not.toContain("-L");
        const headers = {};
        args.forEach((arg,index) => {
          if (arg !== "--header") return;
          const header = args[index+1], colon = header.indexOf(":");
          headers[header.slice(0,colon)] = header.slice(colon+1).trim();
        });
        captured = {url:args[2],body:args[args.indexOf("--data-binary")+1],headers};
      }
      const headers = Object.fromEntries(Object.entries(captured.headers).map(([k,v]) => [k.toLowerCase(),v]));
      expect(captured.url).toBe("https://guardoath.example.test/v1/checks/input/attachments");
      expect(captured.body).toBe(body);
      expect(headers["x-amz-content-sha256"]).toBe(expectedHash);
      expect(headers["x-guardoath-key"]).toBe(syntheticKey);
      expect(headers["content-type"]).toBe("application/json");
      expect(fs.existsSync(marker)).toBe(false);
    }
    const masked = sandbox.integrationExample("node",{path:built.path,
      bodyText:JSON.stringify({content:browserKey+" "+browserSession+" "+fields["key-new"]})});
    expect(masked).not.toContain(browserKey);
    expect(masked).not.toContain(browserSession);
    expect(masked).not.toContain(fields["key-new"]);
    expect(masked).toContain("AUTHENTICATION_VALUE_REDACTED");
    const escapedKey = [...browserKey].map(char => "\\u"+char.charCodeAt(0).toString(16).padStart(4,"0")).join("");
    const escapedResponse = '{"detail":"' + escapedKey + '"}';
    const safeResponse = sandbox.inspectionText(escapedResponse);
    expect(safeResponse).not.toContain(escapedKey);
    expect(sandbox.prettyInspection(safeResponse)).not.toContain(browserKey);
    expect(sandbox.prettyInspection(safeResponse)).toContain("AUTHENTICATION_VALUE_REDACTED");
  } finally { fs.rmSync(dir,{recursive:true,force:true}); }
});

test("inspector drops stale owner replies and copies while attachment previews are pending", async () => {
  const source=fs.readFileSync(path.resolve(__dirname,"../../guardoath/static/dashboard.js"),"utf8");
  const api=source.slice(source.indexOf("async function api("),source.indexOf("function coverage("));
  const builder=source.slice(source.indexOf("async function buildCheckRequest("),source.indexOf("async function execute("));
  const helpers=source.slice(source.indexOf("function currentInspectionOwner("),
    source.indexOf('for (const link of document.querySelectorAll(".workbench-nav a"))'));
  const nodes=new Map();
  const node=id => {
    if(!nodes.has(id)) nodes.set(id,{value:"",textContent:"",disabled:false});
    return nodes.get(id);
  };
  node("inspector-stage").value="input"; node("policy").value="tenant";
  node("input-format").value="text"; node("input-content").value="private previous owner request";
  let session="synthetic-owner-a", finishResponse;
  const copied=[];
  const sandbox={key:"session "+session,SESSION:"session ",revision:1,
    application:{application_id:"sample",tenant:{tenant_id:"tenant-a"}},
    apiInspection:{input:null,output:null},apiPreview:null,inspectorSequence:0,
    sessionStorage:{getItem:() => session},$:node,performance,stageNames:{input:"Input",output:"Output"},
    location:{origin:"https://guardoath.example.test"},
    navigator:{clipboard:{writeText:async value => {copied.push(value);}}},
    requestHeaders:async()=>({}),selectedAttachmentBytes:async()=>null,
    send:async()=>new Promise(resolve=>{finishResponse=resolve;})};
  vm.createContext(sandbox); vm.runInContext(api+"\n"+builder+"\n"+helpers,sandbox);
  await sandbox.refreshRequestInspector();
  expect(node("api-request-json").textContent).toContain("private previous owner request");
  const pending=sandbox.api("/v1/checks/input",{content:"private previous owner request"},sandbox.key);
  await new Promise(resolve=>setImmediate(resolve));
  session="synthetic-owner-b";
  finishResponse({status:200,ok:true,clone:()=>({text:async()=>JSON.stringify({private_result:"previous owner"})}),
    json:async()=>({private_result:"previous owner"})});
  let failure="";
  try { await pending; } catch (error) { failure=error.message; }
  expect(failure).toContain("connection or request changed");
  expect(sandbox.apiInspection.input).toBeNull();
  expect(sandbox.apiPreview).toBeNull();
  expect(node("api-request-json").textContent).not.toContain("private previous owner request");
  expect(node("api-response-json").textContent).not.toContain("previous owner");
  expect(node("api-copy-code").disabled).toBe(true);

  session="synthetic-owner-a";
  await sandbox.refreshRequestInspector();
  session="synthetic-owner-b";
  await sandbox.copyInspection("request");
  expect(copied).toEqual([]);
  expect(node("api-copy-request").disabled).toBe(true);

  session="synthetic-owner-a";
  await sandbox.refreshRequestInspector();
  node("api-response-json").textContent="private previous owner response";
  let finishFiles;
  sandbox.selectedAttachmentBytes=async()=>new Promise(resolve=>{finishFiles=resolve;});
  const preview=sandbox.refreshRequestInspector();
  for(const id of ["api-request-json","api-response-json","api-code-example"])
    expect(node(id).textContent).not.toContain("private previous owner");
  session="synthetic-owner-b"; finishFiles(null); await preview;
  expect(sandbox.apiPreview).toBeNull();
  for(const id of ["api-request-json","api-response-json","api-code-example"])
    expect(node(id).textContent).not.toContain("private previous owner");
  expect(node("api-copy-code").disabled).toBe(true);
  session="synthetic-owner-a";
  const sameOwner=sandbox.currentInspectionOwner();
  sandbox.revision++;
  expect(sameOwner()).toBe(true); // keepInput may retain a same-owner Input receipt.
});

test("API workbench prioritizes requests and preserves content through real section navigation", async ({page}) => {
  await connectDemo(page);
  await expect(page.getByRole("heading",{name:"API test console",exact:true})).toBeVisible();
  await expect(page.locator(".capability-note")).toContainText("Model and agent execution are not connected");
  await expect(page.locator("#demo-key-value")).toHaveAttribute("type","password");
  const order = await page.evaluate(() => ["api-test","policy-workspace","console-help"]
    .map(id => document.getElementById(id).getBoundingClientRect().top + scrollY));
  expect(order[0]).toBeLessThan(order[1]);
  expect(order[1]).toBeLessThan(order[2]);
  await page.locator("#input-content").fill("Keep this request while reviewing policy.");
  await page.getByRole("navigation",{name:"Console sections"}).getByRole("link",{name:"Policy",exact:true}).click();
  await expect(page.locator("#policy-workspace")).toBeFocused();
  await page.getByRole("navigation",{name:"Console sections"}).getByRole("link",{name:"Test",exact:true}).click();
  await expect(page.locator("#api-test")).toBeFocused();
  await expect(page.locator("#input-content")).toHaveValue("Keep this request while reviewing policy.");
  await expect(page.locator("#run-output")).toBeDisabled();
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const columns = await page.locator(".workbench-columns").evaluate(el => getComputedStyle(el).gridTemplateColumns);
  expect(columns.trim().split(/\s+/)).toHaveLength(1);
  const ordered = await page.evaluate(() => {
    const elements=["wizard-input","wizard-input-check","wizard-output","wizard-output-check"]
      .map(id => document.getElementById(id));
    return {tops:elements.map(el => el.getBoundingClientRect().top),
      domOrder:elements.slice(1).every((el,index) =>
        Boolean(elements[index].compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)),
      endpointTop:document.getElementById("api-endpoint").getBoundingClientRect().top};
  });
  expect(ordered.domOrder).toBe(true);
  expect(ordered.endpointTop).toBeLessThan(ordered.tops[0]);
  for (let i=1;i<ordered.tops.length;i++) expect(ordered.tops[i]).toBeGreaterThan(ordered.tops[i-1]);
});

test("inspector matches actual sent JSON and SHA256 and separates HTTP browser and engine evidence", async ({page}) => {
  await page.addInitScript(() => Object.defineProperty(navigator,"clipboard",
    {value:{writeText:async text => { window.syntheticCopiedText = text; }}}));
  const key = await connectDemo(page);
  let sent;
  await page.route("**/v1/checks/input",async route => {
    sent = route.request();
    await new Promise(resolve => setTimeout(resolve,35));
    await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(receipt(sent))});
  });
  await page.locator("#input-content").fill("Café \"quoted\" \\ data\n東京 🔒");
  await page.locator(".request-options > summary").click();
  await page.locator("#request-context").fill("Metadata only — not checked.");
  await page.locator("#run-input").click();
  await expect(page.locator("#api-http-status")).toHaveText("200");
  const body = sent.postData();
  expect(sent.headers()["x-amz-content-sha256"]).toBe(createHash("sha256").update(body).digest("hex"));
  expect(sent.postDataJSON()).toMatchObject({language:"en",request_context:"Metadata only — not checked."});
  expect(JSON.parse(await page.locator("#api-request-json").textContent())).toEqual(sent.postDataJSON());
  await expect(page.locator("#api-endpoint")).toHaveText("/v1/checks/input");
  await expect(page.locator("#api-engine-latency")).toHaveText("2.5 ms");
  expect(parseFloat(await page.locator("#api-browser-latency").textContent())).toBeGreaterThan(2.5);
  expect(JSON.parse(await page.locator("#api-response-json").textContent()).stage).toBe("input");
  await page.locator(".integration-examples > summary").click();
  for (const language of ["curl","python","node"]) {
    await page.locator("#api-code-language").selectOption(language);
    await expect(page.locator("#api-code-example")).toContainText("GUARDOATH_API_KEY");
    await expect(page.locator("#api-code-example")).not.toContainText(key);
    await page.locator("#api-copy-code").click();
    const copied = await page.evaluate(() => window.syntheticCopiedText);
    expect(copied).toBe(await page.locator("#api-code-example").textContent());
    expect(copied).not.toContain(key);
  }
  await page.locator("#api-copy-request").click();
  expect(await page.evaluate(() => window.syntheticCopiedText)).toBe(body);
});

test("HTTP errors remain inspectable while credentials are masked and output remains held", async ({page}) => {
  const key = await connectDemo(page);
  const session = "synthetic-session-secret-not-for-copy";
  await page.evaluate(value => sessionStorage.setItem("guardoath-session",value),session);
  const escapedKey = [...key].map(char => "\\u"+char.charCodeAt(0).toString(16).padStart(4,"0")).join("");
  await page.route("**/v1/checks/input",route => route.fulfill({status:422,
    contentType:"application/json",body:JSON.stringify({detail:"Rejected "+key+" "+session}).replace(key,escapedKey)}));
  await page.locator("#input-content").fill("Deliberate secret handling test "+key+" "+session);
  await page.locator("#run-input").click();
  await expect(page.locator("#api-http-status")).toHaveText("422");
  await expect(page.locator("#api-response-json")).toContainText("AUTHENTICATION_VALUE_REDACTED");
  await expect(page.locator("#api-inspection-state")).toContainText("differ from the original body");
  for (const id of ["api-request-json","api-response-json","api-code-example"]) {
    await expect(page.locator("#"+id)).not.toContainText(key);
    await expect(page.locator("#"+id)).not.toContainText(session);
  }
  await expect(page.locator("#result-status")).toHaveText("Check failed");
  await expect(page.locator("#run-output")).toBeDisabled();
  await expect(page.locator("#answer")).toBeHidden();
});

test("attachment inspector and execution share the actual byte-bearing endpoint", async ({page}) => {
  await connectDemo(page);
  const bytes = Buffer.from("name,value\nsafe,1\n","utf8");
  let sent;
  await page.route("**/v1/checks/input/attachments",async route => {
    sent=route.request();
    await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(receipt(sent))});
  });
  await page.locator("#input-attachments").setInputFiles({name:"sample.csv",mimeType:"text/csv",buffer:bytes});
  await expect(page.locator("#api-endpoint")).toHaveText("/v1/checks/input/attachments");
  await page.locator("#run-input").click();
  await expect(page.locator("#api-http-status")).toHaveText("200");
  expect(sent.postDataJSON().attachments_b64).toEqual([bytes.toString("base64")]);
  expect(JSON.parse(await page.locator("#api-request-json").textContent())).toEqual(sent.postDataJSON());
  expect(sent.headers()["x-amz-content-sha256"]).toBe(createHash("sha256").update(sent.postData()).digest("hex"));
  await expect(page.locator("#input-results")).toContainText("Actual file inspection");
});

test("pending policy edits stay visible across navigation and are saved before the real test request", async ({page}) => {
  await connectDemo(page);
  await page.locator("#tenant-reset").click();
  await expect(page.locator("#tenant-message")).toContainText("Reset to the demo defaults");
  const order=[];
  page.on("request",request => {
    if(request.method()==="POST" && /\/v1\/(?:tenants\/current\/checks|checks\/input)$/.test(new URL(request.url()).pathname))
      order.push(new URL(request.url()).pathname);
  });
  await page.route("**/v1/checks/input",route => route.fulfill({status:200,
    contentType:"application/json",body:JSON.stringify(receipt(route.request()))}));
  await page.locator("#tenant-check-input-secrets").selectOption("flag");
  await expect(page.locator("#workbench-pending")).toBeVisible();
  await expect(page.locator("#run-input")).toHaveText("Save changes and run input checks →");
  await page.getByRole("navigation",{name:"Console sections"}).getByRole("link",{name:"Test",exact:true}).click();
  await expect(page.locator("#tenant-check-input-secrets")).toHaveValue("flag");
  expect(order).toEqual([]);
  await page.locator("#run-input").click();
  await expect(page.locator("#api-http-status")).toHaveText("200");
  expect(order).toEqual(["/v1/tenants/current/checks","/v1/checks/input"]);
  await expect(page.locator("#workbench-pending")).toBeHidden();
  await expect(page.locator("#run-input")).toHaveText("Run input checks →");
  await page.locator("#tenant-reset").click();
  await expect(page.locator("#tenant-message")).toContainText("Reset to the demo defaults");
});

test("disconnect discards a late response instead of restoring the previous request or credential", async ({page}) => {
  await connectDemo(page);
  let release;
  const held = new Promise(resolve => { release=resolve; });
  await page.route("**/v1/checks/input",async route => {
    await held;
    await route.fulfill({status:200,contentType:"application/json",
      body:JSON.stringify(receipt(route.request(),{diagnostic_marker:"late private response"}))});
  });
  await page.locator("#run-input").click();
  await expect(page.locator("#result-status")).toHaveText("Checking…");
  await page.locator("#disconnect").click();
  release();
  await page.waitForFunction(() => busy === false);
  await expect(page.locator("#context-application")).toHaveText("Not connected");
  await expect(page.locator("#api-http-status")).toHaveText("Not sent");
  await expect(page.locator("#api-response-json")).not.toContainText("late private response");
  await expect(page.locator("#api-copy-code")).toBeDisabled();
  await expect(page.locator("#answer")).toBeHidden();
});

test("a replaced customer session cannot display an old successful response or copy its pending request", async ({page}) => {
  await page.addInitScript(() => Object.defineProperty(navigator,"clipboard",
    {value:{writeText:async text => { window.syntheticCopiedText = text; }}}));
  await connectDemo(page);
  await page.evaluate(() => {
    key="session synthetic-owner-a";
    sessionStorage.setItem("guardoath-session","synthetic-owner-a");
    reset();
  });
  await page.locator("#input-content").fill("Private request belonging to the previous owner.");
  let release;
  const held=new Promise(resolve=>{release=resolve;});
  await page.route("**/v1/checks/input",async route => {
    await held;
    await route.fulfill({status:200,contentType:"application/json",
      body:JSON.stringify(receipt(route.request(),{private_marker:"previous-owner-result"}))});
  });
  await page.locator("#run-input").click();
  await expect(page.locator("#result-status")).toHaveText("Checking…");
  await page.evaluate(() => sessionStorage.setItem("guardoath-session","synthetic-owner-b"));
  release();
  await page.waitForFunction(() => busy === false);
  await expect(page.locator("#result-status")).toHaveText("Check failed");
  await expect(page.locator("#api-request-json")).not.toContainText("previous owner");
  await expect(page.locator("#api-response-json")).not.toContainText("previous-owner-result");
  await expect(page.locator("#api-copy-code")).toBeDisabled();
  await expect(page.locator("#answer")).toBeHidden();
  await page.evaluate(() => {
    key="session synthetic-owner-b"; reset();
  });
  await expect(page.locator("#api-copy-request")).toBeEnabled();
  await page.evaluate(() => sessionStorage.setItem("guardoath-session","synthetic-owner-c"));
  await page.locator("#api-copy-request").click();
  await expect(page.locator("#api-copy-status")).toContainText("No previous request or example was copied");
  expect(await page.evaluate(() => window.syntheticCopiedText)).toBeUndefined();
});

for (const mode of ["API key","synthetic customer session"]) {
  test("credential-bearing validation refuses redirects for " + mode, async ({page}) => {
    const demoKey = await connectDemo(page);
    const syntheticSession = "synthetic-redirect-test-session";
    if (mode === "synthetic customer session") {
      // Explicitly synthetic header-path coverage; this does not assert Cognito acceptance.
      await page.evaluate(value => {
        sessionStorage.setItem("guardoath-session",value);
        key = "session " + value;
      },syntheticSession);
    }
    const forwarded = [];
    let initialHeaders;
    await page.route("https://other.example.invalid/**",async route => {
      forwarded.push(route.request().headers());
      await route.fulfill({status:200,body:"{}"});
    });
    await page.route("**/v1/checks/input",async route => {
      initialHeaders=route.request().headers();
      await route.fulfill({status:302,headers:{location:"https://other.example.invalid/capture"}});
    });
    await page.locator("#run-input").click();
    await expect(page.locator("#result-status")).toHaveText("Check failed");
    await expect(page.locator("#api-http-status")).toHaveText("No response");
    expect(initialHeaders[mode === "API key" ? "x-guardoath-key" : "x-guardoath-session"])
      .toBe(mode === "API key" ? demoKey : syntheticSession);
    expect(forwarded).toEqual([]);
    await expect(page.locator("#run-output")).toBeDisabled();
    await expect(page.locator("#answer")).toBeHidden();
  });
}
