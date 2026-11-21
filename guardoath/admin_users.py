"""Bounded Cognito owner directory; admin authorization happens before every call."""
from __future__ import annotations
import base64
from collections import deque
from threading import Lock
import math
import hashlib
import hmac
import json
import re
import time
from datetime import datetime, timezone

class InvalidUserCursor(ValueError):
    pass

class InvalidUserLookup(ValueError):
    pass

class CognitoDirectoryUnavailable(Exception):
    pass

class CognitoLookupRateLimited(CognitoDirectoryUnavailable):
    pass

# Shared, server-trusted owner group name; never read from browser input.
ADMIN_GROUP = "GuardOathOwners"

_POOL = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-\d_[A-Za-z0-9]+$")
_FILTER = re.compile(r"^[A-Za-z0-9._+@-]{0,80}$")
# Deliberately restricted ASCII email subset: never interpolate quotes or escapes into Cognito filters.
_EXACT_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$")
_OWNER_SUB = re.compile(r"^[0-9a-fA-F-]{20,80}$")
_STATES = {"UNCONFIRMED", "CONFIRMED", "ARCHIVED", "COMPROMISED", "UNKNOWN",
           "RESET_REQUIRED", "FORCE_CHANGE_PASSWORD", "EXTERNAL_PROVIDER"}

def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

def _unb64(raw):
    try:
        return base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
    except (TypeError, ValueError) as exc:
        raise InvalidUserCursor("Invalid user cursor") from exc

def _utc(value):
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return None

def _mask_email(value):
    if not isinstance(value, str) or not 3 <= len(value) <= 254 or value.count("@") != 1:
        return None
    local, domain = value.rsplit("@", 1)
    if not local or not domain or any(x.isspace() for x in value):
        return None
    return local[0] + "***@" + domain.lower()

class CognitoUserDirectory:
    """Exactly one bounded ListUsers API call per owner-authorized request."""
    def __init__(self, client, pool_id, key, clock=time.time):
        if (not callable(getattr(client, "list_users", None)) or not isinstance(pool_id, str)
                or not _POOL.fullmatch(pool_id) or not isinstance(key, bytes)
                or len(key) < 32 or not callable(clock)):
            raise ValueError("Cognito user directory requires trusted configuration")
        self.client, self.pool_id, self.key, self.clock = client, pool_id, key, clock
        # Best-effort per-instance provider-call cap; no replacement for a durable distributed limiter.
        self._lookup_lock = Lock()
        self._lookup_times = {}

    def _cursor_binding(self, kind: str, value: str) -> str:
        # HMAC signatures prevent modification but do not hide base64 payload
        # fields. Pseudonymize owner and email prefix independently to prevent
        # their disclosure in browser-visible continuation cursors.
        message = (b"guardoath-user-directory-cursor-v3:" + kind.encode("ascii")
                   + b":" + value.encode("utf-8"))
        return _b64(hmac.new(self.key, message, hashlib.sha256).digest())

    def _cursor(self, token, query, limit, owner_sub):
        payload = {"v": 3, "pool": self.pool_id,
                   "owner": self._cursor_binding("owner", owner_sub),
                   "q": self._cursor_binding("filter", query), "n": limit,
                   "token": token, "exp": int(self.clock()) + 900}
        data = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        signature = _b64(hmac.new(self.key, b"guardoath-user-directory:" + data.encode(), hashlib.sha256).digest())
        return data + "." + signature

    def _verify(self, value, query, limit, owner_sub):
        if not isinstance(value, str) or len(value) > 4096 or not value.isascii() or value.count(".") != 1:
            raise InvalidUserCursor("Invalid user cursor")
        data, supplied = value.split(".")
        expected = _b64(hmac.new(self.key, b"guardoath-user-directory:" + data.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied, expected):
            raise InvalidUserCursor("Invalid user cursor")
        try:
            payload = json.loads(_unb64(data))
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidUserCursor("Invalid user cursor") from exc
        if (not isinstance(payload, dict) or payload.get("v") != 3 or payload.get("pool") != self.pool_id
                or payload.get("owner") != self._cursor_binding("owner", owner_sub)
                or payload.get("q") != self._cursor_binding("filter", query)
                or type(payload.get("n")) is not int
                or payload["n"] != limit or type(payload.get("exp")) is not int
                or payload["exp"] <= self.clock() or not isinstance(payload.get("token"), str)
                or not 1 <= len(payload["token"]) <= 2048):
            raise InvalidUserCursor("Invalid user cursor")
        return payload["token"]

    def _project_user(self, entry):
        if (not isinstance(entry, dict) or not isinstance(entry.get("Username"), str)
                or not 1 <= len(entry["Username"]) <= 256):
            raise ValueError("Invalid directory user")
        attrs = entry.get("Attributes", [])
        if not isinstance(attrs, list) or len(attrs) > 50:
            raise ValueError("Invalid directory attributes")
        approved = {}
        for attribute in attrs:
            if not isinstance(attribute, dict):
                raise ValueError("Malformed Cognito directory attribute")
            if attribute.get("Name") not in ("email", "email_verified"):
                continue
            name, value = attribute["Name"], attribute.get("Value")
            # Do not turn conflicting or malformed provider identity attributes
            # into an apparently authoritative masked account-state summary.
            if name in approved or not isinstance(value, str):
                raise ValueError("Ambiguous Cognito directory identity")
            approved[name] = value
        state = entry.get("UserStatus")
        if state is not None and not isinstance(state, str):
            raise ValueError("Malformed Cognito directory status")
        return {"username": _mask_email(entry["Username"]) or ("user-" + hmac.new(
                          self.key, b"guardoath-cognito-user-label:" + entry["Username"].encode("utf-8"),
                          hashlib.sha256).hexdigest()[:16]),
                      "email_hint": _mask_email(approved.get("email")),
                      "email_verified": (True if approved.get("email_verified") == "true"
                                         else False if approved.get("email_verified") == "false"
                                         else None),
                      "enabled": entry.get("Enabled") if type(entry.get("Enabled")) is bool else None,
                      "status": state if state in _STATES else "UNKNOWN",
                      "created_utc": _utc(entry.get("UserCreateDate")),
                      "updated_utc": _utc(entry.get("UserLastModifiedDate"))}

    def page(self, *, owner_sub: str, limit=20, query="", cursor=None):
        if not isinstance(owner_sub, str) or not _OWNER_SUB.fullmatch(owner_sub):
            raise ValueError("Verified owner subject required for user directory")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Invalid user page size")
        if not isinstance(query, str) or not _FILTER.fullmatch(query):
            raise ValueError("Invalid user email filter")
        args = {"UserPoolId": self.pool_id, "Limit": limit}
        if query:
            args["Filter"] = 'email ^= "' + query + '"'
        if cursor is not None:
            args["PaginationToken"] = self._verify(cursor, query, limit, owner_sub)
        # Share the same per-owner Cognito call budget with exact-email inspection.
        # Check the signed cursor and all filters first: rejected input consumes no provider quota.
        self._reserve_provider_read(owner_sub)
        try:
            response = self.client.list_users(**args)
        except Exception as exc:
            raise CognitoDirectoryUnavailable("Cognito directory provider unavailable") from exc
        # A provider failure or corrupted Cognito response is not an invalid
        # owner query (422). Fail closed as provider-unavailable (503) and do
        # not return an incomplete or misleading account directory.
        try:
            if (not isinstance(response, dict) or not isinstance(response.get("Users"), list)
                    or len(response["Users"]) > limit):
                raise ValueError("Invalid directory response")
            users = [self._project_user(entry) for entry in response["Users"]]
            token = response.get("PaginationToken")
            if token is not None and (not isinstance(token, str) or not 1 <= len(token) <= 2048):
                raise ValueError("Invalid provider cursor")
        except (TypeError, ValueError, AttributeError) as exc:
            raise CognitoDirectoryUnavailable("Invalid Cognito directory response") from exc
        return {"status": "ok", "source": "Cognito ListUsers (single bounded page)",
                "retrieved_utc": _utc(datetime.now(timezone.utc)), "users": users,
                "returned": len(users), "total": None, "limit": limit,
                "next_cursor": self._cursor(token, query, limit, owner_sub) if token else None}

    def _reserve_provider_read(self, owner_sub: str):
        # Bound BOTH directory pages and exact lookups before any provider read.
        # A Lambda instance maintains this window across warm invocations only.
        try:
            now = float(self.clock())
        except (TypeError, ValueError, OverflowError) as exc:
            raise CognitoDirectoryUnavailable("Directory rate window unavailable") from exc
        if not math.isfinite(now):
            raise CognitoDirectoryUnavailable("Directory rate window unavailable")
        with self._lookup_lock:
            if owner_sub not in self._lookup_times and len(self._lookup_times) >= 128:
                self._lookup_times = {owner: times for owner, times in self._lookup_times.items()
                                      if times and times[-1] > now - 60}
                if len(self._lookup_times) >= 128:
                    raise CognitoLookupRateLimited("Directory lookup temporarily limited")
            times = self._lookup_times.setdefault(owner_sub, deque())
            while times and times[0] <= now - 60:
                times.popleft()
            if len(times) >= 20:
                raise CognitoLookupRateLimited("Directory lookup temporarily limited")
            times.append(now)

    def lookup(self, *, owner_sub: str, email: str):
        """One exact-match Cognito identity inspection, with no raw subject/email returned.

        This is a read-only inspection; it never modifies Cognito or grants entitlement.
        The trusted owner identity is injected by the authenticated API, not the caller.
        """
        if not isinstance(owner_sub, str) or not _OWNER_SUB.fullmatch(owner_sub):
            raise InvalidUserLookup("Verified owner subject required")
        if (not isinstance(email, str) or len(email) > 254 or
                not _EXACT_EMAIL.fullmatch(email) or email.startswith(".") or
                ".." in email or email.endswith(".")):
            raise InvalidUserLookup("Invalid exact email")
        self._reserve_provider_read(owner_sub)
        try:
            response = self.client.list_users(
                UserPoolId=self.pool_id, Filter='email = "' + email + '"', Limit=2)
        except Exception as exc:
            raise CognitoDirectoryUnavailable("Cognito identity lookup unavailable") from exc
        if (not isinstance(response, dict) or not isinstance(response.get("Users"), list)
                or len(response["Users"]) > 2 or response.get("PaginationToken") is not None):
            raise CognitoDirectoryUnavailable("Invalid Cognito lookup response")
        entries = response["Users"]
        result = {"source": "Cognito ListUsers exact-email lookup (one bounded read)",
                  "retrieved_utc": _utc(datetime.now(timezone.utc))}
        if not entries:
            return {**result, "status": "not_found", "user": None}
        if len(entries) != 1:
            raise CognitoDirectoryUnavailable("Ambiguous Cognito lookup")
        entry = entries[0]
        try:
            attrs = entry.get("Attributes")
            if not isinstance(attrs, list) or len(attrs) > 50:
                raise ValueError("Invalid attributes")
            candidates = [attr.get("Value") for attr in attrs
                          if isinstance(attr, dict) and attr.get("Name") == "email"]
            if len(candidates) != 1 or not isinstance(candidates[0], str) or (
                    candidates[0].casefold() != email.casefold()):
                raise ValueError("Cognito exact match unverified")
            safe = self._project_user(entry)
        except (ValueError, TypeError, AttributeError, CognitoDirectoryUnavailable) as exc:
            raise CognitoDirectoryUnavailable("Invalid Cognito lookup identity") from exc
        return {**result, "status": "ok", "user": safe}
