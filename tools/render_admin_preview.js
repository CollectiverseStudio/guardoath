"use strict";

// Build an explicitly isolated, offline review copy of the REAL owner UI.
// The production HTML/JS is unchanged. Synthetic HTTP and OAuth state live only
// inside this generated file's lexical scope; nothing reaches an AWS provider.
const fs = require("node:fs");
const path = require("node:path");
const {loadAdminSource, createAdminFixture} = require("../tests/browser/admin_fixture");

const output = process.argv[2];
if (!output || path.extname(output).toLowerCase() !== ".html") {
  throw new Error("Usage: node tools/render_admin_preview.js /output/guardoath-admin-preview.html");
}
const source = loadAdminSource();
const sample = createAdminFixture({source});
const escapeScript = value => value.replace(/<\/script/gi, "<\\/script");
const bootstrap = `(() => {
  const TOKEN = ${JSON.stringify(sample.token)};
  const RECEIPT = ${JSON.stringify(sample.receipt)};
  const FIXTURE_TIME = "2026-11-20T12:00:00Z";
  ${createAdminFixture.toString()}
  const fixture = createAdminFixture({source:{}});
  const fetch = async (url, options = {}) => {
    const response = fixture.resolve(url, options.method || "GET", options.headers || {}, options.body || null);
    return new Response(response.body, {status:response.status, headers:{"Content-Type":response.contentType}});
  };
  const pending = new Map([["go_admin_pkce", JSON.stringify({state:"fixture-state",verifier:"v".repeat(43)})]]);
  const sessionStorage = {getItem:key => pending.get(key) ?? null,
    setItem:(key,value) => pending.set(key,String(value)), removeItem:key => pending.delete(key)};
  const location = {search:"?code=fixture-code&state=fixture-state",assign:() => window.location.reload()};
  const history = {replaceState:() => {}};
  document.getElementById("login-start").textContent = "Reopen sample preview";
  document.getElementById("preview-reset").addEventListener("click", () => window.location.reload());
  ${source._JS}
})();`;
const banner = `<aside id="preview-notice" aria-label="Preview notice"><div><strong>INTERACTIVE PREVIEW</strong><span>Sample data · Not connected to production</span><small>This is the actual admin interface. Accounts, invoices and audit receipts are synthetic; no provider requests or production changes are possible.</small></div><button id="preview-reset" type="button">Reset preview</button></aside>`;
const style = `<style>#preview-notice{background:#fff4d8;color:#573e11;border-bottom:1px solid #e1c77e;padding:14px 24px;display:flex;justify-content:space-between;align-items:center;gap:18px;font-family:system-ui,sans-serif}#preview-notice strong{font-size:11px;letter-spacing:1.2px;margin-right:13px}#preview-notice span{font-size:13px;font-weight:650}#preview-notice small{display:block;margin-top:5px;max-width:900px;line-height:1.5;font-size:11px}#preview-reset{flex-shrink:0;border:1px solid #c3a459;background:transparent;color:#573e11;padding:8px 12px;font-size:12px}@media(max-width:720px){#preview-notice{padding:12px 14px;align-items:flex-start;gap:8px}#preview-notice strong,#preview-notice span{display:block}#preview-reset{font-size:10px;padding:6px 8px}#preview-notice small{font-size:10px}}</style>`;
const html = source._PAGE.replace("</head>", style + "</head>")
  .replace("<body>", "<body>" + banner)
  .replace('<script src="/admin/client.js" defer></script>', "<script>" + escapeScript(bootstrap) + "</script>");
if (html === source._PAGE || html.includes('src="/admin/client.js"')) throw new Error("Admin source preview extraction failed");
fs.mkdirSync(path.dirname(path.resolve(output)), {recursive:true});
fs.writeFileSync(output, html, "utf8");
console.log(JSON.stringify({file:path.resolve(output),bytes:Buffer.byteLength(html),production_connected:false}));
