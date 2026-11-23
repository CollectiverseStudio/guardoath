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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from guardoath.admin_console import VerifiedAdmin
from guardoath.admin_native_mfa import CognitoNativeMfa, NativeMfaUnavailable


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


class NativePasswordStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=1, max_length=254)
    password: SecretStr


class NativePasswordFinish(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket: str = Field(min_length=40, max_length=9000)
    otp: str = Field(min_length=1, max_length=10)


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
                          verify_owner: Callable[[str], VerifiedAdmin | None],
                          native_auth: CognitoNativeMfa | None = None,
                          native_enrollment=None) -> APIRouter:
    """Add the public shell/config and a fail-closed owner-only code exchange."""
    if exchange.config != config or not callable(verify_owner):
        raise ValueError("Browser auth services must match")
    if native_enrollment is not None and native_auth is None:
        raise ValueError("Owner enrollment requires the existing native MFA login")
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
                             "redirect_uri": config.redirect_uri,
                             "native_mfa_enabled": native_auth is not None,
                             "native_enrollment_enabled": native_enrollment is not None},
                            headers={"Cache-Control": "no-store"})

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

    if native_auth is not None:
        def require_origin(request: Request):
            # Password and OTP are only accepted from the production origin;
            # never trust a supplied Host or arbitrary localhost Origin.
            if request.headers.get("origin") != "https://guardoath.com":
                raise HTTPException(status_code=403, detail="Administrator sign-in denied")

        @router.post("/v1/admin/auth/native/start")
        def native_password(payload: NativePasswordStart, request: Request):
            require_origin(request)
            try:
                ticket = native_auth.begin(payload.email, payload.password.get_secret_value())
            except NativeMfaUnavailable as exc:
                raise HTTPException(status_code=503, detail="Administrator sign-in unavailable") from exc
            if ticket is None:
                raise HTTPException(status_code=401, detail="Administrator sign-in failed")
            return JSONResponse({"challenge": "software_token_mfa",
                                 "challenge_ticket": ticket},
                                headers={"Cache-Control": "no-store",
                                         "Pragma": "no-cache",
                                         "Referrer-Policy": "no-referrer"})

        @router.post("/v1/admin/auth/native/finish")
        def native_otp(payload: NativePasswordFinish, request: Request):
            require_origin(request)
            try:
                token = native_auth.finish(payload.ticket, payload.otp)
                if token is None:
                    raise HTTPException(status_code=401, detail="Administrator sign-in failed")
                # This independent verifier MUST prove a signed MFA AMR +
                # recent auth_time, plus live Cognito owner membership.
                principal = verify_owner(token)
            except HTTPException:
                raise
            except NativeMfaUnavailable as exc:
                raise HTTPException(status_code=503, detail="Administrator sign-in unavailable") from exc
            except Exception as exc:
                raise HTTPException(status_code=503, detail="Administrator identity check unavailable") from exc
            if (not isinstance(principal, VerifiedAdmin) or not principal.mfa_verified
                    or "owner" not in principal.roles):
                raise HTTPException(status_code=403, detail="Administrative authorization denied")
            return JSONResponse({"access_token": token, "token_type": "Bearer"},
                                headers={"Cache-Control": "no-store", "Pragma": "no-cache",
                                         "Referrer-Policy": "no-referrer",
                                         "X-Content-Type-Options": "nosniff"})

    if native_enrollment is not None:
        from guardoath.admin_enrollment_browser import create_enrollment_router
        router.include_router(create_enrollment_router(native_enrollment))
    return router


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>GuardOath Admin</title>
<style>
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#183247;background:#f2f5f8;font-synthesis:none;--navy:#102b3f;--teal:#087f81;--line:#dfe7ed;--muted:#607584}
*{box-sizing:border-box}body{margin:0}button,input,select,textarea{font:inherit}button,a,input,select,textarea,summary{-webkit-tap-highlight-color:transparent}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid #3bbdc0;outline-offset:3px}[hidden]{display:none!important}
.topbar{min-height:88px;background:var(--navy);color:#fff;padding:18px 34px;display:flex;align-items:center;justify-content:space-between;gap:24px;border-bottom:1px solid #254356}.brand{display:flex;align-items:center;gap:14px}.brand-mark{width:42px;height:46px;display:grid;place-items:center;background:#117f83;border:1px solid #55b3b4;border-radius:11px 11px 18px 18px;font-size:25px;font-weight:750}.topbar h1{font-size:19px;line-height:1.3;letter-spacing:-.4px;margin:3px 0 0}.eyebrow{font-size:10px;letter-spacing:1.7px;font-weight:750;text-transform:uppercase;margin:0 0 7px;color:var(--teal)}.topbar .eyebrow{color:#89ced1;margin:0}.header-note{margin:0;font-size:12px;line-height:1.7;text-align:right;color:#dae8ef}.header-note span{display:block;color:#9db4c2;font-size:11px}main{max-width:1600px;margin:0 auto}.admin-layout{display:grid;grid-template-columns:218px minmax(0,1fr);min-height:calc(100vh - 88px)}.sidebar{background:var(--navy);padding:29px 16px;display:flex;flex-direction:column;gap:17px}.sidebar-heading{margin:0 13px 0;color:#8eaab9;font-size:10px;letter-spacing:1.4px;font-weight:750}#admin-navigation{display:grid;gap:6px}.section-link{background:transparent;border:1px solid transparent;border-radius:8px;color:#c7d7e1;width:100%;min-height:44px;text-align:left;padding:11px 13px;display:flex;gap:13px;align-items:center;font-size:13px;font-weight:600}.section-link:hover{background:#1c3d51;color:#fff}.section-link[aria-current="page"]{background:#173f50;border-color:#276071;color:#fff;box-shadow:inset 3px 0 #42c5be}.nav-index{font-size:10px;font-variant-numeric:tabular-nums;letter-spacing:1px;color:#6f9aaa}.section-link[aria-current="page"] .nav-index{color:#64d5ca}.sidebar-note{margin:26px 12px 0;padding-top:20px;border-top:1px solid #294454;color:#b7cdd8;font-size:11px;line-height:1.7}.sidebar-note p{color:#8ca8b9;margin:5px 0 0}.status-dot{display:inline-block;width:6px;height:6px;background:#52c2b8;border-radius:50%;margin-right:7px}.workspace{min-width:0;padding:30px 34px 48px}.workspace-header{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:20px}.workspace-header h2{font-size:29px;font-weight:700;letter-spacing:-.8px;margin:0 0 7px}.workspace-header p:not(.eyebrow){font-size:13px;line-height:1.6;color:var(--muted);margin:0;max-width:650px}.workspace-actions{display:flex;gap:8px;flex-shrink:0}.workspace-status{border:1px solid #d9e7e8;border-left:3px solid #369b9b;background:#edf5f5;border-radius:7px;padding:12px 14px;font-size:11px;line-height:1.6;color:#456670;overflow-wrap:anywhere;margin:0 0 20px}.workspace-status:empty{display:none}
.panel{background:#fff;border:1px solid var(--line);border-radius:12px;padding:25px;margin:0 0 18px;min-width:0;box-shadow:0 2px 5px #19364b03}.panel:focus,.section-stack:focus{outline:none}.panel h2{font-size:18px;letter-spacing:-.3px;margin:0 0 11px;line-height:1.4}.panel h3{font-size:14px;margin:25px 0 9px;line-height:1.5}.panel p{font-size:13px;line-height:1.75;overflow-wrap:anywhere}.panel label{font-size:12px;font-weight:650;color:#354f60}.muted{color:var(--muted)}.section-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.section-heading h2{margin:0}.section-tag{font-size:10px;border:1px solid #d7e5e6;border-radius:20px;padding:5px 9px;color:#46747b;background:#f2f8f7;white-space:nowrap}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:13px;margin-top:22px}.metric{border:1px solid var(--line);border-radius:9px;padding:17px;background:#fafcfd;min-width:0}.metric>div{font-size:11px;text-transform:capitalize;letter-spacing:.2px;color:#657b8a;margin-bottom:13px}.metric strong{display:block;font-size:22px;line-height:1.3;letter-spacing:-.6px;font-weight:650;margin-bottom:11px;overflow-wrap:anywhere}.metric small{display:block;font-size:10px;line-height:1.6;color:#788b98}.overview-guides{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:24px}.overview-guides button{color:#244957;background:#fff;text-align:left;padding:16px;border:1px solid var(--line);border-radius:9px;font-size:13px}.overview-guides button:hover{border-color:#60a9a8;background:#f4fbfa}.overview-guides span{display:block;color:var(--muted);font-size:11px;font-weight:400;line-height:1.6;margin-top:7px}
button{background:var(--teal);color:#fff;border:1px solid transparent;border-radius:7px;padding:10px 14px;cursor:pointer;font-size:12px;font-weight:650;line-height:1.4;transition:background .15s,border-color .15s}button:hover{background:#066e71}button:disabled{opacity:.45;cursor:default}.secondary{background:#fff;color:#365264;border-color:#d0dce4}.secondary:hover{background:#e7eef2}input,select,textarea{border:1px solid #cfdae3;background:#fff;color:#173347;border-radius:6px;padding:9px 10px;font-size:13px;max-width:100%;min-height:38px}input:disabled,textarea:disabled{background:#f3f6f8;color:#82939f}textarea{width:100%;resize:vertical}input{margin:5px 5px 5px 0}#proposal-title{width:100%}#audit-id,#draft-id{width:min(100%,520px);font-family:ui-monospace,monospace;font-size:12px}.scroll{overflow:auto;max-width:100%;border:1px solid var(--line);border-radius:8px;margin-top:19px}table{width:100%;min-width:650px;border-collapse:collapse;font-size:12px}th{font-size:10px;color:#637a8a;background:#f4f7f9;letter-spacing:.4px;white-space:nowrap}td,th{border-bottom:1px solid #e7edf1;padding:13px 12px;text-align:left;vertical-align:top}tbody tr:last-child td{border-bottom:0}tbody tr:hover{background:#f8fbfc}td button{margin:0 5px 6px 0;padding:6px 9px;font-size:10px;white-space:nowrap}td:last-child>span{display:block;color:#788d9b;font-size:10px}#tenant-rows td:last-child{min-width:170px}#categories details{border:1px solid var(--line);border-radius:8px;margin:9px 0;padding:0 14px}#categories details details{background:#f8fafb;margin-bottom:13px}summary{padding:14px 0;cursor:pointer;font-size:12px;line-height:1.5}summary span{color:var(--muted);font-size:11px}#categories ul{padding-left:19px;font-size:12px;line-height:1.8}#categories li{margin:10px 0;overflow-wrap:anywhere}#categories li button{font-size:10px;padding:6px 9px;margin:6px}#proposal-target{background:#f3f7f9;border-radius:6px;padding:10px 12px;color:#557382;font-size:12px}.login-panel{max-width:560px;margin:64px auto;padding:36px}.login-panel h2{font-size:25px;letter-spacing:-.6px}.login-panel>p{color:var(--muted)}.login-panel>button{margin:12px 0 6px;min-height:43px}.login-panel .eyebrow{color:var(--teal)}#native-auth{border-top:1px solid var(--line);margin-top:24px;padding-top:15px}#native-auth label{display:block;margin-top:13px}#native-auth input{display:block;width:100%;margin:7px 0 12px}#native-challenge{margin-top:18px}#login-message{color:#456670;overflow-wrap:anywhere}#login-message:empty{display:none}
@media(min-width:1500px){.workspace{padding:34px 45px}}@media(max-width:1080px){.admin-layout{grid-template-columns:180px minmax(0,1fr)}.workspace{padding:26px 22px}.sidebar{padding:28px 10px}.workspace-header{align-items:flex-start}.overview-guides{grid-template-columns:1fr}.overview-guides button{padding:13px}.overview-guides span{margin-top:4px}}@media(max-width:720px){.topbar{padding:16px 18px;min-height:78px;gap:12px}.topbar h1{font-size:16px}.brand{gap:10px}.brand-mark{width:34px;height:39px;font-size:21px}.header-note{display:none}.admin-layout{display:block;min-height:calc(100vh - 78px)}.sidebar{padding:12px 14px;gap:8px}.sidebar-heading,.sidebar-note{display:none}#admin-navigation{grid-template-columns:repeat(4,minmax(0,1fr));gap:5px}.section-link{justify-content:center;font-size:11px;padding:9px 5px;min-height:36px;text-align:center}.nav-index{display:none}.section-link[aria-current="page"]{box-shadow:inset 0 -2px #42c5be}.workspace{padding:21px 14px 30px}.workspace-header{gap:13px;align-items:flex-start;flex-wrap:wrap;margin-bottom:18px}.workspace-header h2{font-size:25px}.workspace-header p:not(.eyebrow){font-size:12px}.workspace-actions{width:100%;justify-content:flex-end}.workspace-actions button{padding:8px 12px}.panel{padding:18px;border-radius:10px}.panel h2{font-size:17px}.section-heading{align-items:flex-start}.section-tag{font-size:9px;padding:4px 7px}.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:16px}.metric{padding:13px}.metric strong{font-size:18px}.login-panel{margin:28px 14px;padding:25px}.panel>input{width:100%}.panel>label{display:block;margin-top:13px}.panel>button{margin-top:5px}.workspace-status{font-size:10px;padding:10px 12px}.overview-guides{margin-top:17px}}@media(prefers-reduced-motion:reduce){button{transition:none}}
</style></head><body>
<header class="topbar"><div class="brand"><span class="brand-mark" aria-hidden="true">G</span><div><p class="eyebrow">Owner workspace</p><h1>GuardOath Administration</h1></div></div><p class="header-note">Owner console<span>Audited access · MFA required</span></p></header><main>
<section id="login" class="panel login-panel"><p class="eyebrow">Secure administration</p><h2>Administrator sign-in</h2><p>Use your GuardOath administrator account with MFA. Customers and demo API keys do not have administrative access.</p><button id="login-start" type="button">Sign in with Cognito</button>
<div id="native-auth" hidden><p class="muted">Administrator password + authenticator app. Requires enrolled TOTP MFA.</p>
<label for="native-email">Administrator email</label> <input id="native-email" type="email" autocomplete="username" maxlength="254">
<label for="native-password">Password</label> <input id="native-password" type="password" autocomplete="current-password">
<button id="native-start" type="button">Continue to authenticator</button>
<div id="native-challenge" hidden><label for="native-code">Authenticator code</label>
<input id="native-code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code">
<button id="native-finish" type="button">Verify administrator</button></div>
<div id="enrollment-entry" hidden><p>First administrator sign-in? An approved owner with no existing authenticator can set one up here.</p>
<button id="enrollment-start" class="secondary" type="button">Set up first authenticator</button></div>
<p id="enrollment-unavailable" class="muted" hidden>First authenticator setup is unavailable. Ask the operator to authorize first-time setup for your account. Existing authenticator sign-in is still available.</p>
<section id="enrollment-setup" hidden aria-labelledby="enrollment-title"><h3 id="enrollment-title">Set up your authenticator</h3>
<p>Add a time-based code in your authenticator app for GuardOath. Copy the setup key into the app, then enter its six-digit code below. Keep the key private.</p>
<label for="enrollment-key">Setup key</label><input id="enrollment-key" type="password" readonly autocomplete="off" spellcheck="false" aria-describedby="enrollment-help">
<button id="enrollment-copy" class="secondary" type="button" disabled>Copy setup key</button>
<p id="enrollment-help" class="muted">Setup expires after three minutes. This is for an approved owner's first authenticator. A lost or previously disabled authenticator needs operator recovery.</p>
<label for="enrollment-code">Code from your new authenticator</label><input id="enrollment-code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6">
<button id="enrollment-finish" type="button" disabled>Save authenticator</button>
<button id="enrollment-cancel" class="secondary" type="button">Cancel setup</button>
<p id="enrollment-message" role="status" aria-live="polite"></p></section></div>
<p id="login-message" role="status"></p></section>
<div id="app" hidden class="admin-layout"><aside class="sidebar"><p class="sidebar-heading">Manage GuardOath</p><nav id="admin-navigation" aria-label="Administration sections">
<button type="button" class="section-link" id="nav-overview" data-section="overview" aria-controls="section-overview" aria-current="page"><span class="nav-index" aria-hidden="true">01</span>Overview</button>
<button type="button" class="section-link" id="nav-users" data-section="users" aria-controls="section-users"><span class="nav-index" aria-hidden="true">02</span>Users</button>
<button type="button" class="section-link" id="nav-tenants" data-section="tenants" aria-controls="section-tenants"><span class="nav-index" aria-hidden="true">03</span>Tenants</button>
<button type="button" class="section-link" id="nav-billing" data-section="billing" aria-controls="section-billing"><span class="nav-index" aria-hidden="true">04</span>Billing</button>
<button type="button" class="section-link" id="nav-usage" data-section="usage" aria-controls="section-usage"><span class="nav-index" aria-hidden="true">05</span>Usage</button>
<button type="button" class="section-link" id="nav-audit" data-section="audit" aria-controls="section-audit"><span class="nav-index" aria-hidden="true">06</span>Audit</button>
<button type="button" class="section-link" id="nav-catalog" data-section="catalog" aria-controls="section-catalog"><span class="nav-index" aria-hidden="true">07</span>Catalog</button>
</nav><div class="sidebar-note"><span class="status-dot" aria-hidden="true"></span>Owner access only<p>Session credentials stay in this tab.</p></div></aside><div class="workspace">
<section class="workspace-header"><div><p class="eyebrow">Control center</p><h2 id="workspace-title">Overview</h2><p id="workspace-description">Inspect your platform, accounts and safeguards from one workspace.</p></div><div class="workspace-actions"><button type="button" id="refresh">Refresh</button><button type="button" id="logout" class="secondary">Sign out</button></div></section><p id="status" class="workspace-status" role="status"></p>
<section id="section-overview" data-admin-section class="panel" tabindex="-1" aria-label="Overview"><div class="section-heading"><h2>Platform overview</h2><span class="section-tag">Source availability</span></div><p class="muted">Current source-backed metrics. A missing source remains unavailable.</p><div class="grid" id="metrics"></div><div class="overview-guides"><button type="button" data-section="users" aria-controls="section-users">Inspect accounts<span>Search users and review sign-in access.</span></button><button type="button" data-section="tenants" aria-controls="section-tenants">Manage tenants<span>Inspect saved entitlements, usage and access.</span></button><button type="button" data-section="catalog" aria-controls="section-catalog">Explore the catalog<span>Review published categories, checks and rules.</span></button></div></section>
<section id="section-users" data-admin-section class="panel" hidden tabindex="-1" aria-label="Users"><h2>Cognito users</h2><p class="muted">Owner-only, one provider page at a time; masked emails and unknown totals.</p><label for="user-search">Email prefix</label> <input id="user-search" maxlength="80" autocomplete="off"> <button id="user-search-go" type="button">Search users</button><div class="scroll"><table><thead><tr><th>Username</th><th>Masked email</th><th>Verified</th><th>Enabled</th><th>Status</th><th>Created (UTC)</th></tr></thead><tbody id="user-rows"></tbody></table></div><p id="user-status" role="status"></p><button id="user-next" type="button" disabled>Next users</button>
<h3>Inspect an exact Cognito account</h3><p class="muted">Use a known full email address. Only masked account state returns; this action does not change sign-in or billing.</p>
<label for="user-exact-email">Exact account email</label> <input id="user-exact-email" type="email" maxlength="254" autocomplete="off" spellcheck="false">
<button id="user-inspect" type="button">Inspect account</button><p id="user-inspect-result" role="status"></p>
<h3>Change Cognito sign-in access</h3><p class="muted">Disable or re-enable a non-owner account only after exact inspection. Does not grant a subscription, change a tenant, or reset MFA. Each action is independently audited and re-read from Cognito. If verification fails, inspect the account rather than retrying blindly.</p>
<label for="user-change-email">Exact account email</label> <input id="user-change-email" type="email" maxlength="254" autocomplete="off" spellcheck="false">
<label for="user-change-action">Action</label> <select id="user-change-action"><option value="disable">Disable sign-in</option><option value="enable">Enable sign-in</option></select>
<button id="user-change" type="button" disabled>Apply audited change</button><p id="user-change-result" role="status"></p>
<h3>Recover an account-action receipt</h3><p class="muted">Retain the action reference when changing access. Signed history never retries a provider write. Optionally enter an exact account email for a fresh independent Cognito state comparison; this cannot prove what caused an indeterminate action.</p>
<label for="user-action-key">Action reference</label> <input id="user-action-key" maxlength="96" autocomplete="off" spellcheck="false">
<label for="user-action-email">Optional exact account email for live comparison</label> <input id="user-action-email" type="email" maxlength="254" autocomplete="off" spellcheck="false">
<button id="user-action-inspect" type="button" disabled>Inspect signed action</button><p id="user-action-result" role="status"></p></section>
<section id="section-tenants" data-admin-section class="panel" hidden tabindex="-1" aria-label="Tenants"><h2>Tenants</h2><p class="muted">API key counts and saved overage policies come from persisted tenant records; Allow and record is not a hard cap. Key material is never displayed. Unknown values remain unavailable.</p><label for="search">Tenant ID filter</label> <input id="search" maxlength="40" autocomplete="off"> <button id="search-go">Search</button><div class="scroll"><table><thead><tr><th>Tenant</th><th>Name</th><th>Status</th><th>Subscription</th><th>Allowance</th><th>Used this month</th><th>Overage policy</th><th>API keys</th><th>Manage</th></tr></thead><tbody id="tenant-rows"></tbody></table></div><p id="tenant-status"></p><button id="next" type="button" disabled>Next page</button></section>
<section id="section-billing" data-admin-section class="section-stack" hidden tabindex="-1" aria-label="Billing"><section class="panel"><h2>Verified recent invoices</h2><p class="muted">Up to five Stripe invoice summaries for an independently verified customer subscription. Amounts are in minor currency units, not a revenue total or forecast.</p><p id="invoice-details" role="status">No invoice history selected.</p></section>
<section class="panel"><h2>Subscription reconciliation review</h2><p class="muted">Select Verify Stripe beside a tenant to compare the current provider status with its saved tenant entitlement. Discrepancies are advisory: no paid access or billing state is modified here.</p><p id="billing-details" role="status">No subscription selected.</p></section>
</section><section id="section-usage" data-admin-section class="panel" hidden tabindex="-1" aria-label="Usage"><h2>Usage and costs</h2><p id="usage-details" role="status">Choose Usage beside a tenant to view authoritative request counts. AWS cost and Stripe invoice amounts are unavailable without verified provider data.</p></section>
<section id="section-audit" data-admin-section class="panel" hidden tabindex="-1" aria-label="Audit"><h2>Audit receipt lookup</h2><p class="muted">Read one stored administrative event by its exact event ID. No exports or audit enumeration.</p><label for="audit-id">Audit event ID</label> <input id="audit-id" maxlength="64" autocomplete="off" spellcheck="false"> <button type="button" id="audit-go">Verify receipt</button><p id="audit-result" role="status"></p></section>
<section id="section-catalog" data-admin-section class="panel" hidden tabindex="-1" aria-label="Catalog"><h2>Categories and checks</h2><p id="catalog-summary"></p><div id="categories"></div>
<h3>Versioned source history</h3><p id="catalog-release-summary" role="status"></p><ol id="catalog-release-entries"></ol><p class="muted">Read-only shipped description history. Fingerprint consistency is not an approval signature, live deployment evidence or permission to override tenant checks. Rollback is a separately reviewed next-version source release.</p>
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
<button id="draft-review" type="button" disabled>Record signed review</button>
<button id="review-manifest" type="button" disabled>Preview reviewed metadata manifest</button>
<button id="manifest-export" type="button" disabled>Export reviewed forward/rollback JSON (not published)</button>
<p id="draft-result" role="status"></p>
<p id="review-manifest-result" role="status"></p></section></div></div>
</main><script src="/admin/client.js" defer></script></body></html>"""

# Zero third-party scripts. OAuth access token is deliberately memory-only.
_JS = r'''"use strict";
(() => {
  let token = null;
  let config = null;
  let nativeTicket = null;
  let enrollmentTicket = null;
  let enrollmentEpoch = 0;
  let enrollmentTimer = null;
  let enrollmentVerifying = false;
  let nextCursor = null;
  let nextUserCursor = null;
  let previewSelection = null;
  let previewReady = null;
  let draftReadyForReview = null;
  let reviewReadyForManifest = null;
  let exportableManifest = null;
  let authEpoch = 0;
  // A late response from an earlier tenant must not replace the latest owner selection.
  let usageSelection = 0;
  let billingSelection = 0;
  let features = {tenant_status_changes:false};
  const el = id => document.getElementById(id);
  const say = (id, value) => { el(id).textContent = value; };
  const sectionCopy = {
    overview: ["Overview", "Inspect your platform, accounts and safeguards from one workspace."],
    users: ["Users", "Search Cognito accounts, inspect identity state and manage sign-in access."],
    tenants: ["Tenants", "Inspect saved customer entitlements, request usage and audited access controls."],
    billing: ["Billing", "Review provider-verified subscriptions and recent invoice summaries."],
    usage: ["Usage and costs", "Inspect recorded requests and saved quotas; unavailable costs stay unavailable."],
    audit: ["Audit", "Verify one persisted administrative receipt using its exact event ID."],
    catalog: ["Catalog", "Explore published rules and review versioned description proposals."]
  };
  function showSection(name, focus = false) {
    if (!token || !Object.prototype.hasOwnProperty.call(sectionCopy, name)) return;
    const target = el("section-" + name);
    for (const panel of document.querySelectorAll("[data-admin-section]")) panel.hidden = panel !== target;
    for (const button of document.querySelectorAll("#admin-navigation [data-section]")) {
      if (button.dataset.section === name) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
    say("workspace-title", sectionCopy[name][0]);
    say("workspace-description", sectionCopy[name][1]);
    document.title = "GuardOath Admin · " + sectionCopy[name][0];
    const emptyStates = {
      "usage-details": "Choose Usage beside a tenant to view recorded requests and saved quotas.",
      "billing-details": "Choose Verify Stripe beside a tenant to review its subscription.",
      "invoice-details": "Choose Invoices beside a tenant to inspect verified recent history."
    };
    for (const [id, message] of Object.entries(emptyStates)) {
      if (target.contains(el(id)) && !el(id).textContent) say(id, message);
    }
    if (focus) {
      target.focus({preventScroll:true});
      window.scrollTo({top:0, behavior:"auto"});
    }
  }
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
  const setLoggedIn = logged => {
    el("login").hidden = logged;
    el("app").hidden = !logged;
    if (logged) showSection("overview");
    else document.title = "GuardOath Admin";
  };
  const invalidateCatalogExport = () => {
    exportableManifest = null;
    el("manifest-export").disabled = true;
  };
  function exportReviewedManifest() {
    if (!token || !exportableManifest ||
        el("draft-id").value.trim() !== exportableManifest.draft_id) {
      invalidateCatalogExport();
      return;
    }
    // The existing authenticated, audited manifest has already been verified.
    // Export only the bounded allowlisted source-plan fields; never a session,
    // customer record, audit secret or executable source file.
    const body = JSON.stringify(exportableManifest, null, 2) + "\n";
    const blob = new Blob([body], {type:"application/json"});
    const url = URL.createObjectURL(blob);
    try {
      const link = document.createElement("a");
      link.href = url;
      link.download = "guardoath-catalog-plan-" + exportableManifest.review_id.slice(0, 12) + ".json";
      document.body.append(link);
      link.click();
      link.remove();
    } finally {
      setTimeout(() => URL.revokeObjectURL(url), 0);
    }
  }
  const clearAdminData = () => {
    invalidateCatalogExport();
    el("metrics").replaceChildren();
    el("tenant-rows").replaceChildren();
    el("user-rows").replaceChildren();
    el("categories").replaceChildren();
    el("catalog-release-entries").replaceChildren();
    for (const id of ["catalog-release-summary", "status", "tenant-status", "user-status", "user-inspect-result", "user-change-result", "catalog-summary", "user-action-result", "usage-details", "billing-details", "invoice-details", "audit-result", "proposal-result", "draft-result", "review-manifest-result"]) say(id, "");
    previewSelection = null;
    previewReady = null;
    draftReadyForReview = null;
    reviewReadyForManifest = null;
    el("draft-review").disabled = true;
    el("review-manifest").disabled = true;
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
    el("user-change-email").value = "";
    el("user-action-key").value = "";
    el("user-action-email").value = "";
    el("user-change").disabled = true;
    el("user-action-inspect").disabled = true;
    nextUserCursor = null;
    el("user-next").disabled = true;
    nextCursor = null;
    features = {tenant_status_changes:false};
    el("next").disabled = true;
  };
  const signOut = (message = "Signed out of this tab") => {
    authEpoch += 1;
    token = null;
    nativeTicket = null;
    el("native-password").value = "";
    el("native-code").value = "";
    el("native-challenge").hidden = true;
    void cancelEnrollment(false);
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
    // Reuse existing authenticated, bounded directory pages; never add a
    // provider read for the Overview. A partial page is not a population total.
    const completePageCount = (page, field) => {
      if (!page || page.status !== "ok" || !Array.isArray(page[field]) ||
          !Object.prototype.hasOwnProperty.call(page, "next_cursor") ||
          page.next_cursor !== null || !Number.isSafeInteger(page.returned) ||
          page.returned < 0 || page.returned > 20 ||
          page.returned !== page[field].length ||
          (field === "tenants" && (!Number.isSafeInteger(page.scanned) ||
              page.scanned !== page.returned))) return null;
      return page.returned;
    };
    const userCount = completePageCount(users, "users");
    const tenantCount = completePageCount(tenants, "tenants");
    // This is a count of saved ACTIVE flags only, not verified Stripe
    // subscriptions, billable accounts, current entitlements or revenue.
    // Require one complete, unambiguous page; never manufacture zero from
    // unknown legacy billing flags, duplicates or a stale partial scan.
    const savedStatesComplete = tenantCount !== null &&
      tenants.tenants.every(t => t && typeof t.tenant_id === "string" &&
        t.tenant_id.length > 0 && (t.demo === true || t.demo === false) &&
        (t.demo === true || ["active","inactive","past_due","canceled"].includes(t.subscription_status))) &&
      new Set(tenants.tenants.map(t => t.tenant_id)).size === tenantCount;
    const savedActiveFlags = savedStatesComplete ?
      tenants.tenants.filter(t => t.demo === false && t.subscription_status === "active").length : null;
    // Never sum incomplete, mixed-month, ambiguous, or imprecise saved usage.
    // Includes demo tenants: a stored validation counter is not a billable event.
    const usagePeriod = typeof tenants?.retrieved_utc === "string" &&
      /^[0-9]{4}-(0[1-9]|1[0-2])-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$/.test(tenants.retrieved_utc)
      ? tenants.retrieved_utc.slice(0, 7) : null;
    let recordedMonthRequests = null;
    if (tenantCount !== null && usagePeriod !== null &&
        new Set(tenants.tenants.map(t => t?.tenant_id)).size === tenantCount &&
        tenants.tenants.every(t => t && typeof t.tenant_id === "string" &&
          /^[a-z][a-z0-9-]{1,39}$/.test(t.tenant_id) &&
          t.usage_month === usagePeriod &&
          Number.isSafeInteger(t.usage_month_count) && t.usage_month_count >= 0)) {
      const sum = tenants.tenants.reduce((total, t) => total + t.usage_month_count, 0);
      if (Number.isSafeInteger(sum)) recordedMonthRequests = sum;
    }
    // Cost-control signal from saved tenant caps, never AWS spend or an
    // assertion that a soft overage policy actually blocked requests.
    // The same complete, UTC-aligned, numerically safe population required
    // for request totals is mandatory. Legacy/partial allowances are unknown.
    const savedCapsComplete = recordedMonthRequests !== null &&
      tenants.tenants.every(t => (t.demo === true || t.demo === false) &&
        Number.isSafeInteger(t.monthly_allowance) &&
        0 <= t.monthly_allowance && t.monthly_allowance <= 1_000_000_000);
    const savedCapReachedTenants = savedCapsComplete ?
      tenants.tenants.filter(t => t.demo === false &&
        t.usage_month_count >= t.monthly_allowance).length : null;
    // A saved allow-and-record overage policy is not a hard cap. Only count
    // this exposure from a complete and unambiguous recorded tenant snapshot.
    const softPoliciesComplete = savedCapsComplete &&
      tenants.tenants.every(t => t.demo === true ||
        t.overage_policy === "block" || t.overage_policy === "allow_and_record");
    const softCapReachedTenants = softPoliciesComplete ?
      tenants.tenants.filter(t => t.demo === false &&
        t.overage_policy === "allow_and_record" &&
        t.usage_month_count >= t.monthly_allowance).length : null;
    const sourceTime = page =>
      typeof page?.retrieved_utc === "string" &&
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(page.retrieved_utc) ?
        " · read at " + page.retrieved_utc + " (not activity freshness)" :
        " · read time unavailable";
    const metrics = {...(overview.metrics || {})};
    // These counts are only the complete directory pages observed on refresh.
    // Saved subscription_status is never counted as a verified paid subscriber.
    if (metrics.users?.value == null) {
      metrics.users = {value:userCount,
        source:userCount === null ? "Cognito · total unavailable (partial or failed read)" :
          "Cognito ListUsers · complete bounded page" + sourceTime(users),
        status:userCount === null ? "unavailable" : "complete_single_page"};
    }
    metrics.tenant_records = {value:tenantCount,
      source:tenantCount === null ? "DynamoDB · total unavailable (partial or failed read)" :
        "DynamoDB tenant table · complete bounded page (includes demos)" + sourceTime(tenants),
      status:tenantCount === null ? "unavailable" : "complete_single_page"};
    metrics.saved_active_subscription_flags = {value:savedActiveFlags,
      source:savedActiveFlags === null ?
        "DynamoDB · incomplete, partial or ambiguous saved subscription flags" :
        "DynamoDB saved subscription_status=active" + sourceTime(tenants) +
        " · NOT Stripe-verified paid subscribers or revenue",
      status:savedActiveFlags === null ? "unavailable" : "complete_single_page_saved_flags_only"};
    metrics.recorded_month_to_date_requests = {value:recordedMonthRequests,
      source:recordedMonthRequests === null ?
        "DynamoDB · unknown, partial, mixed-month or ambiguous recorded usage counters" :
        "DynamoDB tenant usage counters · UTC " + usagePeriod +
        " · complete bounded scan (includes demos)" + sourceTime(tenants) +
        " · recorded validations only; NOT live traffic, billable usage, AWS cost or Stripe revenue",
      status:recordedMonthRequests === null ? "unavailable" : "complete_single_page_recorded_usage"};
    metrics.saved_allowance_reached_tenants = {value:savedCapReachedTenants,
      source:savedCapReachedTenants === null ?
        "DynamoDB · unavailable (partial page, unknown cap, invalid identity or UTC counter)" :
        "DynamoDB saved tenant allowances · UTC " + usagePeriod +
        " · complete bounded scan of non-demo accounts" + sourceTime(tenants) +
        " · count at/above allowance; NOT AWS cost, active alerts or proof of blocking overages",
      status:savedCapReachedTenants === null ? "unavailable" :
        "complete_single_page_saved_allowance_signal"};
    metrics.saved_soft_cap_reached_tenants = {value:softCapReachedTenants,
      source:softCapReachedTenants === null ?
        "DynamoDB · unknown policy, cap, UTC counter or incomplete tenant population" :
        "DynamoDB saved allow_and_record policy at/above saved cap · UTC " + usagePeriod +
        sourceTime(tenants) +
        " · all non-demo tenants; NOT hard blocking, billed overages, live activity or alerts",
      status:softCapReachedTenants === null ? "unavailable" :
        "complete_single_page_saved_soft_cap_signal"};
    el("metrics").replaceChildren();
    for (const [name, metric] of Object.entries(metrics)) {
      const card = document.createElement("div"); card.className = "metric";
      card.append(textNode("div", name.replaceAll("_", " ")),
        textNode("strong", metric?.value ?? "Unavailable"),
        textNode("small", metric?.source || "Unknown source"));
      el("metrics").append(card);
    }
    features = available;
    el("user-change").disabled = !features.cognito_user_status_changes;
    el("user-action-inspect").disabled = !features.cognito_action_receipts;
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
    const history = catalog.metadata_releases;
    el("catalog-release-entries").replaceChildren();
    const validHistory = history && history.status === "verified_source_history" &&
      history.source_chain_verified === true &&
      history.registry_version === catalog.registry_version &&
      Number.isSafeInteger(history.release_count) && history.release_count >= 0 &&
      Array.isArray(history.releases) &&
      history.releases.length === Math.min(history.release_count, 20) &&
      history.releases.every(item =>
        item && typeof item.registry_version === "string" &&
        /^[1-9][0-9]{0,8}$/.test(item.registry_version) &&
        ["category_id", "check_id", "rule_id"].every(key =>
          typeof item[key] === "string" && item[key].length < 101) &&
        Array.isArray(item.changed_fields) && item.changed_fields.length >= 1 &&
        item.changed_fields.length <= 2 && item.changed_fields.every(
          field => field === "title" || field === "use_case"));
    if (!validHistory) {
      say("catalog-release-summary", "Source release history unavailable or failed verification; no zero assumed.");
    } else {
      say("catalog-release-summary", "Source-controlled metadata history: " +
        history.release_count + " shipped releases; chain checked against registry " +
        history.registry_version + ". Not a deployment acceptance or signed approval." +
        (history.has_older_releases ? " Showing newest 20." : ""));
      for (const item of history.releases) {
        el("catalog-release-entries").append(textNode("li",
          "Version " + item.registry_version + " · " + item.category_id + "/" +
          item.check_id + "/" + item.rule_id +
          " · reviewed source fields: " + item.changed_fields.join(", ")));
      }
    }
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
        const evidence = check.test_evidence || {};
        const fileCount = count => Number.isSafeInteger(count) && count >= 0 ? String(count) : "unavailable";
        const stages = Array.isArray(check.stages) && check.stages.length ? check.stages.join(", ") : "unknown";
        const actions = Array.isArray(check.supported_actions) && check.supported_actions.length
          ? check.supported_actions.join(", ") : "unknown";
        const evidenceLine = document.createElement("p");
        evidenceLine.className = "muted";
        evidenceLine.append(textNode("span", "Stages: " + stages + " · Actions: " + actions +
          " · Declared unit-test files: " + fileCount(evidence.unit_test_files_declared) +
          " · Declared evaluation files: " + fileCount(evidence.evaluation_files_declared) +
          " · Registry declarations only; NOT current CI or live deployment evidence."));
        item.append(evidenceLine);
        if (check.use_case) item.append(textNode("p", "Check use case: " + check.use_case));
        if (check.limitations) item.append(textNode("p", "Limitations: " + check.limitations));
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
    say("status", "Administration loaded · Overview" + readAuditLabel(overview) +
      " · Capabilities" + readAuditLabel(available) +
      (features.sensitive_read_audit ? "" : " · Persistent read auditing not configured"));
  }
  const unsafeCatalogText = value => /[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u.test(value);
  async function previewCatalogRule() {
    invalidateCatalogExport();
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
    if (unsafeCatalogText(proposed.title) || unsafeCatalogText(proposed.use_case)) {
      say("proposal-result", "Catalog descriptions cannot contain invisible or bidirectional control characters");
      return;
    }
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
    invalidateCatalogExport();
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
    if (unsafeCatalogText(reason)) {
      say("proposal-result", "Draft reason cannot contain invisible or bidirectional control characters");
      return;
    }
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
      draftReadyForReview = null;
      el("draft-review").disabled = true;
      previewReady = null;
      el("proposal-save").disabled = true;
      say("proposal-result", "Signed draft saved · " + result.draft_id +
        " · NOT reviewed or published; source-controlled release approval and tests still needed" +
        readAuditLabel(result));
    } catch (err) {if (token && epoch === authEpoch) say("proposal-result", err.message);}
  }
  async function inspectCatalogDraft() {
    invalidateCatalogExport();
    draftReadyForReview = null;
    reviewReadyForManifest = null;
    el("review-manifest").disabled = true;
    say("review-manifest-result", "");
    el("draft-review").disabled = true;
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
      if (!result.stale && features.catalog_review_receipts &&
          /^[a-f0-9]{64}$/.test(result.draft.baseline_sha256) &&
          /^[a-f0-9]{64}$/.test(result.draft.proposal_sha256)) {
        draftReadyForReview = {id, baseline:result.draft.baseline_sha256,
                               proposal:result.draft.proposal_sha256};
        el("draft-review").disabled = false;
      }
      say("draft-result", "Signed persistent draft · " + id +
        " · target " + result.draft.rule_id +
        " · baseline SHA-256 " + result.draft.baseline_sha256 +
        " · " + (result.stale ? "STALE — source changed; preview again" :
          "Current baseline — still requires review, tests and versioned source release") +
        " · NOT published" + readAuditLabel(result));
    } catch (err) {if (token && epoch === authEpoch) say("draft-result", err.message);}
  }
  async function reviewCatalogDraft() {
    invalidateCatalogExport();
    if (!token || !features.catalog_review_receipts || !draftReadyForReview) return;
    reviewReadyForManifest = null;
    el("review-manifest").disabled = true;
    say("review-manifest-result", "");
    const selected = {...draftReadyForReview};
    if (el("draft-id").value.trim() !== selected.id) {
      draftReadyForReview = null;
      el("draft-review").disabled = true;
      say("draft-result", "Draft selection changed; inspect again before review");
      return;
    }
    const confirmation = prompt("To record a review, type REVIEW " + selected.id.slice(-8));
    if (confirmation !== "REVIEW " + selected.id.slice(-8)) {
      say("draft-result", "Exact review confirmation required");
      return;
    }
    const reason = prompt("Review reason (10–240 characters; no secrets or customer data):");
    if (!reason || reason.length < 10 || reason.length > 240) return;
    if (unsafeCatalogText(reason)) {
      say("draft-result", "Review reason cannot contain invisible or bidirectional control characters");
      return;
    }
    if (!confirm("Record an immutable catalog review? This does NOT publish rules, change detector behavior, or modify tenants.")) return;
    const epoch = authEpoch;
    draftReadyForReview = null;
    el("draft-review").disabled = true;
    try {
      const result = await request("/v1/admin/catalog/drafts/" + selected.id + "/review", {
        method:"POST", body:JSON.stringify({
          confirmation, reason, idempotency_key:crypto.randomUUID().replaceAll("-", "")
        })
      });
      if (!token || epoch !== authEpoch) return;
      if (result.status !== "review_recorded" || result.persisted !== true ||
          result.reviewed !== true || result.published !== false ||
          result.ready_to_publish !== false || result.draft_id !== selected.id ||
          result.baseline_sha256 !== selected.baseline ||
          result.proposal_sha256 !== selected.proposal ||
          !/^[a-f0-9]{64}$/.test(result.review_id))
        throw new Error("Review receipt verification unavailable");
      const verified = await request("/v1/admin/catalog/reviews/" + result.review_id);
      if (!token || epoch !== authEpoch) return;
      if (!verified.review || verified.review.review_id !== result.review_id ||
          verified.review.draft_id !== selected.id ||
          verified.review.baseline_sha256 !== selected.baseline ||
          verified.review.proposal_sha256 !== selected.proposal ||
          verified.review.status !== "review_recorded" ||
          verified.review.published !== false || verified.published !== false ||
          verified.ready_to_publish !== false ||
          typeof verified.stale !== "boolean" ||
          verified.source_current !== !verified.stale)
        throw new Error("Persisted catalog review integrity unavailable");
      if (!verified.stale) {
        reviewReadyForManifest = {reviewId:result.review_id, draftId:selected.id,
          baseline:selected.baseline, proposal:selected.proposal};
        el("review-manifest").disabled = false;
      }
      say("draft-result", "Immutable signed review verified · " + result.review_id +
        " · source baseline " + selected.baseline +
        (verified.stale ? " · STALE — published catalog changed; review cannot be used as current-source evidence" :
          " · current-source fingerprint independently verified") +
        " · publication NOT authorized; versioned tests, source release and rollback remain required" +
        readAuditLabel(verified));
    } catch(err) {if (token && epoch === authEpoch) say("draft-result", err.message);}
  }

  async function inspectReviewManifest() {
    invalidateCatalogExport();
    if (!token || !features.catalog_review_receipts || !reviewReadyForManifest) return;
    const selected = {...reviewReadyForManifest};
    reviewReadyForManifest = null;
    el("review-manifest").disabled = true;
    if (el("draft-id").value.trim() !== selected.draftId) {
      say("review-manifest-result", "Draft selection changed; inspect and review again");
      return;
    }
    const epoch = authEpoch;
    try {
      const result = await request("/v1/admin/catalog/reviews/" +
        selected.reviewId + "/manifest");
      if (!token || epoch !== authEpoch) return;
      if (result.status !== "metadata_manifest_preview_only" ||
          result.review_id !== selected.reviewId ||
          result.draft_id !== selected.draftId ||
          result.reviewed_source_sha256 !== selected.baseline ||
          result.reviewed_proposal_sha256 !== selected.proposal ||
          !/^[a-f0-9]{64}$/.test(result.candidate_metadata_sha256) ||
          result.rollback_source_sha256 !== selected.baseline ||
          result.published !== false || result.ready_to_publish !== false ||
          result.tenant_policies_changed !== false ||
          result.detector_behavior_changed !== false ||
          result.source_current !== true || result.acceptance_tests_run !== false ||
          result.release_kind !== "review_only_projection_not_executable_source" ||
          result.reversible_patch?.kind !== "reviewed_metadata_source_plan_only" ||
          result.reversible_patch?.round_trip_verified !== true ||
          result.reversible_patch?.published !== false ||
          result.reversible_patch?.executable_source_committed !== false ||
          result.reversible_patch?.tenant_policies_changed !== false ||
          result.reversible_patch?.target?.category_id !== result.category_id ||
          result.reversible_patch?.target?.check_id !== result.check_id ||
          result.reversible_patch?.target?.rule_id !== result.rule_id ||
          result.reversible_patch?.forward?.expected_metadata_sha256 !== selected.baseline ||
          result.reversible_patch?.forward?.candidate_metadata_sha256 !== result.candidate_metadata_sha256 ||
          result.reversible_patch?.rollback?.expected_metadata_sha256 !== result.candidate_metadata_sha256 ||
          result.reversible_patch?.rollback?.candidate_metadata_sha256 !== selected.baseline ||
          result.reversible_patch?.forward?.expected_registry_version !== result.registry_version ||
          result.reversible_patch?.forward?.candidate_registry_version !== result.candidate_registry_version ||
          result.reversible_patch?.rollback?.expected_registry_version !== result.candidate_registry_version ||
          result.reversible_patch?.rollback?.candidate_registry_version !== result.registry_version ||
          !Array.isArray(result.changed_fields) ||
          !result.changed_fields.length || !result.changed_fields.every(
            field => field === "title" || field === "use_case"))
        throw new Error("Reviewed metadata manifest integrity unavailable");
      // A SHA-256 metadata plan is not a cryptographic publication receipt.
      // Reject mismatched/extra fields before letting the owner export a
      // source-change candidate, even if a response has plausible fingerprints.
      const fields = result.changed_fields;
      const forward = result.reversible_patch.forward.changes;
      const rollback = result.reversible_patch.rollback.changes;
      const described = result.changed_metadata;
      const keysMatch = obj => obj && typeof obj === "object" && !Array.isArray(obj) &&
        JSON.stringify(Object.keys(obj).sort()) === JSON.stringify([...fields].sort());
      if (fields.length > 2 || new Set(fields).size !== fields.length ||
          !keysMatch(forward) || !keysMatch(rollback) || !keysMatch(described))
        throw new Error("Reviewed metadata change attribution unavailable");
      for (const field of fields) {
        const pair = forward[field], inverse = rollback[field], actual = described[field];
        const validPair = item => item && typeof item === "object" && !Array.isArray(item) &&
          Object.keys(item).sort().join(",") === "after,before" &&
          typeof item.before === "string" && typeof item.after === "string" &&
          item.before.length <= 4096 && item.after.length <= 4096 &&
          !unsafeCatalogText(item.before) && !unsafeCatalogText(item.after);
        if (!validPair(pair) || !validPair(inverse) || !validPair(actual) ||
            pair.before === pair.after ||
            inverse.before !== pair.after || inverse.after !== pair.before ||
            actual.before !== pair.before || actual.after !== pair.after)
          throw new Error("Reviewed metadata change attribution unavailable");
      }
      exportableManifest = {
        format:"guardoath.reviewed-catalog-metadata-source-plan.v1",
        status:"review_only_not_published",
        review_id:result.review_id, draft_id:result.draft_id,
        registry_version:result.registry_version,
        candidate_registry_version:result.candidate_registry_version,
        reviewed_source_sha256:result.reviewed_source_sha256,
        reviewed_proposal_sha256:result.reviewed_proposal_sha256,
        candidate_metadata_sha256:result.candidate_metadata_sha256,
        rollback_source_sha256:result.rollback_source_sha256,
        target:{category_id:result.category_id,check_id:result.check_id,rule_id:result.rule_id},
        changed_fields:[...fields],
        changed_metadata:Object.fromEntries(fields.map(field=>[field, {
          before:forward[field].before,after:forward[field].after}])),
        forward:{
          expected_registry_version:result.reversible_patch.forward.expected_registry_version,
          candidate_registry_version:result.reversible_patch.forward.candidate_registry_version,
          expected_metadata_sha256:result.reversible_patch.forward.expected_metadata_sha256,
          candidate_metadata_sha256:result.reversible_patch.forward.candidate_metadata_sha256,
          changes:Object.fromEntries(fields.map(field=>[field, {
            before:forward[field].before,after:forward[field].after}]))
        },
        rollback:{
          expected_registry_version:result.reversible_patch.rollback.expected_registry_version,
          candidate_registry_version:result.reversible_patch.rollback.candidate_registry_version,
          expected_metadata_sha256:result.reversible_patch.rollback.expected_metadata_sha256,
          candidate_metadata_sha256:result.reversible_patch.rollback.candidate_metadata_sha256,
          changes:Object.fromEntries(fields.map(field=>[field, {
            before:rollback[field].before,after:rollback[field].after}]))
        },
        round_trip_verified:true, hashes_are_not_signatures:true,
        executable_source_committed:false, published:false,
        tenant_policies_changed:false, detector_behavior_changed:false,
        acceptance_tests_run:false
      };
      el("manifest-export").disabled = false;
      say("review-manifest-result", "Reviewed catalog metadata manifest · version " +
        result.registry_version + " → " + result.candidate_registry_version +
        " · " + result.category_id + "/" + result.check_id + "/" + result.rule_id +
        " · changed: " + result.changed_fields.join(", ") +
        " · candidate metadata SHA-256 " + result.candidate_metadata_sha256 +
        " · metadata forward/rollback round-trip verified" +
        " · rollback fingerprint " + result.rollback_source_sha256 +
        " · NOT published or executable; tests, exact source release and rollback required" +
        readAuditLabel(result));
    } catch(err) {
      if (token && epoch === authEpoch)
        say("review-manifest-result", "Manifest unavailable: " + err.message +
          " · no publication attempted");
    }
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
  async function changeUserStatus() {
    if (!token || !features.cognito_user_status_changes) return;
    const email = el("user-change-email").value.trim();
    const action = el("user-change-action").value;
    el("user-change-email").value = "";
    if (!/^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$/.test(email) ||
        email.length > 254 || email.startsWith(".") || email.includes("..") || email.endsWith(".") ||
        !["disable","enable"].includes(action)) {
      say("user-change-result", "Enter a valid exact account email and action");
      return;
    }
    const epoch = authEpoch;
    say("user-change-result", "Rechecking account state before any change...");
    try {
      const inspected = await request("/v1/admin/users/lookup", {
        method:"POST", body:JSON.stringify({email})
      });
      if (!token || epoch !== authEpoch) return;
      const expected = action === "disable";
      if (inspected.status !== "ok" || !inspected.user || inspected.user.enabled !== expected) {
        say("user-change-result", "Account unavailable or state changed. Inspect it before retrying.");
        return;
      }
      const confirmation = action.toUpperCase() + " " + email;
      if (prompt("Type exactly to confirm: " + confirmation) !== confirmation) return;
      const reason = prompt("Why is this sign-in access change needed? (10–240 characters; no personal data)");
      if (!reason || reason.length < 10 || reason.length > 240) return;
      if (!confirm("Change Cognito sign-in access for this exact account? This does NOT change Stripe or paid entitlement.")) return;
      const actionKey = crypto.randomUUID().replaceAll("-","");
      el("user-action-key").value = actionKey;
      el("user-action-email").value = email;
      say("user-action-result", "Retain action reference: " + actionKey);
      const result = await request("/v1/admin/users/status", {
        method:"POST", body:JSON.stringify({
          email, action, expected_enabled:expected, confirmation, reason,
          idempotency_key:actionKey
        })
      });
      if (!token || epoch !== authEpoch) return;
      if (result.status !== "verified" || result.enabled !== !expected ||
          !/^[a-f0-9]{64}$/.test(result.audit_event_id || "") ||
          !/^[a-f0-9]{64}$/.test(result.intent_event_id || "") ||
          !/^[a-f0-9]{64}$/.test(result.target_fingerprint || ""))
        throw new Error("Cognito status outcome unverified; inspect account before any new action");
      const verified = await request("/v1/admin/audit/" + result.audit_event_id);
      if (!token || epoch !== authEpoch) return;
      const receipt = verified.audit;
      const change = receipt && receipt.change;
      if (!receipt || receipt.integrity_status !== "signed_verified" ||
          receipt.operation !== "cognito.user.status.outcome" ||
          receipt.event_id !== result.audit_event_id ||
          change?.field !== "cognito_enabled" || change.before !== expected ||
          change.after !== !expected || change.related_event_id !== result.intent_event_id ||
          change.target_fingerprint !== result.target_fingerprint)
        throw new Error("Cognito outcome audit integrity unavailable; inspect account before any new action");
      say("user-change-result", "Cognito sign-in access changed and verified · signed outcome " +
          result.audit_event_id + " · immutable intent " + result.intent_event_id +
          " · saved audit source " + (receipt.source || "unavailable") + readAuditLabel(verified));
      await pageUsers();
    } catch (err) {
      if (token && epoch === authEpoch)
        say("user-change-result", err.message + " · inspect account and audit before another change");
    }
  }

  async function inspectUserActionReceipt() {
    if (!token || !features.cognito_action_receipts) return;
    const key = el("user-action-key").value.trim();
    if (!/^[A-Za-z0-9_-]{16,96}$/.test(key)) {
      say("user-action-result", "Enter the original 16–96 character action reference.");
      return;
    }
    const email = el("user-action-email").value.trim();
    if (email && (email.length > 254 || !/^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$/.test(email) ||
        email.startsWith(".") || email.endsWith(".") || email.includes(".."))) {
      say("user-action-result", "Invalid exact email; no current account queried.");
      return;
    }
    const epoch = authEpoch;
    say("user-action-result", email ? "Verifying signed history and live Cognito status..." :
        "Inspecting signed action trail...");
    try {
      const result = await request("/v1/admin/users/status/receipt", {
        method:"POST", body:JSON.stringify(email ? {idempotency_key:key,email} : {idempotency_key:key})
      });
      if (!token || epoch !== authEpoch) return;
      if (!["not_found","indeterminate","outcome_recorded"].includes(result.status) ||
          result.current_provider_state_verified !== (email !== "" && result.status !== "not_found"))
        throw new Error("Action receipt verification response invalid");
      if (result.status === "not_found") {
        if (result.intent_event_id !== null || result.outcome_event_id !== null)
          throw new Error("Action receipt identifiers invalid");
        say("user-action-result", "No signed intent for this owner reference; account state unknown." + readAuditLabel(result));
        return;
      }
      if (!/^[a-f0-9]{64}$/.test(result.intent_event_id || ""))
        throw new Error("Action intent identifier invalid");
      if (email && (typeof result.current_enabled !== "boolean" ||
          !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(result.current_observed_at_utc || "")))
        throw new Error("Current Cognito status verification invalid");
      const liveLabel = email ? " · independently observed current Cognito enabled: " +
        result.current_enabled + " · status observed UTC " + result.current_observed_at_utc +
        " (not proof of the historical provider action)" : "";
      if (result.status === "indeterminate") {
        if (result.outcome_event_id !== null ||
            (email && result.current_matches_recorded_outcome !== null))
          throw new Error("Unverified outcome");
        say("user-action-result", "Signed intent " + result.intent_event_id +
            " exists, but provider outcome is INDETERMINATE." + liveLabel +
            " A current state cannot establish whether the earlier action succeeded." + readAuditLabel(result));
        return;
      }
      if (!/^[a-f0-9]{64}$/.test(result.outcome_event_id || "") ||
          typeof result.enabled_at_verification !== "boolean")
        throw new Error("Historical action outcome invalid");
      if (email && (typeof result.current_matches_recorded_outcome !== "boolean" ||
          result.current_matches_recorded_outcome !==
            (result.current_enabled === result.enabled_at_verification)))
        throw new Error("Current and historical Cognito status comparison invalid");
      say("user-action-result", "Signed intent " + result.intent_event_id +
          " · signed historical outcome " + result.outcome_event_id +
          " · enabled at verification: " + result.enabled_at_verification +
          (email ? liveLabel + (result.current_matches_recorded_outcome ?
            " · current enabled state matches historical snapshot" :
            " · current enabled state DIFFERS from historical snapshot") :
            " · current Cognito status NOT checked. Inspect exact account before another action.") + readAuditLabel(result));
    } catch (err) {
      if (token && epoch === authEpoch) say("user-action-result", err.message);
    }
  }

  function showTenants(data) {
    el("tenant-rows").replaceChildren();
    for (const tenant of data.tenants || []) {
      const row = document.createElement("tr");
      for (const field of ["tenant_id", "display_name", "status", "subscription_status", "monthly_allowance", "usage_month_count"]) row.append(textNode("td", tenant[field]));
      row.append(textNode("td", tenant.overage_policy === "block" ? "Block at saved cap" :
        tenant.overage_policy === "allow_and_record" ? "Allow and record overage" : "Unavailable"));
      row.append(textNode("td", tenant["api_key_count"]));
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
      if (features.billing_invoices && tenant.demo === false) {
        const invoices = document.createElement("button");
        invoices.type = "button";
        invoices.textContent = "Invoices";
        invoices.addEventListener("click", () => inspectInvoices(tenant));
        actions.append(invoices);
      }
      if (features.billing_downgrade && features.audit_receipt_lookup && tenant.demo === false &&
          tenant.subscription_status === "active" && Number.isSafeInteger(tenant.revision) &&
          Number.isSafeInteger(tenant.monthly_allowance) && tenant.monthly_allowance >= 0) {
        const downgrade = document.createElement("button");
        downgrade.type = "button";
        downgrade.textContent = "Reconcile unpaid";
        downgrade.addEventListener("click", () => reconcileUnpaid(tenant));
        actions.append(downgrade);
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
    const selection = ++usageSelection;
    showSection("usage", true);
    say("usage-details", "Inspecting recorded usage for " + tenant.tenant_id + "...");
    try {
      const result = await request("/v1/admin/tenants/" + encodeURIComponent(tenant.tenant_id) + "/usage");
      if (!token || epoch !== authEpoch || selection !== usageSelection) return;
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
      const pacing = usage.quota_pacing || {};
      const pacingStates = ["unknown_counter", "stale_or_unknown_metadata", "zero_allowance",
        "insufficient_observation", "already_over_limit", "already_at_limit",
        "projected_over_limit", "projected_near_limit", "projected_watch",
        "projected_within_limit"];
      const pacingState = pacingStates.includes(pacing.state)
        ? pacing.state.replaceAll("_", " ") : "unavailable";
      const projected = Number.isSafeInteger(pacing.projected_month_end_requests_floor) &&
        pacing.projected_month_end_requests_floor >= 0
        ? pacing.projected_month_end_requests_floor : "Unavailable";
      const metadata = ["recent_record_metadata", "stale_record_metadata", "unavailable"]
        .includes(pacing.record_metadata_freshness)
        ? pacing.record_metadata_freshness.replaceAll("_", " ") : "unavailable";
      const history = usage.historical_volume || {};
      const historyStates = ["unavailable", "insufficient_history", "low_volume_history",
        "possible_request_surge", "elevated_request_volume", "lower_request_volume",
        "within_historical_range"];
      const historyState = historyStates.includes(history.state)
        ? history.state.replaceAll("_", " ") : "unavailable";
      const historyMedian = Number.isSafeInteger(history.history_median_monthly_requests) &&
        history.history_median_monthly_requests >= 0
        ? history.history_median_monthly_requests : "Unavailable";
      const historyRatio = Number.isSafeInteger(history.projected_vs_history_percent_floor) &&
        history.projected_vs_history_percent_floor >= 0
        ? history.projected_vs_history_percent_floor + "%" : "Unavailable";
      say("usage-details", tenant.tenant_id + " · UTC " + (usage.period_utc || "unknown") +
        " · Recorded: " + (count === null || count === undefined ? "Unavailable" : count) +
        " · Allowance: " + (usage.monthly_allowance ?? "Unavailable") +
        " · Remaining before allowance: " + (remaining === null || remaining === undefined ? "Unavailable" : remaining) +
        " · Overage policy: " + (usage.overage_policy || "Unavailable") +
        " · Recorded quota signal: " + level + " (" + percent + ")" +
        " · Saved-cap behavior: " + policy +
        " · Projected recorded month-end requests: " + projected +
        " · Quota pacing: " + pacingState +
        " · Record metadata: " + metadata +
        " · Advisory linear pacing only; not a live cost or automated alert" +
        " · Historical recorded-request volume: " + historyState +
        " · Prior 3-month median: " + historyMedian +
        " · Projected vs prior median: " + historyRatio +
        " · Volume comparison is advisory; historical records may be incomplete" +
        " · Record updated: " + (usage.record_updated_at || "Unavailable") +
        " · AWS costs: Unavailable · Stripe invoices: Unavailable · Source: " + (usage.source || "unknown") + readAuditLabel(result));
    } catch (err) {
      if (token && epoch === authEpoch && selection === usageSelection) say("usage-details", err.message);
    }
  }
  async function verifyBilling(tenant) {
    if (!token || !features.billing_verify) return;
    const epoch = authEpoch;
    const selection = ++billingSelection;
    showSection("billing", true);
    say("invoice-details", "No invoice history selected for this subscription inspection.");
    say("billing-details", "Verifying subscription for " + tenant.tenant_id + "...");
    try {
      const result = await request("/v1/admin/subscriptions/" + encodeURIComponent(tenant.tenant_id));
      if (token && epoch === authEpoch && selection === billingSelection) {
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
          " · Cancellation schedule: " +
          (state.verified && state.cancellation_status === "scheduled_for_period_end"
            ? "Scheduled at current period end (UTC: " +
              (typeof state.cancellation_effective_utc === "string" ?
                state.cancellation_effective_utc : "Unavailable") + ")" :
           state.verified && state.cancellation_status === "scheduled_for_date"
            ? "Scheduled for provider date (UTC: " +
              (typeof state.cancellation_effective_utc === "string" ?
                state.cancellation_effective_utc : "Unavailable") + ")" :
           state.verified && state.cancellation_status === "not_scheduled"
            ? "Not scheduled" : "Unavailable") +
          " · Invoice collection method: " +
          (state.verified && state.collection_method === "send_invoice"
            ? "Send invoice (payment unverified)" :
           state.verified && state.collection_method === "charge_automatically"
            ? "Automatic charge (payment unverified)" : "Unavailable") +
          " · Billing period source: " +
          (state.verified && state.current_period_source === "subscription_item"
            ? "Verified subscription item" :
           state.verified && state.current_period_source === "subscription"
            ? "Verified subscription" : "Unavailable") +
          " · Payment collection: " +
          (state.verified && state.collection_status === "paused"
            ? "Paused (" + (["keep_as_draft","mark_uncollectible","void"].includes(state.collection_behavior) ?
                state.collection_behavior : "behavior unavailable") +
              "; resume UTC: " + (state.collection_resumes_at_utc || "Not scheduled") + ")"
            : state.verified && state.collection_status === "collecting" ? "Not paused" : "Unavailable") +
          " · Next invoice amount: Unavailable" +
          " · UTC provider read: " + (state.verified_at || "Unavailable") + readAuditLabel(result));
        say("status", tenant.tenant_id + ": subscription reviewed; no entitlement change");
      }
    } catch(err) { if (token && epoch === authEpoch && selection === billingSelection) {
      say("billing-details", tenant.tenant_id + ": Stripe verification unavailable; no entitlement change");
      say("status", err.message);
    }}
  }
  async function inspectInvoices(tenant) {
    if (!token || !features.billing_invoices) return;
    const epoch = authEpoch;
    const selection = ++billingSelection;
    showSection("billing", true);
    say("billing-details", "No subscription verification selected for this invoice inspection.");
    say("invoice-details", "Inspecting verified Stripe history...");
    try {
      const result = await request("/v1/admin/subscriptions/" +
        encodeURIComponent(tenant.tenant_id) + "/invoices");
      if (!token || epoch !== authEpoch || selection !== billingSelection) return;
      const history = result.history || {};
      if (history.verified === false && history.status === "unbound") {
        say("invoice-details", tenant.tenant_id +
          " · No verified Stripe binding; invoices unavailable" + readAuditLabel(result));
        return;
      }
      if (history.verified !== true || history.status !== "verified" ||
          !Array.isArray(history.invoices) || history.invoices.length > 5 ||
          history.count !== history.invoices.length || typeof history.has_more !== "boolean")
        throw new Error("Verified invoice history unavailable");
      const rows = history.invoices.map(row => {
        if (!["draft","open","paid","uncollectible","void"].includes(row.status) ||
            !/^[a-z]{3}$/.test(row.currency) ||
            ![row.amount_due_minor,row.amount_paid_minor,row.amount_remaining_minor]
              .every(n => Number.isSafeInteger(n) && n >= 0) ||
            typeof row.created_utc !== "string" ||
            ![null, "charge_automatically", "send_invoice"].includes(row.collection_method) ||
            ![null, true, false].includes(row.attempted) ||
            !(row.attempt_count === null ||
              (Number.isSafeInteger(row.attempt_count) &&
               row.attempt_count >= 0 && row.attempt_count <= 10000)) ||
            !(row.next_payment_attempt_utc === null ||
              (typeof row.next_payment_attempt_utc === "string" &&
               /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(row.next_payment_attempt_utc))))
          throw new Error("Invalid invoice summary");
        const collection = row.status === "open" && row.amount_remaining_minor > 0
          ? " · Collection: " + (row.collection_method || "Unavailable") +
            " · Attempted: " + (row.attempted === null ? "Unavailable" :
              (row.attempted ? "Yes" : "No")) +
            " · attempts recorded: " + (row.attempt_count === null ? "Unavailable" :
              row.attempt_count) +
            " · next attempt recorded: " + (row.next_payment_attempt_utc || "Unavailable") +
            " (not proof of payment failure)"
          : "";
        return row.created_utc + " " + row.status + " " + row.currency.toUpperCase() +
          " due " + row.amount_due_minor + " / paid " + row.amount_paid_minor +
          " / remaining " + row.amount_remaining_minor + " (minor units)" + collection;
      });
      // Recompute provider-page review from the already-validated redacted rows.
      // This is not evidence of failed payment, overdue status, or total revenue.
      const review = history.payment_review;
      let reviewLabel = "";
      if (review != null) {
        const open = history.invoices.filter(row =>
          row.status === "open" && row.amount_remaining_minor > 0);
        const uncollectible = history.invoices.filter(row => row.status === "uncollectible");
        const expectedBalances = Object.create(null);
        for (const row of open)
          expectedBalances[row.currency] = (expectedBalances[row.currency] || 0) + row.amount_remaining_minor;
        const reported = review.open_balance_by_currency_minor;
        if (review.scope !== "verified_first_page_only" ||
            review.open_with_balance_count !== open.length ||
            review.uncollectible_count !== uncollectible.length ||
            review.complete_history !== !history.has_more ||
            !reported || typeof reported !== "object" || Array.isArray(reported) ||
            Object.keys(reported).length !== Object.keys(expectedBalances).length ||
            Object.entries(expectedBalances).some(([currency, amount]) => reported[currency] !== amount))
          throw new Error("Invoice review summary mismatch");
        reviewLabel = " · Open with balance (shown page): " + open.length +
          " · Uncollectible (shown page): " + uncollectible.length +
          " · Open remaining by currency: " +
          (Object.keys(expectedBalances).length
            ? Object.entries(expectedBalances).map(([currency, minor]) =>
                currency.toUpperCase() + " " + minor + " minor units").join(", ")
            : "none") +
          (history.has_more ? " · Partial invoice history" : " · Complete provider page");
      }
      say("invoice-details", tenant.tenant_id + " · " +
        (rows.length ? rows.join(" · ") : "No invoices in provider page") +
        (history.has_more ? " · More exist; only first five shown" : "") +
        reviewLabel +
        " · Direct Stripe, not revenue or overdue determination · UTC " + (history.read_at_utc || "unknown") +
        readAuditLabel(result));
    } catch (err) {
      if (token && epoch === authEpoch && selection === billingSelection)
        say("invoice-details", tenant.tenant_id + " · Invoice verification unavailable; no billing changes");
    }
  }

  async function reconcileUnpaid(tenant) {
    if (!token || !features.billing_downgrade || !features.audit_receipt_lookup ||
        tenant.subscription_status !== "active" || !Number.isSafeInteger(tenant.revision) ||
        !Number.isSafeInteger(tenant.monthly_allowance) || tenant.monthly_allowance < 0) return;
    const confirmation = prompt("Only REVOKES paid entitlement after verified Stripe delinquency. Type RECONCILE " + tenant.tenant_id);
    if (confirmation !== "RECONCILE " + tenant.tenant_id) return;
    const reason = prompt("Reason for verified billing downgrade (10–240 characters; no sensitive data):");
    if (!reason || reason.length < 10 || reason.length > 240) return;
    if (!confirm("Recheck exact Stripe subscription and, if non-active, set saved allowance to zero and block overages for " +
        tenant.tenant_id + "? This cannot restore subscriptions.")) return;
    const epoch = authEpoch;
    try {
      const result = await request("/v1/admin/subscriptions/" +
        encodeURIComponent(tenant.tenant_id) + "/downgrade", {
        method:"POST", body:JSON.stringify({expected_revision:tenant.revision,
          confirmation,reason,idempotency_key:crypto.randomUUID().replaceAll("-", "")})
      });
      const label = auditReceipt(result, tenant.tenant_id, tenant.revision);
      if (!["past_due","canceled","inactive"].includes(result.subscription_status) ||
          result.monthly_allowance !== 0 || result.overage !== "block")
        throw new Error("Billing downgrade response not verified; inspect before retry");
      const receipt = await request("/v1/admin/audit/" + encodeURIComponent(result.audit_event_id));
      if (!token || epoch !== authEpoch) return;
      const change = receipt.audit?.change;
      if (receipt.audit?.integrity_status !== "signed_verified" ||
          receipt.audit?.event_id !== result.audit_event_id ||
          receipt.audit?.operation !== "billing.downgrade" ||
          receipt.audit?.tenant_id !== tenant.tenant_id ||
          receipt.audit?.prior_revision !== tenant.revision ||
          receipt.audit?.new_revision !== result.revision ||
          change?.field !== "subscription_status" || change?.before !== "active" ||
          change?.after !== result.subscription_status ||
          change?.provider_status !== result.provider_status ||
          change?.allowance_before !== tenant.monthly_allowance ||
          change?.allowance_after !== 0)
        throw new Error("Billing outcome audit unverified; inspect before retry");
      nextCursor = null;
      await load();
      say("status","Verified unpaid downgrade saved · " + label);
    } catch(err) { if (token && epoch === authEpoch) say("status",err.message + " · inspect before retry"); }
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
            !["cognito.exact_user","stripe.subscription","stripe.invoices","audit.receipt","cognito.user_directory",
              "tenant.directory","tenant.detail","tenant.usage","catalog.registry","catalog.preview",
              "admin.overview","admin.features","catalog.draft","cognito.mutation_preflight"].includes(change.resource) ||
            receipt.prior_revision !== undefined || receipt.new_revision !== undefined)
          throw new Error("Protected read receipt invalid");
        say("audit-result", "Stored receipt · " + integrity + " · protected read " +
          change.resource + " · tenant " + (receipt.tenant_id || "unknown") +
          " · UTC " + (receipt.when_utc || "unknown") + readAuditLabel(response));
        return;
      }
      if (receipt.operation === "cognito.user.status.intent" ||
          receipt.operation === "cognito.user.status.outcome") {
        if (receipt.integrity_status !== "signed_verified" ||
            change.field !== "cognito_enabled" ||
            typeof change.before !== "boolean" || typeof change.after !== "boolean" ||
            change.before === change.after || !/^[a-f0-9]{64}$/.test(change.target_fingerprint || "") ||
            (receipt.operation.endsWith(".outcome") && !/^[a-f0-9]{64}$/.test(change.related_event_id || ""))) {
          throw new Error("Cognito user status receipt invalid");
        }
        say("audit-result", "Signed Cognito account " +
          (receipt.operation.endsWith(".outcome") ? "verified outcome" : "attempt (NOT outcome)") +
          " · enabled " + change.before + " → " + change.after +
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

  // Backend verification establishes the HMAC of the durable audit row.
  // A successful mutation response is not itself proof of signed persistence.
  async function verifyTenantMutationAudit(outcome, tenantId, priorRevision, expected) {
    if (!features.audit_receipt_lookup)
      throw new Error("Signed audit lookup unavailable; outcome may have applied. Inspect before retrying");
    const response = await request("/v1/admin/audit/" + encodeURIComponent(outcome.audit_event_id));
    const receipt = response?.audit, change = receipt?.change;
    if (!receipt || receipt.status !== "ok" ||
        receipt.integrity_status !== "signed_verified" ||
        receipt.event_id !== outcome.audit_event_id ||
        receipt.operation !== expected.operation ||
        receipt.tenant_id !== tenantId ||
        receipt.prior_revision !== priorRevision ||
        receipt.new_revision !== outcome.revision ||
        !change || Object.entries(expected.change).some(([key, value]) => change[key] !== value))
      throw new Error("Signed mutation audit unverified; outcome may have applied. Inspect before retrying");
  }
  async function capUsage(tenant) {
    if (!token || !features.monthly_cost_caps) return;
    if (!features.audit_receipt_lookup) {say("status","Signed audit lookup unavailable; no change attempted");return;}
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
      await verifyTenantMutationAudit(outcome, tenant.tenant_id, tenant.revision, {
        operation:"tenant.allowance_cap",
        change:{field:"monthly_allowance",before:tenant.monthly_allowance,after:limit}
      });
      if (token && epoch === authEpoch) {await load();say("status","Cost cap saved · " + receipt);}
    } catch(err) {if(token && epoch===authEpoch) say("status",err.message);}
  }
  async function revokeTenantKeys(tenant) {
    if (!token || !features.tenant_key_revocation || !Number.isSafeInteger(tenant.revision) ||
        !Number.isInteger(tenant.api_key_count) || tenant.api_key_count < 1) return;
    if (!features.audit_receipt_lookup) {say("status","Signed audit lookup unavailable; no change attempted");return;}
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
      await verifyTenantMutationAudit(outcome, tenant.tenant_id, tenant.revision, {
        operation:"tenant.keys_revoke",
        change:{field:"api_keys",before_count:tenant.api_key_count,after_count:0}
      });
      if (token && epoch===authEpoch) {await load();say("status","Application keys revoked · " + receipt);}
    } catch (err) {if (token && epoch===authEpoch) say("status",err.message);}
  }
  async function setTenantRule(ruleId) {
    if (!token || !features.tenant_rule_changes) return;
    if (!features.audit_receipt_lookup) {say("status","Signed audit lookup unavailable; no change attempted");return;}
    const tenantId=prompt("Tenant ID whose saved rule action should change:");
    if (!tenantId || !/^[a-z][a-z0-9-]{1,39}$/.test(tenantId)) return;
    const stage=prompt("Stage: input or output", "input");
    if (!(["input","output"].includes(stage))) return;
    const epoch=authEpoch;
    try {
      // Inspect the exact authoritative saved stage and override BEFORE asking
      // for a mutation. The inspection revision is the subsequent CAS token.
      const current=await request("/v1/admin/tenants/"+encodeURIComponent(tenantId)+
        "/rule-action?"+new URLSearchParams({stage,rule_id:ruleId}));
      if (!token || epoch!==authEpoch) return;
      if (current.tenant_id !== tenantId || current.rule_id !== ruleId ||
          current.stage !== stage ||
          !Number.isSafeInteger(current.revision) || current.revision < 1 ||
          !["block","flag","allow","redact",null].includes(current.check_action) ||
          !["same_as_check","off","allow","flag","block"].includes(current.rule_action) ||
          !["check_off","rule_off","configured_not_execution_evidence"].includes(current.eligibility) ||
          (current.check_action === null) !== (current.eligibility === "check_off") ||
          (current.check_action !== null && current.rule_action === "off") !==
            (current.eligibility === "rule_off"))
        throw new Error("Saved tenant rule inspection could not be verified; no mutation");
      const warning = current.eligibility === "check_off"
        ? " The check itself is OFF on this stage; an override cannot enable it."
        : current.eligibility === "rule_off"
        ? " This rule is currently OFF on this stage."
        : " Configured actions do not prove execution.";
      say("status", "Saved "+tenantId+" "+stage+" "+ruleId+": check "+
        (current.check_action || "OFF")+", override "+current.rule_action+"."+warning+
        readAuditLabel(current));
      const action=prompt("Action: off, allow, flag, block, or same_as_check. Saved override: "+
        current.rule_action+"; check action: "+(current.check_action || "OFF")+"."+warning,
        current.rule_action);
      if (!["off","allow","flag","block","same_as_check"].includes(action) ||
          action === current.rule_action) return;
      const confirmation=prompt("Type tenant ID exactly: " + tenantId);
      if (confirmation !== tenantId) return;
      const reason=prompt("Audit reason (10–240 characters; no sensitive data):");
      if (!reason || reason.length < 10 || reason.length > 240) return;
      if (!confirm("Change "+stage+" rule "+ruleId+" on "+tenantId+
        " from "+current.rule_action+" to "+action+"?"+warning)) return;
      if (!token || epoch!==authEpoch) return;
      const outcome=await request("/v1/admin/tenants/"+encodeURIComponent(tenantId)+"/rule-action", {
        method:"POST",body:JSON.stringify({expected_revision:current.revision,
          stage,rule_id:ruleId,action,confirmation,reason,
          idempotency_key:crypto.randomUUID().replaceAll("-", "")})
      });
      const receipt = auditReceipt(outcome, tenantId, current.revision);
      await verifyTenantMutationAudit(outcome, tenantId, current.revision, {
        operation:"tenant.rule_action",
        change:{field:"rule_action",rule_id:ruleId,stage,before:current.rule_action,after:action}
      });
      if (token && epoch===authEpoch) {await load();say("status","Tenant rule saved · " + receipt);}
    } catch(err) {if(token && epoch===authEpoch) say("status",err.message);}
  }
  async function changeStatus(tenant) {
    if (!token || !features.tenant_status_changes) return;
    if (!features.audit_receipt_lookup) {say("status","Signed audit lookup unavailable; no change attempted");return;}
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
      await verifyTenantMutationAudit(outcome, tenant.tenant_id, tenant.revision, {
        operation:"tenant.status",
        change:{field:"status",before:tenant.status,after:nextStatus}
      });
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
    if (enrollmentVerifying) return;
    const epoch = ++authEpoch;
    await cancelEnrollment(false);
    if (epoch !== authEpoch) return;
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
    clearEnrollment();
    token = result.access_token;
    clearAdminData();
    setLoggedIn(true);
    try { await load(); } catch(err) { say("status", err.message); }
  }
  async function beginNative() {
    if (enrollmentVerifying) return;
    const email = el("native-email").value.trim();
    const password = el("native-password").value;
    el("native-password").value = "";
    const epoch = ++authEpoch;
    await cancelEnrollment(false);
    if (epoch !== authEpoch) return;
    nativeTicket = null;
    el("native-challenge").hidden = true;
    try {
      const challenge = await request("/v1/admin/auth/native/start", {
        method:"POST",body:JSON.stringify({email,password})
      });
      if (epoch !== authEpoch) return;
      if (challenge.challenge !== "software_token_mfa" ||
          typeof challenge.challenge_ticket !== "string") throw new Error("Administrator sign-in rejected");
      nativeTicket = challenge.challenge_ticket;
      el("native-challenge").hidden = false;
      say("login-message", "Enter the code from your authenticator app");
    } catch(err) {if (epoch === authEpoch) say("login-message", err.message);}
  }
  async function finishNative() {
    const epoch = authEpoch;
    const ticket = nativeTicket;
    nativeTicket = null;
    const otp = el("native-code").value;
    el("native-code").value = "";
    el("native-challenge").hidden = true;
    if (!ticket) return;
    try {
      const result = await request("/v1/admin/auth/native/finish", {
        method:"POST",body:JSON.stringify({ticket,otp})
      });
      if (epoch !== authEpoch) return;
      if (typeof result.access_token !== "string" || !result.access_token)
        throw new Error("Administrator sign-in rejected");
      authEpoch += 1;
      clearEnrollment();
      token = result.access_token;
      el("native-email").value = "";
      clearAdminData();
      setLoggedIn(true);
      await load();
    } catch(err) {if (epoch === authEpoch || token) say(token ? "status" : "login-message", err.message);}
  }
  async function enrollmentRequest(action, payload) {
    const response = await fetch("/v1/admin/auth/enrollment/" + action, {
      method:"POST", headers:{"Accept":"application/json", "Content-Type":"application/json"},
      body:JSON.stringify(payload), cache:"no-store", credentials:"omit"
    });
    let value; try {value = await response.json();} catch {value = null;}
    if (!response.ok) {
      const retryCode = response.status === 400 && value?.code === "code_mismatch";
      const message = retryCode ? "That code was not accepted. Use the current code from your app, or cancel setup." :
        response.status === 401 || response.status === 403 ? "Setup was not authorized. Check your owner account and password, or sign in if you already have an authenticator." :
        "Setup could not be confirmed. Try administrator sign-in; if setup is incomplete, contact the operator before trying setup again.";
      const error = new Error(message); error.retryCode = retryCode; throw error;
    }
    return value;
  }
  function clearEnrollment() {
    const ticket = enrollmentTicket;
    enrollmentTicket = null;
    enrollmentEpoch += 1;
    enrollmentVerifying = false;
    clearTimeout(enrollmentTimer); enrollmentTimer = null;
    el("enrollment-key").value = "";
    el("enrollment-code").value = "";
    el("native-password").value = "";
    el("enrollment-setup").hidden = true;
    el("enrollment-copy").disabled = true;
    el("enrollment-finish").disabled = true;
    el("enrollment-cancel").disabled = false;
    for (const id of ["enrollment-start", "native-start", "login-start"]) el(id).disabled = false;
    say("enrollment-message", "");
    return ticket;
  }
  async function cancelEnrollment(announce = true) {
    const wasVerifying = enrollmentVerifying;
    const wasStarting = !el("enrollment-setup").hidden && !enrollmentTicket && !wasVerifying;
    const ticket = clearEnrollment();
    const epoch = enrollmentEpoch;
    if (announce) say("login-message", wasStarting ? "Setup cleared from this tab. Wait for the pending setup to finish or expire before starting again." :
      "Setup cleared from this tab. Sign in with your existing authenticator, or start first-time setup again.");
    if (ticket && !wasVerifying) {
      try {await enrollmentRequest("cancel", {ticket});}
      catch {if (announce && epoch === enrollmentEpoch) say("login-message", "Setup cleared from this tab. Cancellation could not be confirmed; wait for setup to expire before starting again.");}
    }
  }
  async function beginEnrollment() {
    if (enrollmentVerifying || !config?.native_enrollment_enabled) return;
    const email = el("native-email").value.trim();
    const password = el("native-password").value;
    const auth = ++authEpoch;
    await cancelEnrollment(false);
    if (auth !== authEpoch) return;
    nativeTicket = null;
    el("native-code").value = "";
    el("native-challenge").hidden = true;
    const epoch = enrollmentEpoch;
    el("enrollment-setup").hidden = false;
    for (const id of ["enrollment-start", "native-start", "login-start"]) el(id).disabled = true;
    say("enrollment-message", "Checking your approved owner account…");
    say("login-message", "");
    try {
      const result = await enrollmentRequest("start", {email,password});
      if (epoch !== enrollmentEpoch) {
        if (typeof result?.enrollment_ticket === "string")
          void enrollmentRequest("cancel", {ticket:result.enrollment_ticket}).catch(() => {});
        return;
      }
      if (!/^[A-Z2-7]{16,128}$/.test(result?.setup_key || "") ||
          typeof result?.enrollment_ticket !== "string" || !result.enrollment_ticket.startsWith("e1.") ||
          !Number.isInteger(result.expires_in) || result.expires_in <= 0 || result.expires_in > 180)
        throw new Error("Setup could not be confirmed. Contact the operator before trying again.");
      enrollmentTicket = result.enrollment_ticket;
      el("enrollment-key").value = result.setup_key;
      el("enrollment-copy").disabled = false;
      el("enrollment-finish").disabled = false;
      say("enrollment-message", "Copy the setup key to your authenticator app, then enter its current code. You are not signed in yet.");
      enrollmentTimer = setTimeout(() => {
        if (epoch !== enrollmentEpoch) return;
        const wasVerifying = enrollmentVerifying;
        clearEnrollment();
        say("login-message", wasVerifying ? "Setup timed out while saving. Try administrator sign-in; if setup is incomplete, contact the operator." :
          "Setup expired and the key was cleared. Enter your password to start again.");
      }, result.expires_in * 1000);
      el("enrollment-code").focus();
    } catch(err) {
      if (epoch === enrollmentEpoch) {clearEnrollment(); say("login-message", err.message);}
    }
  }
  async function finishEnrollment() {
    if (!enrollmentTicket || enrollmentVerifying) return;
    const code = el("enrollment-code").value;
    el("enrollment-code").value = "";
    if (!/^[0-9]{6}$/.test(code)) {say("enrollment-message", "Enter the six-digit code from your authenticator app.");return;}
    const ticket = enrollmentTicket, epoch = enrollmentEpoch;
    enrollmentVerifying = true;
    el("enrollment-finish").disabled = true;
    el("enrollment-cancel").disabled = true;
    el("enrollment-copy").disabled = true;
    say("enrollment-message", "Saving your authenticator…");
    try {
      const result = await enrollmentRequest("finish", {ticket,code});
      if (epoch !== enrollmentEpoch) return;
      if (result?.status !== "completed" || result.sign_in_required !== true)
        throw new Error("Setup could not be confirmed. Contact the operator before trying again.");
      clearEnrollment();
      say("login-message", "Authenticator saved. Enter your password again, choose Continue to authenticator, then sign in with a new code from your app.");
      el("native-password").focus();
    } catch(err) {
      if (epoch !== enrollmentEpoch) return;
      if (err.retryCode) {
        enrollmentVerifying = false;
        el("enrollment-finish").disabled = false;
        el("enrollment-cancel").disabled = false;
        el("enrollment-copy").disabled = false;
        say("enrollment-message", err.message);
      } else {clearEnrollment();say("login-message", err.message);}
    }
  }
  async function copyEnrollmentKey() {
    if (!enrollmentTicket || enrollmentVerifying) return;
    const epoch = enrollmentEpoch;
    try {
      await navigator.clipboard.writeText(el("enrollment-key").value);
      if (epoch === enrollmentEpoch) say("enrollment-message", "Setup key copied. Paste it into your authenticator app, then clear it from your clipboard.");
    } catch {
      if (epoch === enrollmentEpoch) say("enrollment-message", "Clipboard access was unavailable. Allow clipboard copying for this page and try Copy setup key again.");
    }
  }
  request("/v1/admin/auth/config").then(value => {
    config = value;
    if (value.native_mfa_enabled === true) el("native-auth").hidden = false;
    if (value.native_mfa_enabled === true && value.native_enrollment_enabled === true) el("enrollment-entry").hidden = false;
    if (value.native_mfa_enabled === true && value.native_enrollment_enabled !== true) el("enrollment-unavailable").hidden = false;
  }).catch(() => {});
  el("enrollment-start").addEventListener("click", beginEnrollment);
  el("enrollment-finish").addEventListener("click", finishEnrollment);
  el("enrollment-cancel").addEventListener("click", () => cancelEnrollment());
  el("enrollment-copy").addEventListener("click", copyEnrollmentKey);
  window.addEventListener("pagehide", () => clearEnrollment());
  el("native-start").addEventListener("click", beginNative);
  for (const button of document.querySelectorAll("[data-section]")) {
    button.addEventListener("click", () => showSection(button.dataset.section, true));
  }
  el("native-finish").addEventListener("click", finishNative);
  el("login-start").addEventListener("click", beginLogin);
  el("proposal-save").addEventListener("click", saveCatalogDraft);
  el("draft-inspect").addEventListener("click", inspectCatalogDraft);
  el("draft-review").addEventListener("click", reviewCatalogDraft);
  el("review-manifest").addEventListener("click", inspectReviewManifest);
  el("manifest-export").addEventListener("click", exportReviewedManifest);
  el("draft-id").addEventListener("input", invalidateCatalogExport);
  el("refresh").addEventListener("click", () => {invalidateCatalogExport();load().catch(e => say("status", e.message));});
  el("logout").addEventListener("click", () => signOut());
  el("search-go").addEventListener("click", () => {nextCursor=null;page();});
  el("user-search-go").addEventListener("click", () => {nextUserCursor=null;pageUsers();});
  el("user-next").addEventListener("click", pageUsers);
  el("user-inspect").addEventListener("click", inspectUser);
  el("user-change").addEventListener("click", changeUserStatus);
  el("user-action-inspect").addEventListener("click", inspectUserActionReceipt);
  el("next").addEventListener("click", page);
  el("audit-go").addEventListener("click", inspectAudit);
  el("proposal-go").addEventListener("click", previewCatalogRule);
  finishLogin().catch(err => say("login-message", err.message));
})();'''

