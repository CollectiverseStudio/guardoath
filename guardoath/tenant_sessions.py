"""Tenant sign-in: an Amazon Cognito user pool session decides who may change a tenant's settings.

An API key runs checks. Changing a customer tenant's checks, rule settings and API keys needs a signed-in
session instead, so a leaked production key cannot change them. Demo users have no accounts; their demo key
can still change their own demo record.

The browser signs in with the user pool's hosted sign-in page (authorization code with PKCE). The server
exchanges the code for tokens and checks every session by asking Cognito (GetUser), so no token-signing
library is needed. A token is accepted only when it is an access token from this user pool and app client,
has not expired, and its user carries the read-only attribute custom:tenant_id.

Turned on with GUARDOATH_COGNITO_POOL_ID, GUARDOATH_COGNITO_CLIENT_ID, GUARDOATH_COGNITO_DOMAIN and
GUARDOATH_COGNITO_REDIRECT_URI (all four, or none). The region comes from the pool id.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from threading import Lock
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
DOMAIN = re.compile(r"^[a-z0-9.-]{1,253}$")
TOKEN = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
MAX_TOKEN = 8192
CACHE_SECONDS = 60.0
TIMEOUT_SECONDS = 3.0

# post(url, headers, body) -> (status, body bytes). Replaced in tests; never logs the token.
Post = Callable[[str, dict, bytes], tuple[int, bytes]]


def https_post(url: str, headers: dict, body: bytes) -> tuple[int, bytes]:
    if not url.startswith("https://"):
        raise ValueError("Only https is used for sign-in")
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read(65_536)
    except urllib.error.HTTPError as error:
        return error.code, error.read(65_536)


def claims(token: str) -> dict | None:
    """The token's payload, unverified. Used only after Cognito has accepted the same token."""
    if not isinstance(token, str) or len(token) > MAX_TOKEN or not TOKEN.fullmatch(token):
        return None
    part = token.split(".")[1]
    try:
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


class CognitoSessions:
    def __init__(self, pool_id: str, client_id: str, domain: str, redirect_uri: str, post: Post = https_post,
                 clock: Callable[[], float] = monotonic, wall: Callable[[], float] = time):
        match = POOL_ID.fullmatch(pool_id or "")
        if not match:
            raise ValueError("GUARDOATH_COGNITO_POOL_ID is not a user pool id")
        if not CLIENT_ID.fullmatch(client_id or ""):
            raise ValueError("GUARDOATH_COGNITO_CLIENT_ID is not an app client id")
        if not DOMAIN.fullmatch(domain or ""):
            raise ValueError("GUARDOATH_COGNITO_DOMAIN must be a host name, such as name.auth.us-east-1.amazoncognito.com")
        if not re.fullmatch(r"https://[a-z0-9.-]+(?::\d+)?/dashboard", redirect_uri or "") and \
                not re.fullmatch(r"http://(?:127\.0\.0\.1|localhost):\d+/dashboard", redirect_uri or ""):
            raise ValueError("GUARDOATH_COGNITO_REDIRECT_URI must be the dashboard address")
        self.region = match["region"]
        self.pool_id, self.client_id, self.domain, self.redirect_uri = pool_id, client_id, domain, redirect_uri
        self.issuer = f"https://cognito-idp.{self.region}.amazonaws.com/{pool_id}"
        self._post, self._clock, self._wall = post, clock, wall
        self._cache: dict[str, tuple[float, str]] = {}
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
                "redirect_uri": self.redirect_uri, "scope": " ".join(SCOPES)}

    def exchange(self, code: str, verifier: str) -> dict | None:
        """Trade a sign-in code (and its PKCE verifier) for an access token. None when Cognito refuses."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", code or "") or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier or ""):
            return None
        body = urllib.parse.urlencode({"grant_type": "authorization_code", "client_id": self.client_id, "code": code,
                                       "code_verifier": verifier, "redirect_uri": self.redirect_uri}).encode()
        try:
            status, raw = self._post(f"https://{self.domain}/oauth2/token",
                                     {"Content-Type": "application/x-www-form-urlencoded"}, body)
            data = json.loads(raw) if status == 200 else None
        except (OSError, ValueError):
            return None
        token = data.get("access_token") if isinstance(data, dict) else None
        expires = data.get("expires_in") if isinstance(data, dict) else None
        if not isinstance(token, str) or type(expires) is not int or self.tenant_for(token) is None:
            return None
        return {"access_token": token, "expires_in": expires}

    def tenant_for(self, token: str) -> str | None:
        """The tenant a signed-in session belongs to, or None. Checked with Cognito at most once a minute
        per token, and never past the token's own expiry."""
        data = claims(token)
        now = self._wall()
        if (data is None or data.get("token_use") != "access" or data.get("client_id") != self.client_id
                or data.get("iss") != self.issuer or type(data.get("exp")) is not int or data["exp"] <= now
                or "aws.cognito.signin.user.admin" not in str(data.get("scope", "")).split()):
            return None
        key = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            cached = self._cache.get(key)
            if cached and self._clock() < cached[0]:
                return cached[1]
        tenant_id = self._ask_cognito(token)
        if tenant_id is not None:
            with self._lock:
                if len(self._cache) > 1000:
                    self._cache.clear()
                self._cache[key] = (self._clock() + min(CACHE_SECONDS, data["exp"] - now), tenant_id)
        return tenant_id

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
        values = [a.get("Value") for a in data["UserAttributes"] if isinstance(a, dict) and a.get("Name") == TENANT_ATTRIBUTE]
        if len(values) != 1 or not isinstance(values[0], str) or not re.fullmatch(TENANT_ID_PATTERN, values[0]):
            return None
        return values[0]
