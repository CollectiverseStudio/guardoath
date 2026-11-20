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
<header><h1>GuardOath Administration</h1><p>Owner console · Read-only</p></header><main>
<section id="login" class="panel"><h2>Administrator sign-in</h2><p>Use your GuardOath administrator account with MFA. Customers and demo API keys do not have administrative access.</p><button id="login-start" type="button">Sign in with Cognito</button><p id="login-message" role="status"></p></section>
<div id="app" hidden><section class="panel"><nav><button type="button" id="refresh">Refresh</button><button type="button" id="logout">Sign out</button></nav><p id="status" role="status"></p></section>
<section class="panel"><h2>Overview</h2><div class="grid" id="metrics"></div></section>
<section class="panel"><h2>Cognito users</h2><p class="muted">Owner-only, one provider page at a time; masked emails and unknown totals.</p><label for="user-search">Email prefix</label> <input id="user-search" maxlength="80" autocomplete="off"> <button id="user-search-go" type="button">Search users</button><div class="scroll"><table><thead><tr><th>Username</th><th>Masked email</th><th>Verified</th><th>Enabled</th><th>Status</th><th>Created (UTC)</th></tr></thead><tbody id="user-rows"></tbody></table></div><p id="user-status" role="status"></p><button id="user-next" type="button" disabled>Next users</button></section>
<section class="panel"><h2>Tenants</h2><p class="muted">API key counts are sourced from the persisted tenant record; actual key material is never displayed. Unknown counts remain unavailable.</p><label for="search">Tenant ID filter</label> <input id="search" maxlength="40" autocomplete="off"> <button id="search-go">Search</button><div class="scroll"><table><thead><tr><th>Tenant</th><th>Name</th><th>Status</th><th>Subscription</th><th>Allowance</th><th>Used this month</th><th>API keys</th><th>Manage</th></tr></thead><tbody id="tenant-rows"></tbody></table></div><p id="tenant-status"></p><button id="next" type="button" disabled>Next page</button></section>
<section class="panel"><h2>Usage and costs</h2><p id="usage-details" role="status">Choose Usage beside a tenant to view authoritative request counts. AWS cost and Stripe invoice amounts are unavailable without verified provider data.</p></section>
<section class="panel"><h2>Categories and checks</h2><p id="catalog-summary"></p><div id="categories"></div></section></div>
</main><script src="/admin/client.js" defer></script></body></html>"""

# Zero third-party scripts. OAuth access token is deliberately memory-only.
_JS = r'''"use strict";
(() => {
  let token = null;
  let config = null;
  let nextCursor = null;
  let nextUserCursor = null;
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
    for (const id of ["status", "tenant-status", "user-status", "catalog-summary", "usage-details"]) say(id, "");
    el("search").value = "";
    el("user-search").value = "";
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
    showUsers(users);
    showTenants(tenants);
    const categories = Array.isArray(catalog.categories) ? catalog.categories : [];
    say("catalog-summary", "Published registry: " + (catalog.registry_version ?? "unknown") +
      " · " + categories.length + " categories · " + (catalog.total_checks ?? "unknown") +
      " checks · " + (catalog.total_rules ?? "unknown") + " rules");
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
    say("status", "Read-only administration loaded");
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
      " · UTC retrieved: " + (data.retrieved_utc || "unknown") + " · total unavailable" :
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
    } catch (err) { if (token && epoch === authEpoch) say("user-status", err.message); }
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
      if (features.monthly_cost_caps && tenant.demo === false && Number.isInteger(tenant.revision)) {
        const cap = document.createElement("button");
        cap.type = "button";
        cap.textContent = "Cap usage";
        cap.addEventListener("click", () => capUsage(tenant));
        actions.append(cap);
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
    say("tenant-status", (data.tenants || []).length + " tenants on this page; source: " + (data.source || "unknown"));
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
      say("usage-details", tenant.tenant_id + " · UTC " + (usage.period_utc || "unknown") +
        " · Recorded: " + (count === null || count === undefined ? "Unavailable" : count) +
        " · Allowance: " + (usage.monthly_allowance ?? "Unavailable") +
        " · Remaining before allowance: " + (remaining === null || remaining === undefined ? "Unavailable" : remaining) +
        " · Overage policy: " + (usage.overage_policy || "Unavailable") +
        " · AWS costs: Unavailable · Stripe invoices: Unavailable · Source: " + (usage.source || "unknown"));
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
        say("status", tenant.tenant_id + ": Stripe " + (state.status || "unavailable") +
            " · " + (state.verified ? "verified provider response" : "no live subscription binding"));
      }
    } catch(err) { if (token && epoch === authEpoch) say("status", err.message); }
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
  el("refresh").addEventListener("click", () => load().catch(e => say("status", e.message)));
  el("logout").addEventListener("click", () => signOut());
  el("search-go").addEventListener("click", () => {nextCursor=null;page();});
  el("user-search-go").addEventListener("click", () => {nextUserCursor=null;pageUsers();});
  el("user-next").addEventListener("click", pageUsers);
  el("next").addEventListener("click", page);
  finishLogin().catch(err => say("login-message", err.message));
})();'''
