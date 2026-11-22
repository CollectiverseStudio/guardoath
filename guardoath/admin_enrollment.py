"""First authenticator enrollment for a live, verified GuardOath owner only.

This bootstrap capability is deliberately separate from administrative login.
It never returns a bearer token, creates a VerifiedAdmin or changes group
membership. It does not authorize factor replacement/reset: an explicit
operator-designated subject and absence of known active factors are required.
Cognito's live lists cannot prove that a legacy factor was never deactivated.
A new password + TOTP login must pass the unchanged signed-MFA admin verifier.

The browser receives an AES-GCM sealed, three-minute ticket. DynamoDB stores
only signed control metadata in the independent audit table. Full-document
CAS serializes association/verification across Lambda instances. Unknown
provider or persistence outcomes fail closed and need operator investigation.
"""
from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import secrets
from threading import BoundedSemaphore, Lock
from time import monotonic, time

from guardoath.admin_cognito import SUBJECT, _verified_owner_groups
from guardoath.admin_enrollment_state import (
    DynamoEnrollmentState, provider_code,
)

_EMAIL = re.compile(r"^[^@\s]{1,100}@[^@\s]{1,153}$")
_USERNAME = re.compile(r"^[A-Za-z0-9_.+@-]{1,128}$")
_TICKET = re.compile(r"^e1\.[A-Za-z0-9_-]{80,18000}$")
_PURPOSE = "guardoath-owner-first-totp-enrollment-ticket-v1"
_DENIED = {"NotAuthorizedException", "UserNotFoundException",
           "UserNotConfirmedException", "PasswordResetRequiredException"}
ENROLLMENT_SUBJECT = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class EnrollmentUnavailable(Exception):
    """No confirmation is possible; do not expose provider details or retry writes."""


class EnrollmentCodeMismatch(Exception):
    """Cognito definitively rejected the code; the original pending flow survives."""


@dataclass(frozen=True)
class _BootstrapOwner:
    subject: str
    username: str
    email: str
    auth_time: int


class CognitoNativeEnrollment:
    """Bounded enrollment only; never an administrative authorization service."""

    MAX_AGE = DynamoEnrollmentState.MAX_AGE

    def __init__(self, cognito, sessions, state: DynamoEnrollmentState, key: bytes, *,
                 enrollment_subject: str, wallclock=time, clock=monotonic):
        if (not isinstance(key, bytes) or len(key) < 32
                or not isinstance(enrollment_subject, str) or not ENROLLMENT_SUBJECT.fullmatch(enrollment_subject)
                or not callable(getattr(sessions, "verified_claims", None))
                or not getattr(sessions, "pool_id", None)
                or not _USERNAME.fullmatch(getattr(sessions, "client_id", ""))
                or not isinstance(state, DynamoEnrollmentState)
                or state.pool_id != sessions.pool_id or state.client_id != sessions.client_id
                or not all(callable(getattr(cognito, name, None)) for name in (
                    "initiate_auth", "get_user", "admin_get_user", "admin_list_groups_for_user",
                    "associate_software_token", "verify_software_token", "set_user_mfa_preference"))):
            raise ValueError("Bound admin verifier, provider and independent enrollment state required")
        # Import lazily: disabled native enrollment adds no runtime requirement.
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self._cognito, self._sessions, self._state = cognito, sessions, state
        self.pool_id, self.client_id = sessions.pool_id, sessions.client_id
        self.enrollment_subject = enrollment_subject
        self._aad = (_PURPOSE + "\0" + self.pool_id + "\0" + self.client_id).encode("utf-8")
        self._cipher = AESGCM(hmac.new(key, self._aad, hashlib.sha256).digest())
        self._wallclock, self._clock = wallclock, clock
        self._times = deque()
        self._lock, self._slots = Lock(), BoundedSemaphore(2)

    def _limited(self, callback):
        with self._lock:
            now = self._clock()
            while self._times and now - self._times[0] >= 60:
                self._times.popleft()
            # Per-warm-instance request bound; Cognito quotas/WAF remain the
            # external aggregate boundary. CAS also caps a flow at five codes.
            if len(self._times) >= 12 or not self._slots.acquire(blocking=False):
                raise EnrollmentUnavailable() from None
            self._times.append(now)
        try:
            return callback()
        except (EnrollmentUnavailable, EnrollmentCodeMismatch):
            raise
        except Exception:
            # In particular never chain SDK errors containing tokens or inputs.
            raise EnrollmentUnavailable() from None
        finally:
            self._slots.release()

    @staticmethod
    def _attributes(response: dict) -> dict | None:
        attrs = response.get("UserAttributes")
        if not isinstance(attrs, list) or len(attrs) > 100:
            return None
        found = {}
        for item in attrs:
            if (not isinstance(item, dict) or set(item) != {"Name", "Value"}
                    or not isinstance(item["Name"], str) or not isinstance(item["Value"], str)
                    or item["Name"] in found):
                return None
            found[item["Name"]] = item["Value"]
        return found

    @staticmethod
    def _factor_state(response: dict, mode: str) -> bool:
        # Missing lists are documented for an account with no enabled factors;
        # explicit null, strings, unknown factors and legacy SMS fields deny.
        factors = response.get("UserMFASettingList", [])
        preferred = response.get("PreferredMfaSetting", "")
        legacy = response.get("MFAOptions", [])
        if (not isinstance(factors, list) or legacy != [] or not isinstance(legacy, list)
                or not isinstance(preferred, str)):
            return False
        if mode == "none":
            return factors == [] and preferred == ""
        if mode == "associated":
            return factors in ([], ["SOFTWARE_TOKEN_MFA"]) and preferred in ("", "SOFTWARE_TOKEN_MFA")
        return factors == ["SOFTWARE_TOKEN_MFA"] and preferred == "SOFTWARE_TOKEN_MFA"

    def _signed_identity(self, token: str) -> tuple[str, str, int] | None:
        if not isinstance(token, str) or not 16 <= len(token) <= 8192:
            return None
        claims = self._sessions.verified_claims(token)
        if not isinstance(claims, dict):
            return None
        subject, username, when = (claims.get(k) for k in ("sub", "username", "auth_time"))
        if (not isinstance(subject, str) or not SUBJECT.fullmatch(subject)
                or subject != self.enrollment_subject
                or not isinstance(username, str) or not _USERNAME.fullmatch(username)
                or type(when) is not int or not -30 <= int(self._wallclock()) - when <= self.MAX_AGE):
            return None
        return subject, username, when

    def _live_owner(self, token: str, *, mode: str = "none",
                    expected: _BootstrapOwner | None = None) -> _BootstrapOwner | None:
        identity = self._signed_identity(token)
        if identity is None:
            return None
        subject, username, auth_time = identity
        try:
            current = self._cognito.get_user(AccessToken=token)
            admin = self._cognito.admin_get_user(UserPoolId=self.pool_id, Username=username)
            groups = self._cognito.admin_list_groups_for_user(
                UserPoolId=self.pool_id, Username=username, Limit=60)
        except Exception as exc:
            if provider_code(exc) in _DENIED:
                return None
            raise EnrollmentUnavailable() from None
        if (not isinstance(current, dict) or not isinstance(admin, dict)
                or current.get("Username") != username or admin.get("Username") != username
                or admin.get("Enabled") is not True or admin.get("UserStatus") != "CONFIRMED"
                or not isinstance(groups, dict) or "NextToken" in groups
                or not _verified_owner_groups(groups)
                or not all(self._factor_state(result, mode) for result in (current, admin))):
            return None
        first, second = self._attributes(current), self._attributes(admin)
        if (first is None or second is None or first.get("sub") != subject or second.get("sub") != subject
                or first.get("email_verified") != "true" or second.get("email_verified") != "true"
                or not isinstance(first.get("email"), str) or not _EMAIL.fullmatch(first["email"])
                or first["email"] != second.get("email")):
            return None
        owner = _BootstrapOwner(subject, username, first["email"], auth_time)
        return owner if expected is None or owner == expected else None

    @staticmethod
    def _b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    def _seal(self, owner: _BootstrapOwner, token: str, record: dict) -> str:
        payload = {"v": 1, "purpose": _PURPOSE, "pool": self.pool_id, "client": self.client_id,
                   "subject": owner.subject, "username": owner.username, "email": owner.email,
                   "auth_time": owner.auth_time, "token": token,
                   "issued": record["issued"], "expires": record["expires"], "nonce": record["nonce"]}
        iv = secrets.token_bytes(12)
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "e1." + self._b64(iv + self._cipher.encrypt(iv, raw, self._aad))

    def _open(self, ticket: str) -> dict | None:
        if not isinstance(ticket, str) or not _TICKET.fullmatch(ticket):
            return None
        try:
            encoded = ticket[3:]
            sealed = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            if self._b64(sealed) != encoded:
                return None
            value = json.loads(self._cipher.decrypt(sealed[:12], sealed[12:], self._aad))
            if (not isinstance(value, dict) or set(value) != {
                    "v", "purpose", "pool", "client", "subject", "username", "email", "auth_time",
                    "token", "issued", "expires", "nonce"}
                    or type(value["v"]) is not int or value["v"] != 1 or value["purpose"] != _PURPOSE
                    or value["pool"] != self.pool_id or value["client"] != self.client_id
                    or type(value["issued"]) is not int or type(value["expires"]) is not int
                    or value["expires"] != value["issued"] + self.MAX_AGE
                    or not -30 <= int(self._wallclock()) - value["issued"] <= self.MAX_AGE
                    or type(value["auth_time"]) is not int
                    or not isinstance(value["subject"], str) or not SUBJECT.fullmatch(value["subject"])
                    or not isinstance(value["username"], str) or not _USERNAME.fullmatch(value["username"])
                    or not isinstance(value["email"], str) or not _EMAIL.fullmatch(value["email"])
                    or not isinstance(value["token"], str) or not 16 <= len(value["token"]) <= 8192
                    or not isinstance(value["nonce"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["nonce"])):
                return None
            return value
        except Exception:
            # InvalidTag, invalid UTF-8/JSON and malformed input are indistinct.
            return None

    @staticmethod
    def _owner(value: dict) -> _BootstrapOwner:
        return _BootstrapOwner(value["subject"], value["username"], value["email"], value["auth_time"])

    def begin(self, email: str, password: str) -> dict | None:
        if (not isinstance(email, str) or not _EMAIL.fullmatch(email)
                or not isinstance(password, str) or not 12 <= len(password) <= 256):
            return None

        def begin_once():
            try:
                response = self._cognito.initiate_auth(
                    AuthFlow="USER_PASSWORD_AUTH", ClientId=self.client_id,
                    AuthParameters={"USERNAME": email, "PASSWORD": password})
            except Exception as exc:
                if provider_code(exc) in _DENIED:
                    return None
                raise EnrollmentUnavailable() from None
            if not isinstance(response, dict) or response.get("ChallengeName") or response.get("Session"):
                return None
            auth = response.get("AuthenticationResult")
            token = auth.get("AccessToken") if isinstance(auth, dict) else None
            owner = self._live_owner(token)
            if owner is None or owner.email.casefold() != email.casefold():
                return None
            record = self._state.reserve(owner.subject, secrets.token_hex(16), int(self._wallclock()))
            if record is None:
                return None
            # Recheck immediately before the first provider mutation. No stale
            # group/attribute cache or browser-supplied identity authorizes it.
            if self._live_owner(token, expected=owner) is None:
                self._state.block(record)
                return None
            if (int(self._wallclock()) > record["expires"]
                    or self._signed_identity(token) != (owner.subject, owner.username, owner.auth_time)):
                self._state.block(record)
                return None
            try:
                associated = self._cognito.associate_software_token(AccessToken=token)
            except Exception:
                # ASSOCIATING is deliberately non-expiring: the SDK response
                # might have been lost after provider acceptance.
                raise EnrollmentUnavailable() from None
            secret = associated.get("SecretCode") if isinstance(associated, dict) else None
            # AccessToken authorization can return SecretCode alone. Cognito
            # Sessions belong to its separate challenge-based enrollment flow;
            # do not require or invent one for this authenticated-token flow.
            if not isinstance(secret, str) or not re.fullmatch(r"[A-Z2-7]{16,128}", secret):
                raise EnrollmentUnavailable() from None
            ready = self._state.ready(record)
            if ready is None:
                raise EnrollmentUnavailable() from None
            if int(self._wallclock()) > ready["expires"]:
                return None
            return {"setup_key": secret, "enrollment_ticket": self._seal(owner, token, ready),
                    "expires_in": max(0, ready["expires"] - int(self._wallclock()))}

        return self._limited(begin_once)

    def finish(self, ticket: str, code: str) -> bool:
        value = self._open(ticket)
        if value is None or not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
            return False

        def finish_once():
            record = self._state.current(value["subject"], value["nonce"], value["issued"],
                                         int(self._wallclock()))
            if record is None or self._live_owner(value["token"], expected=self._owner(value)) is None:
                return False
            if self._open(ticket) is None:  # The live reads consumed part of the same deadline.
                return False
            verifying = self._state.verifying(record)
            if verifying is None:
                return False
            # A slow CAS cannot extend either the sealed-ticket deadline or
            # the signed password-login age. The claimed row remains blocked
            # if either deadline elapsed while persistence was in flight.
            if (self._open(ticket) is None or self._signed_identity(value["token"]) != (
                    value["subject"], value["username"], value["auth_time"])):
                raise EnrollmentUnavailable() from None
            try:
                result = self._cognito.verify_software_token(AccessToken=value["token"], UserCode=code)
            except Exception as exc:
                if provider_code(exc) == "CodeMismatchException":
                    retried = self._state.retry_code(verifying, int(self._wallclock()))
                    if retried is not None and self._state.read(value["subject"]) == retried:
                        raise EnrollmentCodeMismatch() from None
                # All other outcomes are ambiguous; never reopen VERIFYING.
                raise EnrollmentUnavailable() from None
            if not isinstance(result, dict) or result.get("Status") != "SUCCESS":
                raise EnrollmentUnavailable() from None
            # Verify may register the factor. Revalidate ownership independently
            # without requiring the account to still have zero factors.
            if self._live_owner(value["token"], mode="associated", expected=self._owner(value)) is None:
                raise EnrollmentUnavailable() from None
            if (self._open(ticket) is None or self._signed_identity(value["token"]) != (
                    value["subject"], value["username"], value["auth_time"])):
                raise EnrollmentUnavailable() from None
            try:
                result = self._cognito.set_user_mfa_preference(
                    AccessToken=value["token"],
                    SoftwareTokenMfaSettings={"Enabled": True, "PreferredMfa": True})
            except Exception:
                raise EnrollmentUnavailable() from None
            if (not isinstance(result, dict)
                    or self._live_owner(value["token"], mode="complete", expected=self._owner(value)) is None):
                raise EnrollmentUnavailable() from None
            complete = self._state.complete(verifying)
            if complete is None or self._state.read(value["subject"]) != complete:
                raise EnrollmentUnavailable() from None
            return True

        return self._limited(finish_once)

    def cancel(self, ticket: str) -> bool:
        value = self._open(ticket)
        if value is None:
            return False

        def cancel_once():
            record = self._state.current(value["subject"], value["nonce"], value["issued"],
                                         int(self._wallclock()))
            if record is None or self._live_owner(value["token"], expected=self._owner(value)) is None:
                return False
            cancelled = self._state.cancel(record)
            return cancelled is not None and self._state.read(value["subject"]) == cancelled

        return self._limited(cancel_once)
