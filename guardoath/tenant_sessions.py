"""Tenant sign-in: an Amazon Cognito user pool session decides who may change a tenant's settings.

An API key runs checks. Changing a customer tenant's checks, rule settings and API keys needs a signed-in
session instead, so a leaked production key cannot change them. Demo users have no accounts; their demo key
can still change their own demo record.

The browser signs in with the user pool's hosted sign-in page (authorization code with PKCE). The server
exchanges the code for tokens. Every session is checked in two steps:

1. Locally, with no network call: the token must be an RS256 JSON Web Token signed by one of this user
   pool's published keys (its JWKS), and its claims must name this pool, this app client, an access token,
   the aws.cognito.signin.user.admin scope and an expiry in the future. A forged or altered token stops here.
   The keys are fetched from the pool at most once every JWKS_REFRESH_SECONDS, even when tokens name keys
   that do not exist.
2. With Cognito (GetUser), for a token that passed step 1: this confirms that the user still exists and the
   token was not revoked, and gives the read-only attribute custom:tenant_id. A result is kept for at most
   CACHE_SECONDS (never past the token's expiry); a refusal is kept for DENIAL_SECONDS. Calls to Cognito are
   limited to UPSTREAM_PER_MINUTE a minute and UPSTREAM_AT_ONCE at a time; over that the request is refused
   with SignInBusy (HTTP 503), never let through.

Turned on with GUARDOATH_COGNITO_POOL_ID, GUARDOATH_COGNITO_CLIENT_ID, GUARDOATH_COGNITO_DOMAIN and
GUARDOATH_COGNITO_REDIRECT_URI (all four, or none). The region comes from the pool id, and the sign-in
domain must be a Cognito prefix domain in that region.
"""
from __future__ import annotations

import base64
from collections import deque
import hashlib
import hmac
import json
import os
import re
from threading import BoundedSemaphore, Lock
from time import monotonic, time
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request

from guardoath.tenant_record import TENANT_ID_PATTERN

SESSION_HEADER = "X-GuardOath-Session"
TENANT_ATTRIBUTE = "custom:tenant_id"
SCOPES = ("openid", "email", "aws.cognito.signin.user.admin")
SETTINGS = ("GUARDOATH_COGNITO_POOL_ID", "GUARDOATH_COGNITO_CLIENT_ID", "GUARDOATH_COGNITO_DOMAIN",
            "GUARDOATH_COGNITO_REDIRECT_URI")
POOL_ID = re.compile(r"^(?P<region>[a-z]{2}(?:-[a-z]+)+-\d)_[0-9A-Za-z]{1,55}$")
CLIENT_ID = re.compile(r"^[0-9a-z]{1,128}$")
PART = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_TOKEN = 8192
MIN_KEY_BITS = 2048
CACHE_SECONDS = 60.0
DENIAL_SECONDS = 60.0
JWKS_REFRESH_SECONDS = 300.0
UPSTREAM_PER_MINUTE = 60
UPSTREAM_AT_ONCE = 4
TIMEOUT_SECONDS = 3.0
MAX_RESPONSE_BYTES = 65_536
# DER prefix of a PKCS #1 v1.5 DigestInfo for SHA-256 (RFC 8017, section 9.2, note 1).
SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")

# post(url, headers, body) -> (status, body bytes) and get(url) -> (status, body bytes). Replaced in tests.
Post = Callable[[str, dict, bytes], tuple[int, bytes]]
Get = Callable[[str], tuple[int, bytes]]


class SignInBusy(Exception):
    """Too many sign-in checks need Cognito right now; the request is refused, not let through."""


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Redirects are not followed for sign-in", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirects())


def _send(request: urllib.request.Request) -> tuple[int, bytes]:
    if not request.full_url.startswith("https://"):
        raise ValueError("Only https is used for sign-in")
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as error:
        return error.code, error.read(MAX_RESPONSE_BYTES) if error.fp else b""


def https_post(url: str, headers: dict, body: bytes) -> tuple[int, bytes]:
    return _send(urllib.request.Request(url, data=body, method="POST", headers=headers))


def https_get(url: str) -> tuple[int, bytes]:
    return _send(urllib.request.Request(url, method="GET"))


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _b64_int(value) -> int | None:
    if not isinstance(value, str) or not PART.fullmatch(value):
        return None
    try:
        return int.from_bytes(_b64(value), "big")
    except ValueError:
        return None


def rs256_valid(signing_input: bytes, signature: bytes, modulus: int, exponent: int) -> bool:
    """RSASSA-PKCS1-v1_5 with SHA-256 (RFC 8017, 8.2.2): recompute the encoded message and compare it."""
    size = (modulus.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    value = int.from_bytes(signature, "big")
    if value >= modulus:
        return False
    digest = SHA256_DIGEST_INFO + hashlib.sha256(signing_input).digest()
    padding = size - 3 - len(digest)
    if padding < 8:
        return False
    expected = b"\x00\x01" + b"\xff" * padding + b"\x00" + digest
    return hmac.compare_digest(pow(value, exponent, modulus).to_bytes(size, "big"), expected)


def parse_jwks(raw: bytes) -> dict[str, tuple[int, int]]:
    """Signing keys by key id: RSA keys of at least MIN_KEY_BITS bits for RS256. Anything else is skipped."""
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return {}
    keys = data.get("keys") if isinstance(data, dict) else None
    found: dict[str, tuple[int, int]] = {}
    for key in keys if isinstance(keys, list) else []:
        if not isinstance(key, dict) or key.get("kty") != "RSA" or key.get("alg", "RS256") != "RS256" \
                or key.get("use", "sig") != "sig" or not isinstance(key.get("kid"), str):
            continue
        modulus, exponent = _b64_int(key.get("n")), _b64_int(key.get("e"))
        if modulus and exponent and modulus.bit_length() >= MIN_KEY_BITS and exponent >= 3 and exponent % 2:
            found[key["kid"]] = (modulus, exponent)
    return found


class CognitoSessions:
    def __init__(self, pool_id: str, client_id: str, domain: str, redirect_uri: str, post: Post = https_post,
                 get: Get = https_get, clock: Callable[[], float] = monotonic, wall: Callable[[], float] = time):
        match = POOL_ID.fullmatch(pool_id or "")
        if not match:
            raise ValueError("GUARDOATH_COGNITO_POOL_ID is not a user pool id")
        if not CLIENT_ID.fullmatch(client_id or ""):
            raise ValueError("GUARDOATH_COGNITO_CLIENT_ID is not an app client id")
        self.region = match["region"]
        prefix_domain = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.auth\." + re.escape(self.region) + r"\.amazoncognito\.com"
        if not re.fullmatch(prefix_domain, domain or ""):
            raise ValueError("GUARDOATH_COGNITO_DOMAIN must be the pool's sign-in domain: <prefix>.auth."
                             + self.region + ".amazoncognito.com")
        if not re.fullmatch(r"https://[a-z0-9.-]+(?::\d+)?/dashboard", redirect_uri or "") and \
                not re.fullmatch(r"http://(?:127\.0\.0\.1|localhost):\d+/dashboard", redirect_uri or ""):
            raise ValueError("GUARDOATH_COGNITO_REDIRECT_URI must be the dashboard address")
        self.pool_id, self.client_id, self.domain, self.redirect_uri = pool_id, client_id, domain, redirect_uri
        # Opt-in only: requires separately configured Cognito + Google and a
        # persistent tenant store. No account can be created by an API key.
        flag = os.getenv("GUARDOATH_SELF_REGISTRATION", "0")
        if flag not in {"0", "1"}:
            raise ValueError("GUARDOATH_SELF_REGISTRATION must be 0 or 1")
        self.registration_enabled = flag == "1"
        google = os.getenv("GUARDOATH_GOOGLE_LOGIN_ENABLED", "0")
        if google not in {"0", "1"}:
            raise ValueError("GUARDOATH_GOOGLE_LOGIN_ENABLED must be 0 or 1")
        self.google_enabled = google == "1"
        if self.registration_enabled and not self.google_enabled:
            raise ValueError("Public registration requires a configured Google login provider")
        self.issuer = f"https://cognito-idp.{self.region}.amazonaws.com/{pool_id}"
        self.jwks_url = self.issuer + "/.well-known/jwks.json"
        self._post, self._get, self._clock, self._wall = post, get, clock, wall
        self._keys: dict[str, tuple[int, int]] = {}
        self._keys_checked_at: float | None = None
        self._allowed: dict[str, tuple[float, str]] = {}
        self._denied: dict[str, float] = {}
        self._calls: deque[float] = deque()
        self._at_once = BoundedSemaphore(UPSTREAM_AT_ONCE)
        self._lock = Lock()

    @classmethod
    def from_environment(cls) -> "CognitoSessions | None":
        values = [os.getenv(name) for name in SETTINGS]
        if not any(values):
            return None
        if not all(values):
            raise ValueError("Tenant sign-in needs all of " + ", ".join(SETTINGS))
        return cls(*values)

    def public_config(self) -> dict:
        """What the dashboard needs to start a sign-in. Holds no secret: the app client has none."""
        return {"authorize_url": f"https://{self.domain}/oauth2/authorize",
                "logout_url": f"https://{self.domain}/logout", "client_id": self.client_id,
                "redirect_uri": self.redirect_uri, "scope": " ".join(SCOPES),
                "registration_enabled": self.registration_enabled,
                "google_provider": "Google" if self.google_enabled else None}

    # Step 1: local checks

    def _key(self, kid: str) -> tuple[int, int] | None:
        with self._lock:
            key = self._keys.get(kid)
            now = self._clock()
            if self._keys_checked_at is not None and now - self._keys_checked_at < JWKS_REFRESH_SECONDS:
                return key
            # Unknown key id, or keys older than JWKS_REFRESH_SECONDS: look again, at most once per
            # JWKS_REFRESH_SECONDS (a failed fetch counts too). A successful fetch replaces the keys, so a key the
            # pool has removed stops being accepted; a failed fetch keeps the last keys (Cognito GetUser still decides).
            self._keys_checked_at = now
        try:
            status, raw = self._get(self.jwks_url)
            fresh = parse_jwks(raw) if status == 200 else {}
        except (OSError, ValueError):
            fresh = {}
        with self._lock:
            if fresh:
                self._keys = fresh
            return self._keys.get(kid)

    def verified_claims(self, token: str) -> dict | None:
        """The token's claims if it is a current access token signed by this pool for this app client."""
        if not isinstance(token, str) or len(token) > MAX_TOKEN:
            return None
        parts = token.split(".")
        if len(parts) != 3 or not all(PART.fullmatch(part) for part in parts):
            return None
        try:
            header, payload, signature = json.loads(_b64(parts[0])), json.loads(_b64(parts[1])), _b64(parts[2])
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(header, dict) or header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) \
                or not isinstance(payload, dict):
            return None
        key = self._key(header["kid"])
        if key is None or not rs256_valid((parts[0] + "." + parts[1]).encode("ascii"), signature, *key):
            return None
        expires = payload.get("exp")
        if (payload.get("token_use") != "access" or payload.get("client_id") != self.client_id
                or payload.get("iss") != self.issuer or type(expires) is not int or expires <= self._wall()
                or "aws.cognito.signin.user.admin" not in str(payload.get("scope", "")).split()):
            return None
        return payload

    # Step 2: Cognito

    def _upstream(self):
        """A slot for one call to Cognito, or SignInBusy. Callers release it with self._at_once.release()."""
        with self._lock:
            now = self._clock()
            while self._calls and now - self._calls[0] >= 60.0:
                self._calls.popleft()
            if len(self._calls) >= UPSTREAM_PER_MINUTE:
                raise SignInBusy()
            if not self._at_once.acquire(blocking=False):
                raise SignInBusy()
            self._calls.append(now)

    def tenant_for(self, token: str) -> str | None:
        """The tenant a signed-in session belongs to, or None. Raises SignInBusy when Cognito may not be
        asked right now."""
        data = self.verified_claims(token)
        if data is None:
            return None
        key = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            now = self._clock()
            allowed = self._allowed.get(key)
            if allowed and now < allowed[0]:
                return allowed[1]
            denied = self._denied.get(key)
            if denied and now < denied:
                return None
        self._upstream()
        try:
            tenant_id = self._ask_cognito(token)
        finally:
            self._at_once.release()
        with self._lock:
            now = self._clock()
            for cache in (self._allowed, self._denied):
                if len(cache) > 1000:
                    cache.clear()
            if tenant_id is None:
                self._denied[key] = now + DENIAL_SECONDS
            else:
                self._allowed[key] = (now + min(CACHE_SECONDS, data["exp"] - self._wall()), tenant_id)
        return tenant_id

    def exchange(self, code: str, verifier: str) -> dict | None:
        """Trade a sign-in code (and its PKCE verifier) for an access token. None when Cognito refuses."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", code or "") or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier or ""):
            return None
        body = urllib.parse.urlencode({"grant_type": "authorization_code", "client_id": self.client_id, "code": code,
                                       "code_verifier": verifier, "redirect_uri": self.redirect_uri}).encode()
        self._upstream()
        try:
            status, raw = self._post(f"https://{self.domain}/oauth2/token",
                                     {"Content-Type": "application/x-www-form-urlencoded"}, body)
            data = json.loads(raw) if status == 200 else None
        except (OSError, ValueError):
            data = None
        finally:
            self._at_once.release()
        token = data.get("access_token") if isinstance(data, dict) else None
        expires = data.get("expires_in") if isinstance(data, dict) else None
        if not isinstance(token, str) or type(expires) is not int or self.tenant_for(token) is None:
            return None
        return {"access_token": token, "expires_in": expires}

    def _ask_cognito(self, token: str) -> str | None:
        # GetUser accepts only a valid, unexpired, unrevoked access token of a user who still exists.
        try:
            status, raw = self._post(f"https://cognito-idp.{self.region}.amazonaws.com/",
                                     {"Content-Type": "application/x-amz-json-1.1",
                                      "X-Amz-Target": "AWSCognitoIdentityProviderService.GetUser"},
                                     json.dumps({"AccessToken": token}).encode())
            data = json.loads(raw) if status == 200 else None
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("UserAttributes"), list):
            return None
        names = [a.get("Name") for a in data["UserAttributes"] if isinstance(a, dict)]
        # Duplicated security identity attributes must never become trusted by
        # last-write-wins dictionary construction.
        if any(names.count(name) > 1 for name in (TENANT_ATTRIBUTE, "sub", "email_verified", "identities")):
            return None
        attributes = {a.get("Name"): a.get("Value") for a in data["UserAttributes"]
                      if isinstance(a, dict) and isinstance(a.get("Name"), str)}
        tenant_id = attributes.get(TENANT_ATTRIBUTE)
        if isinstance(tenant_id, str) and re.fullmatch(TENANT_ID_PATTERN, tenant_id):
            return tenant_id
        # A first-time Google/Cognito account does not have custom:tenant_id.
        # Derive a stable, isolated tenant identifier ONLY from the verified
        # Cognito subject and issuer. Never use email or browser-supplied IDs:
        # addresses can change and users may have identical email aliases.
        # The application must explicitly create this tenant at /v1/register.
        # Until then normal /v1/applications/current remains unauthorized.
        if not self.registration_enabled or tenant_id is not None:
            return None
        subject = attributes.get("sub")
        # Do not let a native Cognito user who merely has a verified email
        # self-register through a Google-only customer enrollment flow.
        # Cognito GetUser includes the provider identities for federated users;
        # unlinked Google profiles also carry its stable Google_ username.
        raw_identities = attributes.get("identities")
        if not isinstance(raw_identities, str) or len(raw_identities) > 4096:
            return None
        try:
            identities = json.loads(raw_identities)
        except (ValueError, RecursionError):
            return None
        google = (isinstance(identities, list) and 1 <= len(identities) <= 5 and
                  any(isinstance(item, dict) and
                      item.get("providerName") == "Google" and
                      item.get("providerType") == "Google" and
                      isinstance(item.get("userId"), str) and
                      1 <= len(item["userId"]) <= 256
                      for item in identities))
        username = data.get("Username")
        if (not google or not isinstance(username, str) or
                not username.casefold().startswith("google_") or
                attributes.get("email_verified") != "true" or
                not isinstance(subject, str) or
                subject != self.verified_claims(token).get("sub")):
            return None
        return "usr-" + hashlib.sha256((self.issuer + "\\0" + subject).encode()).hexdigest()[:28]
