"""Server-bounded, software-token MFA administrator authentication.

Separate Cognito public admin client, USER_PASSWORD_AUTH followed by a real
SOFTWARE_TOKEN_MFA challenge. Requires the existing signed access-token AMR,
auth_time and live owner-group verifier AFTER Cognito finishes MFA.
Disabled unless explicitly wired by admin runtime. No refresh tokens or
passwords are stored. The short-lived HMAC ticket contains an opaque Cognito
challenge session; keep it in transient JS memory, never logs/storage.
"""
from __future__ import annotations

import base64
from collections import deque
from hashlib import sha256
import hmac
import json
import re
import secrets
from threading import BoundedSemaphore, Lock
from time import monotonic, time


_EMAIL = re.compile(r"^[^@\s]{1,100}@[^@\s]{1,153}$")
_USERNAME = re.compile(r"^[A-Za-z0-9_.+@-]{1,128}$")
_CODE = re.compile(r"^[0-9]{6}$")
_TICKET = re.compile(r"^[A-Za-z0-9_-]{40,9000}\.[A-Za-z0-9_-]{43}$")
_MAX_AGE = 180


class NativeMfaUnavailable(Exception):
    """Rate limit, capacity or Cognito availability failure; never allow access."""


class CognitoNativeMfa:
    """Short-lived stateless MFA challenge binding, keyed independently of tokens."""

    def __init__(self, cognito, client_id: str, key: bytes, *,
                 wallclock=time, clock=monotonic):
        if not _USERNAME.fullmatch(client_id) or not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("Independent admin client and signing key required")
        if not all(callable(getattr(cognito, m, None)) for m in
                   ("initiate_auth", "respond_to_auth_challenge")):
            raise ValueError("Cognito native challenge APIs required")
        self._cognito = cognito
        self._client = client_id
        self._key = hmac.new(key, b"guardoath-admin-native-mfa-v1", sha256).digest()
        self._wallclock = wallclock
        self._clock = clock
        self._lock = Lock()
        self._requests = deque()
        self._slots = BoundedSemaphore(2)

    @property
    def client_id(self) -> str:
        """The public admin OAuth client to which all challenges are bound."""
        return self._client

    def _call(self, callback):
        with self._lock:
            now = self._clock()
            while self._requests and now - self._requests[0] >= 60:
                self._requests.popleft()
            # Per-warm-instance bound; WAF and Cognito quotas remain required.
            if len(self._requests) >= 12 or not self._slots.acquire(blocking=False):
                raise NativeMfaUnavailable()
            self._requests.append(now)
        try:
            return callback()
        finally:
            self._slots.release()

    @staticmethod
    def _b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _unb64(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def _ticket(self, username: str, session: str) -> str:
        payload = json.dumps({
            "v": 1, "client": self._client, "username": username,
            "session": session, "time": int(self._wallclock()),
            "nonce": secrets.token_hex(16),
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self._key, payload, sha256).digest()
        return self._b64(payload) + "." + self._b64(signature)

    def begin(self, email: str, password: str) -> str | None:
        if (not isinstance(email, str) or not _EMAIL.fullmatch(email)
                or not isinstance(password, str) or not 12 <= len(password) <= 256):
            return None
        try:
            response = self._call(lambda: self._cognito.initiate_auth(
                AuthFlow="USER_PASSWORD_AUTH", ClientId=self._client,
                AuthParameters={"USERNAME": email, "PASSWORD": password},
            ))
        except NativeMfaUnavailable:
            raise
        except Exception as exc:
            # Denied credentials and availability errors have distinct generic
            # outcomes, but never expose provider messages or account existence.
            code = getattr(exc, "response", {}).get("Error", {}).get("Code") if isinstance(
                getattr(exc, "response", None), dict) else None
            if code in {"NotAuthorizedException", "UserNotFoundException",
                        "UserNotConfirmedException", "PasswordResetRequiredException"}:
                return None
            raise NativeMfaUnavailable() from exc
        if not isinstance(response, dict) or response.get("ChallengeName") != "SOFTWARE_TOKEN_MFA":
            # In particular, a password-only AuthenticationResult is NEVER enough.
            return None
        session = response.get("Session")
        params = response.get("ChallengeParameters")
        username = (params.get("USERNAME", email) if isinstance(params, dict) else email)
        if (not isinstance(session, str) or not 20 <= len(session) <= 4096
                or not isinstance(username, str) or not _USERNAME.fullmatch(username)):
            return None
        return self._ticket(username, session)

    def _verify_ticket(self, ticket: str) -> dict | None:
        if not isinstance(ticket, str) or not _TICKET.fullmatch(ticket):
            return None
        encoded, signature = ticket.split(".", 1)
        try:
            raw = self._unb64(encoded)
            expected = hmac.new(self._key, raw, sha256).digest()
            if (self._b64(raw) != encoded or self._b64(expected) != signature
                    or not hmac.compare_digest(self._unb64(signature), expected)):
                return None
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != {
                    "v", "client", "username", "session", "time", "nonce"}:
                return None
            if payload["v"] != 1 or payload["client"] != self._client:
                return None
            issued = payload["time"]
            age = int(self._wallclock()) - issued if type(issued) is int else 999999
            if not -30 <= age <= _MAX_AGE:
                return None
            if (not isinstance(payload["username"], str)
                    or not _USERNAME.fullmatch(payload["username"])
                    or not isinstance(payload["session"], str)
                    or not 20 <= len(payload["session"]) <= 4096
                    or not isinstance(payload["nonce"], str)
                    or not re.fullmatch(r"[a-f0-9]{32}", payload["nonce"])):
                return None
            return payload
        except (ValueError, TypeError, UnicodeError, KeyError, OverflowError):
            return None

    def finish(self, ticket: str, code: str) -> str | None:
        if not isinstance(code, str) or not _CODE.fullmatch(code):
            return None
        payload = self._verify_ticket(ticket)
        if payload is None:
            return None
        try:
            response = self._call(lambda: self._cognito.respond_to_auth_challenge(
                ClientId=self._client, ChallengeName="SOFTWARE_TOKEN_MFA",
                Session=payload["session"], ChallengeResponses={
                    "USERNAME": payload["username"], "SOFTWARE_TOKEN_MFA_CODE": code,
                },
            ))
        except NativeMfaUnavailable:
            raise
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code") if isinstance(
                getattr(exc, "response", None), dict) else None
            if code in {"NotAuthorizedException", "CodeMismatchException",
                        "ExpiredCodeException", "InvalidParameterException"}:
                return None
            raise NativeMfaUnavailable() from exc
        if not isinstance(response, dict) or response.get("ChallengeName"):
            return None
        auth = response.get("AuthenticationResult")
        token = auth.get("AccessToken") if isinstance(auth, dict) else None
        # OAuth refresh and ID tokens are intentionally never returned.
        return token if isinstance(token, str) and 16 <= len(token) <= 8192 else None
