"""Opt-in Cognito PKCE browser entry for GuardOath's read-only operator console.

This public login shell contains no tenant data. An exchanged Cognito access token
is returned only after the SAME independent owner/MFA check used by admin APIs.
The browser keeps that token in memory, never in persistent browser storage.
Requires a separate Cognito app-client callback registered for /admin/login.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from threading import BoundedSemaphore, Lock
from collections import deque
from time import monotonic
from typing import Callable
from urllib.parse import urlencode
import urllib.error
import urllib.request

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from guardoath.admin_console import VerifiedAdmin


SAFE_DOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.auth\.[a-z]{2}(?:-[a-z]+)+-\d\.amazoncognito\.com$")
SAFE_CLIENT = re.compile(r"^[a-z0-9]{1,128}$")
SAFE_CODE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
SAFE_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


@dataclass(frozen=True)
class BrowserAuthConfig:
    domain: str
    client_id: str
    redirect_uri: str

    def __post_init__(self):
        if not SAFE_DOMAIN.fullmatch(self.domain) or not SAFE_CLIENT.fullmatch(self.client_id):
            raise ValueError("Invalid Cognito administrative app client")
        if not (self.redirect_uri == "https://guardoath.com/admin/login" or
                re.fullmatch(r"http://(?:localhost|127\.0\.0\.1):\d+/admin/login", self.redirect_uri)):
            raise ValueError("Admin callback must be a permitted HTTPS callback or local test URL")


class CodeExchange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=256)
    code_verifier: str = Field(min_length=43, max_length=128)


class ExchangeUnavailable(Exception):
    pass


class BoundExchange:
    """Small bounded back-end Cognito exchange. No URL comes from the browser."""

    def __init__(self, config: BrowserAuthConfig, sender: Callable[[str, bytes], dict] | None = None,
                 clock: Callable[[], float] = monotonic):
        self.config = config
        self._sender = sender or self._send
        self._clock = clock
        self._times = deque()
        self._lock = Lock()
        self._slots = BoundedSemaphore(3)

    @staticmethod
    def _send(url: str, body: bytes) -> dict:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(NoRedirect())
        request = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with opener.open(request, timeout=3.0) as response:
                if response.status != 200:
                    raise ExchangeUnavailable()
                raw = response.read(16385)
        except (OSError, urllib.error.HTTPError, TimeoutError) as exc:
            raise ExchangeUnavailable() from exc
        if len(raw) > 16384:
            raise ExchangeUnavailable()
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ExchangeUnavailable() from exc
        if not isinstance(result, dict):
            raise ExchangeUnavailable()
        return result

    def exchange(self, code: str, verifier: str) -> str | None:
        if not SAFE_CODE.fullmatch(code) or not SAFE_VERIFIER.fullmatch(verifier):
            return None
        with self._lock:
            now = self._clock()
            while self._times and now - self._times[0] >= 60:
                self._times.popleft()
            if len(self._times) >= 30:
                raise ExchangeUnavailable()
            if not self._slots.acquire(blocking=False):
                raise ExchangeUnavailable()
            self._times.append(now)
        try:
            body = urlencode({"grant_type": "authorization_code", "client_id": self.config.client_id,
                              "redirect_uri": self.config.redirect_uri,
                              "code": code, "code_verifier": verifier}).encode("ascii")
            result = self._sender("https://" + self.config.domain + "/oauth2/token", body)
            if not isinstance(result, dict):
                raise ExchangeUnavailable()
            token = result.get("access_token")
            if not isinstance(token, str) or not 16 <= len(token) <= 8192:
                return None
            return token
        except (OSError, ValueError) as exc:
            raise ExchangeUnavailable() from exc
        finally:
            self._slots.release()


def create_browser_router(config: BrowserAuthConfig, exchange: BoundExchange,
                          verify_owner: Callable[[str], VerifiedAdmin | None]) -> APIRouter:
    """Add the public shell/config and a fail-closed owner-only code exchange."""
    if exchange.config != config or not callable(verify_owner):
        raise ValueError("Browser auth services must match")
    router = APIRouter()

    @router.get("/admin/login", include_in_schema=False)
    def login_page():
        return HTMLResponse(_PAGE, headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        })

    @router.get("/admin/client.js", include_in_schema=False)
    def browser_js():
        return Response(_JS, media_type="text/javascript", headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.get("/v1/admin/auth/config")
    def public_config():
        return JSONResponse({"domain": config.domain, "client_id": config.client_id,
                             "redirect_uri": config.redirect_uri}, headers={"Cache-Control": "no-store"})

    @router.post("/v1/admin/auth/exchange")
    def exchange_code(payload: CodeExchange):
        try:
            token = exchange.exchange(payload.code, payload.code_verifier)
            if token is None:
                raise HTTPException(status_code=401, detail="Administrator sign-in failed")
            principal = verify_owner(token)
        except HTTPException:
            raise
        except ExchangeUnavailable as exc:
            raise HTTPException(status_code=503, detail="Administrator sign-in unavailable") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Administrator identity check unavailable") from exc
        if not isinstance(principal, VerifiedAdmin) or not principal.mfa_verified or "owner" not in principal.roles:
            raise HTTPException(status_code=403, detail="Administrative authorization denied")
        return JSONResponse({"access_token": token, "token_type": "Bearer"}, headers={
            "Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff"})

    return router


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>GuardOath Admin</title>
<style>:root{font-family:system-ui,sans-serif;color:#14273a;background:#f5f7fb}body{margin:0}header{background:#12273c;color:white;padding:20px max(20px,5vw)}main{max-width:1100px;margin:24px auto;padding:0 20px}.panel{background:white;border:1px solid #dbe3ee;border-radius:12px;padding:20px;margin-bottom:16px}button{background:#154e82;color:white;border:0;border-radius:6px;padding:10px 16px;cursor:pointer}button:disabled{opacity:.5;cursor:default}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.metric{border:1px solid #ddd;border-radius:8px;padding:12px}.muted{color:#526678}table{width:100%;border-collapse:collapse}td,th{border-bottom:1px solid #dfe5ed;padding:9px;text-align:left}.scroll{overflow-x:auto}input{padding:9px;max-width:100%}nav{display:flex;flex-wrap:wrap;gap:12px}nav button{background:#30546f}#app[hidden]{display:none}</style></head><body>
<header><h1>GuardOath Administration</h1><p>Owner console · Read-only inspection with optional audited access controls</p></header><main>
<section id="login" class="panel"><h2>Administrator sign-in</h2><p>Use your GuardOath administrator account with MFA. Customers and demo API keys do not have administrative access.</p><button id="login-start" type="button">Sign in with Cognito</button><p id="login-message" role="status"></p></section>
<div id="app" hidden><section class="panel"><nav><button type="button" id="refresh">Refresh</button><button type="button" id="logout">Sign out</button></nav><p id="status" role="status"></p></section>
<section class="panel"><h2>Overview</h2><div class="grid" id="metrics"></div></section>
<section class="panel"><h2>Cognito users</h2><p class="muted">Owner-only, one provider page at a time; masked emails and unknown totals.</p><label for="user-search">Email prefix</label> <input id="user-search" maxlength="80" autocomplete="off"> <button id="user-search-go" type="button">Search users</button><div class="scroll"><table><thead><tr><th>Username</th><th>Masked email</th><th>Verified</th><th>Enabled</th><th>Status</th><th>Created (UTC)</th></tr></thead><tbody id="user-rows"></tbody></table></div><p id="user-status" role="status"></p><button id="user-next" type="button" disabled>Next users</button>
<h3>Inspect an exact Cognito account</h3><p class="muted">Use a known full email address. Only masked account state returns; this action does not change sign-in or billing.</p>
<label for="user-exact-email">Exact account email</label> <input id="user-exact-email" type="email" maxlength="254" autocomplete="off" spellcheck="false">
<button id="user-inspect" type="button">Inspect account</button><p id="user-inspect-result" role="status"></p></section>
<section class="panel"><h2>Tenants</h2><p class="muted">API key counts are sourced from the persisted tenant record; actual key material is never displayed. Unknown counts remain unavailable.</p><label for="search">Tenant ID filter</label> <input id="search" maxlength="40" autocomplete="off"> <button id="search-go">Search</button><div class="scroll"><table><thead><tr><th>Tenant</th><th>Name</th><th>Status</th><th>Subscription</th><th>Allowance</th><th>Used this month</th><th>API keys</th><th>Manage</th></tr></thead><tbody id="tenant-rows"></tbody></table></div><p id="tenant-status"></p><button id="next" type="button" disabled>Next page</button></section>
<section class="panel"><h2>Subscription reconciliation review</h2><p class="muted">Select Verify Stripe beside a tenant to compare the current provider status with its saved tenant entitlement. Discrepancies are advisory: no paid access or billing state is modified here.</p><p id="billing-details" role="status">No subscription selected.</p></section>
<section class="panel"><h2>Usage and costs</h2><p id="usage-details" role="status">Choose Usage beside a tenant to view authoritative request counts. AWS cost and Stripe invoice amounts are unavailable without verified provider data.</p></section>
<section class="panel"><h2>Audit receipt lookup</h2><p class="muted">Read one stored administrative event by its exact event ID. No exports or audit enumeration.</p><label for="audit-id">Audit event ID</label> <input id="audit-id" maxlength="64" autocomplete="off" spellcheck="false"> <button type="button" id="audit-go">Verify receipt</button><p id="audit-result" role="status"></p></section>
<section class="panel"><h2>Categories and checks</h2><p id="catalog-summary"></p><div id="categories"></div>
<h3>Preview a versioned catalog description proposal</h3><p class="muted">Select Preview description beside a published rule. This checks the change against the live registry version; it does not save, publish, enable or change any rule. Review fingerprints are not signatures or approvals.</p>
<p id="proposal-target">No rule selected.</p>
<label for="proposal-title">Proposed rule title</label><p><input id="proposal-title" maxlength="120" disabled></p>
<label for="proposal-use-case">Proposed use case</label><p><textarea id="proposal-use-case" rows="3" maxlength="500" disabled></textarea></p>
<button id="proposal-go" type="button" disabled>Preview description change</button>
<button id="proposal-save" type="button" disabled>Save immutable draft</button>
<p id="proposal-result" role="status"></p>
<h3>Inspect a saved catalog draft</h3><label for="draft-id">Draft ID</label>
<input id="draft-id" maxlength="64" autocomplete="off" spellcheck="false">
<button id="draft-inspect" type="button">Inspect draft</button>
<p id="draft-result" role="status"></p></section></div>
</main><script src="/admin/client.js" defer></script></body></html>"""

# Zero third-party scripts. OAuth access token is deliberately memory-only.
_JS = r'''"use strict";
(() => {
  let token = null;
  let config = null;
  let nextCursor = null;
  let nextUserCursor = null;
  let previewSelection = null;
  let previewReady = null;
  let authEpoch = 0;
  let features = {tenant_status_changes:false};
  const el = id => document.getElementById(id);
  const say = (id, value) => { el(id).textContent = value; };
  async function request(path, options = {}) {
    const headers = {"Accept":"application/json", ...(options.body ? {"Content-Type":"application/json"} : {})};
    if (token) headers["X-GuardOath-Admin-Session"] = token;
    const result = await fetch(path, { ...options, headers, cache:"no-store", credentials:"omit" });
    if (!result.ok) {
      if (token && (result.status === 401 || result.status === 403)) signOut("Administrator session expired or access revoked");
      if (result.status === 429) throw new Error("Cognito directory request limit reached (429); retry later");
      throw new Error("Request failed (" + result.status + ")");
    }
    return result.json();
  }
  const textNode = (tag, value) => { const item = document.createElement(tag); item.textContent = String(value ?? "Unavailable"); return item; };
  const setLoggedIn = logged => { el("login").hidden = logged; el("app").hidden = !logged; };
  const clearAdminData = () => {
    el("metrics").replaceChildren();
    el("tenant-rows").replaceChildren();
    el("user-rows").replaceChildren();
    el("categories").replaceChildren();
    for (const id of ["status", "tenant-status", "user-status", "user-inspect-result", "catalog-summary", "usage-details", "billing-details", "audit-result", "proposal-result", "draft-result"]) say(id, "");
    previewSelection = null;
    previewReady = null;
    el("proposal-save").disabled = true;
    el("draft-id").value = "";
    say("proposal-target", "No rule selected.");
    el("proposal-title").value = "";
    el("proposal-use-case").value = "";
    el("proposal-title").disabled = true;
    el("proposal-use-case").disabled = true;
    el("proposal-go").disabled = true;
    el("audit-id").value = "";
    el("search").value = "";
    el("user-search").value = "";
    el("user-exact-email").value = "";
    nextUserCursor = null;
    el("user-next").disabled = true;
    nextCursor = null;
    features = {tenant_status_changes:false};
    el("next").disabled = true;
  };
  const signOut = (message = "Signed out of this tab") => {
    authEpoch += 1;
    token = null;
    clearAdminData();
    setLoggedIn(false);
    say("login-message", message);
  };
  async function load() {
    if (!token) return;
    const epoch = authEpoch;
    const [overview, tenants, catalog, available, users] = await Promise.all([
      request("/v1/admin/overview"), request("/v1/admin/tenants?limit=20"),
      request("/v1/admin/catalog"), request("/v1/admin/features"),
      request("/v1/admin/users?limit=20").catch(() => ({status:"unavailable",users:[]}))]);
    if (!token || epoch !== authEpoch) return;
    el("metrics").replaceChildren();
    for (const [name, metric] of Object.entries(overview.metrics || {})) {
      const card = document.createElement("div"); card.className = "metric";
      card.append(textNode("div", name.replaceAll("_", " ")), textNode("strong", metric?.value ?? "Unavailable"), textNode("small", metric?.source || "Unknown source"));
      el("metrics").append(card);
    }
    features = available;
    previewSelection = null;
    previewReady = null;
    el("proposal-save").disabled = true;
    say("proposal-target", "No rule selected.");
    say("proposal-result", "");
    el("proposal-go").disabled = true;
    el("proposal-title").disabled = true;
    el("proposal-use-case").disabled = true;
    showUsers(users);
    showTenants(tenants);
    const categories = Array.isArray(catalog.categories) ? catalog.categories : [];
    say("catalog-summary", "Published registry: " + (catalog.registry_version ?? "unknown") +
      " · " + categories.length + " categories · " + (catalog.total_checks ?? "unknown") +
      " checks · " + (catalog.total_rules ?? "unknown") + " rules" + readAuditLabel(catalog));
    el("categories").replaceChildren();
    for (const category of categories) {
      const container = document.createElement("details");
      const heading = document.createElement("summary");
      const checks = Array.isArray(category.checks) ? category.checks : [];
      heading.append(textNode("strong", category.name), textNode("span", " — " + checks.length + " checks"));
      container.append(heading);
      for (const check of checks) {
        const item = document.createElement("details");
        const title = document.createElement("summary");
        title.append(textNode("strong", check.name || check.check_id),
          textNode("span", " — " + (check.rule_count ?? "?") + " rules"));
        item.append(title);
        const entries = document.createElement("ul");
        for (const rule of check.rules || []) {
          const entry = document.createElement("li");
          entry.append(textNode("strong", rule.rule_id),
            textNode("span", " — " + (rule.title || "") + ": " + (rule.use_case || "")));
          if (features.catalog_preview) {
            const preview = document.createElement("button");
            preview.type = "button";
            preview.textContent = "Preview description";
            preview.addEventListener("click", () => {
              previewReady = null;
              el("proposal-save").disabled = true;
              previewSelection = {category_id:category.category_id, check_id:check.check_id,
                rule_id:rule.rule_id, registry_version:catalog.registry_version};
              say("proposal-target", "Selected " + rule.rule_id + " · published registry " + catalog.registry_version);
              el("proposal-title").value = rule.title || "";
              el("proposal-use-case").value = rule.use_case || "";
              el("proposal-title").disabled = false;
              el("proposal-use-case").disabled = false;
              el("proposal-go").disabled = false;
              say("proposal-result", "Edit the title or use case, then preview. No changes will be saved.");
            });
            entry.append(preview);
          }
          if (features.tenant_rule_changes) {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = "Set tenant rule";
            button.addEventListener("click", () => setTenantRule(rule.rule_id));
            entry.append(button);
          }
          entries.append(entry);
        }
        item.append(entries); container.append(item);
      }
      el("categories").append(container);
    }
    say("status", "Read-only administration loaded · Overview" + readAuditLabel(overview) +
      " · Capabilities" + readAuditLabel(available) +
      (features.sensitive_read_audit ? "" : " · Persistent read auditing not configured"));
  }
  async function previewCatalogRule() {
    if (!token || !features.catalog_preview || !previewSelection) return;
    const selection = {...previewSelection};
    const epoch = authEpoch;
    const version = Number(selection.registry_version);
    if (!Number.isSafeInteger(version) || version < 1) {
      say("proposal-result", "Published catalog version unavailable"); return;
    }
    previewReady = null;
    el("proposal-save").disabled = true;
    const proposed = {expected_registry_version:selection.registry_version,
      proposed_registry_version:String(version + 1), category_id:selection.category_id,
      check_id:selection.check_id, rule_id:selection.rule_id,
      title:el("proposal-title").value, use_case:el("proposal-use-case").value};
    try {
      const result = await request("/v1/admin/catalog/preview", {method:"POST",
        body:JSON.stringify(proposed)});
      if (!token || epoch !== authEpoch || previewSelection?.rule_id !== selection.rule_id) return;
      if (result.status !== "preview_only" || result.published !== false ||
          result.persisted !== false || result.registry_version !== selection.registry_version ||
          result.rule_id !== selection.rule_id || result.tenant_policies_changed !== false ||
          result.detector_behavior_changed !== false || !Array.isArray(result.changed_fields) ||
          result.fingerprint_kind !== "review_only_untrusted_sha256_not_signature" ||
          !/^[a-f0-9]{64}$/.test(result.baseline_sha256) ||
          !/^[a-f0-9]{64}$/.test(result.proposal_sha256)) {
        throw new Error("Catalog preview verification unavailable");
      }
      previewReady = proposed;
      el("proposal-save").disabled = !features.catalog_drafts;
      say("proposal-result", "Preview only — NOT saved or published. Candidate registry " +
        result.candidate_registry_version + " · proposed fields: " +
        result.changed_fields.join(", ") + ". Tenant settings and detector behavior unchanged. " +
        "Published baseline SHA-256: " + result.baseline_sha256 +
        " · Proposal SHA-256: " + result.proposal_sha256 +
        " (review identifiers only; NOT signatures or approval). " +
        (result.requires || "Review and tests required") + readAuditLabel(result));
    } catch(err) {
      if (token && epoch === authEpoch) say("proposal-result", err.message);
    }
  }

  async function saveCatalogDraft() {
    if (!token || !features.catalog_drafts || !previewReady) return;
    const proposed = {...previewReady};
    if (el("proposal-title").value !== proposed.title ||
        el("proposal-use-case").value !== proposed.use_case) {
      previewReady = null;
      el("proposal-save").disabled = true;
      say("proposal-result", "Description changed; preview again before saving");
      return;
    }
    const reason = prompt("Draft reason (10–240 characters; no sensitive information):");
    if (!reason || reason.length < 10 || reason.length > 240) return;
    if (!confirm("Save an immutable review draft? This does NOT publish a rule or change customer settings.")) return;
    const epoch = authEpoch;
    try {
      const result = await request("/v1/admin/catalog/drafts", {method:"POST",
        body:JSON.stringify({...proposed,reason,
          idempotency_key:crypto.randomUUID().replaceAll("-", "")})});
      if (!token || epoch !== authEpoch) return;
      if (result.status !== "draft_saved" || result.persisted !== true ||
          result.published !== false || result.tenant_policies_changed !== false ||
          !/^[a-f0-9]{64}$/.test(result.draft_id)) throw new Error("Draft receipt unavailable");
      el("draft-id").value = result.draft_id;
      previewReady = null;
      el("proposal-save").disabled = true;
      say("proposal-result", "Signed draft saved · " + result.draft_id +
        " · NOT reviewed or published; source-controlled release approval and tests still needed" +
        readAuditLabel(result));
    } catch (err) {if (token && epoch === authEpoch) say("proposal-result", err.message);}
  }
  async function inspectCatalogDraft() {
    if (!token || !features.catalog_drafts) return;
    const id = el("draft-id").value.trim();
    if (!/^[a-f0-9]{64}$/.test(id)) {say("draft-result", "Enter a valid draft ID");return;}
    const epoch = authEpoch;
    try {
      const result = await request("/v1/admin/catalog/drafts/" + id);
      if (!token || epoch !== authEpoch) return;
      if (!result.draft || result.draft.draft_id !== id ||
          result.draft.published !== false || result.ready_to_publish !== false ||
          typeof result.stale !== "boolean") throw new Error("Draft integrity unavailable");
      say("draft-result", "Signed persistent draft · " + id +
        " · target " + result.draft.rule_id +
        " · baseline SHA-256 " + result.draft.baseline_sha256 +
        " · " + (result.stale ? "STALE — source changed; preview again" :
          "Current baseline — still requires review, tests and versioned source release") +
        " · NOT published" + readAuditLabel(result));
    } catch (err) {if (token && epoch === authEpoch) say("draft-result", err.message);}
  }
  function showUsers(data) {
    el("user-rows").replaceChildren();
    for (const user of data.users || []) {
      const row = document.createElement("tr");
      for (const field of ["username", "email_hint", "email_verified", "enabled", "status", "created_utc"]) {
        const value = typeof user[field] === "boolean" ? (user[field] ? "Yes" : "No") : user[field];
        row.append(textNode("td", value));
      }
      el("user-rows").append(row);
    }
    nextUserCursor = data.next_cursor || null;
    el("user-next").disabled = !nextUserCursor;
    say("user-status", data.status === "ok" ?
      (data.returned ?? 0) + " users on this page · " + (data.source || "Cognito") +
      " · UTC retrieved: " + (data.retrieved_utc || "unknown") + " · total unavailable" + readAuditLabel(data) :
      "Cognito user directory unavailable");
  }
  async function pageUsers() {
    if (!token) return;
    const epoch = authEpoch;
    const qs = new URLSearchParams({limit:"20",query:el("user-search").value});
    if (nextUserCursor) qs.set("cursor", nextUserCursor);
    try {
      const result = await request("/v1/admin/users?" + qs);
      if (token && epoch === authEpoch) showUsers(result);
    } catch (err) {
      if (token && epoch === authEpoch) {
        el("user-rows").replaceChildren();
        nextUserCursor = null;
        el("user-next").disabled = true;
        say("user-status", err.message);
      }
    }
  }
  function readAuditLabel(result) {
    const id = result && result.read_audit_event_id;
    return typeof id === "string" && /^[a-f0-9]{64}$/.test(id) ?
      " · Protected read receipt " + id : "";
  }
  async function inspectUser() {
    if (!token) return;
    const email = el("user-exact-email").value.trim();
    // The address is transient; no raw email is displayed or saved after lookup.
    el("user-exact-email").value = "";
    if (!/^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$/.test(email) ||
        email.length > 254 || email.startsWith(".") || email.includes("..") || email.endsWith(".")) {
      say("user-inspect-result", "Enter a valid exact account email");
      return;
    }
    const epoch = authEpoch;
    say("user-inspect-result", "Inspecting Cognito account...");
    try {
      const result = await request("/v1/admin/users/lookup", {
        method: "POST", body: JSON.stringify({email})
      });
      if (!token || epoch !== authEpoch) return;
      if (result.status === "not_found") {
        say("user-inspect-result", "Cognito has no exact email match; no account changes made" + readAuditLabel(result));
      } else if (result.status === "ok" && result.user &&
                 ["CONFIRMED","UNCONFIRMED","ARCHIVED","COMPROMISED","UNKNOWN","RESET_REQUIRED",
                  "FORCE_CHANGE_PASSWORD","EXTERNAL_PROVIDER"].includes(result.user.status)) {
        const user = result.user;
        say("user-inspect-result", "Account " + (user.username || "Unavailable") +
            " · masked email " + (user.email_hint || "Unavailable") +
            " · status " + user.status +
            " · enabled " + (user.enabled === true ? "Yes" : user.enabled === false ? "No" : "Unavailable") +
            " · verified email " + (user.email_verified === true ? "Yes" : user.email_verified === false ? "No" : "Unavailable") +
            " · updated UTC " + (user.updated_utc || "Unavailable") +
            " · source: " + (result.source || "Cognito") + readAuditLabel(result));
      } else {
        say("user-inspect-result", "Cognito identity inspection unavailable");
      }
    } catch (err) {
      if (token && epoch === authEpoch) say("user-inspect-result", err.message);
    }
  }
  function showTenants(data) {
    el("tenant-rows").replaceChildren();
    for (const tenant of data.tenants || []) {
      const row = document.createElement("tr");
      for (const field of ["tenant_id", "display_name", "status", "subscription_status", "monthly_allowance", "usage_month_count", "api_key_count"]) row.append(textNode("td", tenant[field]));
      const actions = document.createElement("td");
      const usage = document.createElement("button");
      usage.type = "button";
      usage.textContent = "Usage";
      usage.addEventListener("click", () => inspectUsage(tenant));
      actions.append(usage);
      if (features.billing_verify && tenant.demo === false) {
        const verify = document.createElement("button");
        verify.type = "button";
        verify.textContent = "Verify Stripe";
        verify.addEventListener("click", () => verifyBilling(tenant));
        actions.append(verify);
      }
      if (features.monthly_cost_caps && tenant.demo === false && Number.isInteger(tenant.revision) &&
          Number.isSafeInteger(tenant.monthly_allowance) && tenant.monthly_allowance >= 0) {
        const cap = document.createElement("button");
        cap.type = "button";
        cap.textContent = "Cap usage";
        cap.addEventListener("click", () => capUsage(tenant));
        actions.append(cap);
      }
      if (features.tenant_key_revocation && tenant.demo === false &&
          Number.isSafeInteger(tenant.revision) && Number.isInteger(tenant.api_key_count) &&
          tenant.api_key_count > 0) {
        const revoke = document.createElement("button");
        revoke.type = "button";
        revoke.textContent = "Revoke keys";
        revoke.addEventListener("click", () => revokeTenantKeys(tenant));
        actions.append(revoke);
      }
      if (features.tenant_status_changes && tenant.demo === false && Number.isInteger(tenant.revision)) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = tenant.status === "active" ? "Suspend" : "Reactivate";
        button.addEventListener("click", () => changeStatus(tenant));
        actions.append(button);
      } else {
        actions.append(textNode("span", "Read-only"));
      }
      row.append(actions);
      el("tenant-rows").append(row);
    }
    nextCursor = data.next_cursor || null;
    el("next").disabled = !nextCursor;
    say("tenant-status", (data.tenants || []).length + " tenants on this page; source: " + (data.source || "unknown") + readAuditLabel(data));
  }
  async function inspectUsage(tenant) {
    if (!token) return;
    const epoch = authEpoch;
    try {
      const result = await request("/v1/admin/tenants/" + encodeURIComponent(tenant.tenant_id) + "/usage");
      if (!token || epoch !== authEpoch) return;
      const usage = result.usage || {};
      const count = usage.requests_recorded;
      const remaining = usage.remaining_before_allowance;
      const signal = usage.usage_threshold || {};
      const level = ["unavailable", "within_limit", "watch", "near_limit", "at_limit", "over_limit"].includes(signal.state)
        ? signal.state.replaceAll("_", " ") : "unavailable";
      const percent = Number.isSafeInteger(signal.utilization_percent_floor) && signal.utilization_percent_floor >= 0
        ? signal.utilization_percent_floor + "%" : "Unavailable";
      const policy = ["blocks_at_saved_cap", "records_overage"].includes(signal.policy)
        ? signal.policy.replaceAll("_", " ") : "unavailable";
      say("usage-details", tenant.tenant_id + " · UTC " + (usage.period_utc || "unknown") +
        " · Recorded: " + (count === null || count === undefined ? "Unavailable" : count) +
        " · Allowance: " + (usage.monthly_allowance ?? "Unavailable") +
        " · Remaining before allowance: " + (remaining === null || remaining === undefined ? "Unavailable" : remaining) +
        " · Overage policy: " + (usage.overage_policy || "Unavailable") +
        " · Recorded quota signal: " + level + " (" + percent + ")" +
        " · Saved-cap behavior: " + policy +
        " · Record updated: " + (usage.record_updated_at || "Unavailable") +
        " · AWS costs: Unavailable · Stripe invoices: Unavailable · Source: " + (usage.source || "unknown") + readAuditLabel(result));
    } catch (err) {
      if (token && epoch === authEpoch) say("usage-details", err.message);
    }
  }
  async function verifyBilling(tenant) {
    if (!token || !features.billing_verify) return;
    const epoch = authEpoch;
    try {
      const result = await request("/v1/admin/subscriptions/" + encodeURIComponent(tenant.tenant_id));
      if (token && epoch === authEpoch) {
        const state = result.subscription || {};
        const assessment = state.reconciliation || {};
        const level = ["critical","review","consistent","unavailable"].includes(assessment.level) ?
          assessment.level : "unavailable";
        say("billing-details", tenant.tenant_id + " · Provider: " +
          (state.verified ? state.status : "unverified (" + (state.status || "unavailable") + ")") +
          " · Reconciliation: " + level + " (" + (assessment.code || "unknown") + ")" +
          " · " + (assessment.next_step || "Unable to establish billing agreement") +
          " · Current period ends (UTC): " +
          (state.verified && typeof state.current_period_end_utc === "string" ? state.current_period_end_utc : "Unavailable") +
          " · Cancel at period end: " +
          (state.verified && state.cancel_at_period_end === true ? "Yes" :
           state.verified && state.cancel_at_period_end === false ? "No" : "Unavailable") +
          " · Next invoice amount: Unavailable" +
          " · UTC provider read: " + (state.verified_at || "Unavailable") + readAuditLabel(result));
        say("status", tenant.tenant_id + ": subscription reviewed; no entitlement change");
      }
    } catch(err) { if (token && epoch === authEpoch) {
      say("billing-details", tenant.tenant_id + ": Stripe verification unavailable; no entitlement change");
      say("status", err.message);
    }}
  }
  async function inspectAudit() {
    if (!token || !features.audit_receipt_lookup) {
      say("audit-result", "Persistent audit lookup unavailable");
      return;
    }
    const event = el("audit-id").value.trim();
    if (!/^[a-f0-9]{64}$/.test(event)) {
      say("audit-result", "Enter a valid 64-character audit event ID");
      return;
    }
    const epoch = authEpoch;
    try {
      const response = await request("/v1/admin/audit/" + encodeURIComponent(event));
      if (!token || epoch !== authEpoch) return;
      const receipt = response.audit || {};
      if (receipt.event_id !== event || receipt.status !== "ok")
        throw new Error("Audit receipt verification unavailable");
      const change = receipt.change || {};
      const integrity = receipt.integrity_status === "signed_verified" ? "HMAC integrity verified" :
        receipt.integrity_status === "legacy_unverified" ? "Legacy receipt — integrity unverified" : null;
      if (!integrity) throw new Error("Audit integrity status unavailable");
      if (receipt.operation === "admin.read") {
        if (receipt.integrity_status !== "signed_verified" || change.field !== "protected_read" ||
            !["cognito.exact_user","stripe.subscription","audit.receipt","cognito.user_directory",
              "tenant.directory","tenant.detail","tenant.usage","catalog.registry","catalog.preview",
              "admin.overview","admin.features","catalog.draft"].includes(change.resource) ||
            receipt.prior_revision !== undefined || receipt.new_revision !== undefined)
          throw new Error("Protected read receipt invalid");
        say("audit-result", "Stored receipt · " + integrity + " · protected read " +
          change.resource + " · tenant " + (receipt.tenant_id || "unknown") +
          " · UTC " + (receipt.when_utc || "unknown") + readAuditLabel(response));
        return;
      }
      if (!Number.isSafeInteger(receipt.new_revision) ||
          receipt.new_revision !== receipt.prior_revision + 1)
        throw new Error("Mutation audit receipt invalid");
      say("audit-result", "Stored receipt · " + integrity + " · " + (receipt.operation || "unknown") +
        " · tenant " + (receipt.tenant_id || "unknown") +
        " · " + (change.field || "change") + ": " + (change.before ?? "unknown") +
        " → " + (change.after ?? "unknown") +
        " · revision " + receipt.new_revision + " · UTC " + (receipt.when_utc || "unknown") +
        readAuditLabel(response));
    } catch (err) {
      if (token && epoch === authEpoch) say("audit-result", err.message);
    }
  }
  function auditReceipt(result, tenantId, priorRevision) {
    // A 2xx response without a signed-by-server transaction receipt is not
    // evidence that the tenant revision was actually changed and audited.
    if (!result || result.tenant_id !== tenantId ||
        !Number.isSafeInteger(priorRevision) ||
        result.revision !== priorRevision + 1 ||
        typeof result.audit_event_id !== "string" ||
        !/^[a-f0-9]{64}$/.test(result.audit_event_id)) {
      throw new Error("Administrative change has no verifiable audit receipt; refresh before retrying");
    }
    return "Audit event " + result.audit_event_id + " · saved tenant revision " + result.revision;
  }
  async function capUsage(tenant) {
    if (!token || !features.monthly_cost_caps) return;
    if (!Number.isSafeInteger(tenant.monthly_allowance) || tenant.monthly_allowance < 0) return;
    const input = prompt("New monthly request cap for " + tenant.tenant_id +
      ". This may ONLY reduce the existing allowance, and will BLOCK overages.");
    if (input === null || !/^\d+$/.test(input)) return;
    const limit = Number(input);
    if (!Number.isSafeInteger(limit) || limit < 0 || limit > tenant.monthly_allowance) return;
    const confirmation = prompt("Type tenant ID to confirm: " + tenant.tenant_id);
    if (confirmation !== tenant.tenant_id) return;
    const reason = prompt("Reason for budget cap (10–240 characters; no personal data):");
    if (!reason || reason.length < 10 || reason.length > 240) return;
    if (!confirm("Apply monthly cap " + limit + " and BLOCK overages for " + tenant.tenant_id + "?")) return;
    const epoch=authEpoch;
    try {
      const outcome = await request("/v1/admin/tenants/" + encodeURIComponent(tenant.tenant_id) + "/allowance-cap", {
        method:"POST",body:JSON.stringify({expected_revision:tenant.revision,new_limit:limit,
          confirmation,reason,idempotency_key:crypto.randomUUID().replaceAll("-", "")})
      });
      const receipt = auditReceipt(outcome, tenant.tenant_id, tenant.revision);
      if (token && epoch === authEpoch) {await load();say("status","Cost cap saved · " + receipt);}
    } catch(err) {if(token && epoch===authEpoch) say("status",err.message);}
  }
  async function revokeTenantKeys(tenant) {
    if (!token || !features.tenant_key_revocation || !Number.isSafeInteger(tenant.revision) ||
        !Number.isInteger(tenant.api_key_count) || tenant.api_key_count < 1) return;
    const confirmation = prompt("This immediately revokes ALL application API keys for " +
      tenant.tenant_id + ". Type the tenant ID exactly:");
    if (confirmation !== tenant.tenant_id) return;
    const reason = prompt("Reason for revoking application keys (10–240 characters; no personal data):");
    if (!reason || reason.length < 10 || reason.length > 240) return;
    if (!confirm("Permanently revoke " + tenant.api_key_count +
        " application API keys for " + tenant.tenant_id + "? Customers must issue new keys.")) return;
    const epoch = authEpoch;
    try {
      const outcome = await request("/v1/admin/tenants/" +
        encodeURIComponent(tenant.tenant_id) + "/revoke-keys", {
        method:"POST", body:JSON.stringify({expected_revision:tenant.revision,
          confirmation,reason,idempotency_key:crypto.randomUUID().replaceAll("-", "")})
      });
      const receipt = auditReceipt(outcome, tenant.tenant_id, tenant.revision);
      if (outcome.api_key_count !== 0) throw new Error("Revocation response is unverified; refresh before retry");
      if (token && epoch===authEpoch) {await load();say("status","Application keys revoked · " + receipt);}
    } catch (err) {if (token && epoch===authEpoch) say("status",err.message);}
  }
  async function setTenantRule(ruleId) {
    if (!token || !features.tenant_rule_changes) return;
    const tenantId=prompt("Tenant ID whose saved rule action should change:");
    if (!tenantId || !/^[a-z][a-z0-9-]{1,39}$/.test(tenantId)) return;
    const stage=prompt("Stage: input or output", "input");
    if (!(["input","output"].includes(stage))) return;
    const action=prompt("Action: off, allow, flag, block, or same_as_check", "block");
    if (!(["off","allow","flag","block","same_as_check"].includes(action))) return;
    const confirmation=prompt("Type tenant ID exactly: " + tenantId);
    if (confirmation !== tenantId) return;
    const reason=prompt("Audit reason (10–240 characters; no sensitive data):");
    if (!reason || reason.length < 10 || reason.length > 240) return;
    if (!confirm("Change " + stage + " rule " + ruleId + " to " + action + " on " + tenantId + "?")) return;
    const epoch=authEpoch;
    try {
      const current=await request("/v1/admin/tenants/"+encodeURIComponent(tenantId));
      if (!token || epoch!==authEpoch || !Number.isInteger(current.tenant?.revision)) return;
      const outcome = await request("/v1/admin/tenants/"+encodeURIComponent(tenantId)+"/rule-action", {
        method:"POST",body:JSON.stringify({expected_revision:current.tenant.revision,
          stage,rule_id:ruleId,action,confirmation,reason,
          idempotency_key:crypto.randomUUID().replaceAll("-", "")})
      });
      const receipt = auditReceipt(outcome, tenantId, current.tenant.revision);
      if (token && epoch===authEpoch) {await load();say("status","Tenant rule saved · " + receipt);}
    } catch(err) {if(token && epoch===authEpoch) say("status",err.message);}
  }
  async function changeStatus(tenant) {
    if (!token || !features.tenant_status_changes) return;
    const nextStatus = tenant.status === "active" ? "disabled" : "active";
    const confirmation = prompt("To " + (nextStatus === "disabled" ? "suspend" : "reactivate") +
      " this account, type its tenant ID exactly: " + tenant.tenant_id);
    if (confirmation !== tenant.tenant_id) return;
    const reason = prompt("Enter an audit reason (10–240 characters). Do not include secrets or personal data.");
    if (!reason || reason.length < 10 || reason.length > 240) return;
    if (!confirm("Apply " + nextStatus + " to " + tenant.tenant_id + "? This affects customer access.")) return;
    const epoch = authEpoch;
    try {
      const outcome = await request("/v1/admin/tenants/" + encodeURIComponent(tenant.tenant_id) + "/status", {
        method: "POST", body: JSON.stringify({status:nextStatus, confirmation, reason,
          expected_revision:tenant.revision, idempotency_key: crypto.randomUUID().replaceAll("-", "")})
      });
      const receipt = auditReceipt(outcome, tenant.tenant_id, tenant.revision);
      if (token && epoch === authEpoch) {
        nextCursor = null;
        await load();
        say("status", "Tenant access updated · " + receipt);
      }
    } catch(err) { if (token && epoch === authEpoch) say("status", err.message); }
  }
  async function page() {
    const epoch = authEpoch;
    if (!token) return;
    const qs = new URLSearchParams({limit:"20",search:el("search").value});
    if (nextCursor) qs.set("cursor", nextCursor);
    try {
      const result = await request("/v1/admin/tenants?" + qs);
      if (token && epoch === authEpoch) showTenants(result);
    } catch(err) { if (token && epoch === authEpoch) say("tenant-status", err.message); }
  }
  async function beginLogin() {
    try {
      config = await request("/v1/admin/auth/config");
      if (!crypto?.subtle || !crypto.getRandomValues) throw new Error("Secure browser context required");
      const bytes = new Uint8Array(32); crypto.getRandomValues(bytes);
      const to64 = array => btoa(String.fromCharCode(...array)).replaceAll("+","-").replaceAll("/","_").replaceAll("=", "");
      const verifier = to64(bytes);
      crypto.getRandomValues(bytes);
      const state = to64(bytes);
      sessionStorage.setItem("go_admin_pkce", JSON.stringify({verifier, state}));
      const challenge = to64(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
      const uri = new URL("https://" + config.domain + "/oauth2/authorize");
      uri.search = new URLSearchParams({response_type:"code",client_id:config.client_id,redirect_uri:config.redirect_uri,
        scope:"openid email aws.cognito.signin.user.admin",code_challenge_method:"S256",code_challenge:challenge,state});
      location.assign(uri.href);
    } catch(err) { say("login-message", err.message); }
  }
  async function finishLogin() {
    const params = new URLSearchParams(location.search);
    if (!params.has("code") && !params.has("error")) return;
    const saved = sessionStorage.getItem("go_admin_pkce");
    sessionStorage.removeItem("go_admin_pkce");
    history.replaceState(null, "", "/admin/login");
    if (!saved || params.has("error") || !params.get("code") || !params.get("state")) throw new Error("Administrator sign-in rejected");
    let pending; try { pending = JSON.parse(saved); } catch { throw new Error("Administrator sign-in rejected"); }
    if (pending.state !== params.get("state") || !pending.verifier) throw new Error("Administrator sign-in rejected");
    const result = await request("/v1/admin/auth/exchange",{method:"POST",body:JSON.stringify({code:params.get("code"),code_verifier:pending.verifier})});
    if (typeof result.access_token !== "string" || !result.access_token) throw new Error("Administrator sign-in rejected");
    authEpoch += 1;
    token = result.access_token;
    clearAdminData();
    setLoggedIn(true);
    try { await load(); } catch(err) { say("status", err.message); }
  }
  el("login-start").addEventListener("click", beginLogin);
  el("proposal-save").addEventListener("click", saveCatalogDraft);
  el("draft-inspect").addEventListener("click", inspectCatalogDraft);
  el("refresh").addEventListener("click", () => load().catch(e => say("status", e.message)));
  el("logout").addEventListener("click", () => signOut());
  el("search-go").addEventListener("click", () => {nextCursor=null;page();});
  el("user-search-go").addEventListener("click", () => {nextUserCursor=null;pageUsers();});
  el("user-next").addEventListener("click", pageUsers);
  el("user-inspect").addEventListener("click", inspectUser);
  el("next").addEventListener("click", page);
  el("audit-go").addEventListener("click", inspectAudit);
  el("proposal-go").addEventListener("click", previewCatalogRule);
  finishLogin().catch(err => say("login-message", err.message));
})();'''
