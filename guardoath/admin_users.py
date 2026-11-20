"""Bounded Cognito owner directory; admin authorization happens before every call."""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import re
import time
from datetime import datetime, timezone

class InvalidUserCursor(ValueError):
    pass

_POOL = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-\d_[A-Za-z0-9]+$")
_FILTER = re.compile(r"^[A-Za-z0-9._+@-]{0,80}$")
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

    def _cursor(self, token, query, limit):
        payload = {"v": 1, "pool": self.pool_id, "q": query, "n": limit,
                   "token": token, "exp": int(self.clock()) + 900}
        data = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        signature = _b64(hmac.new(self.key, b"guardoath-user-directory:" + data.encode(), hashlib.sha256).digest())
        return data + "." + signature

    def _verify(self, value, query, limit):
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
        if (not isinstance(payload, dict) or payload.get("v") != 1 or payload.get("pool") != self.pool_id
                or payload.get("q") != query or type(payload.get("n")) is not int
                or payload["n"] != limit or type(payload.get("exp")) is not int
                or payload["exp"] <= self.clock() or not isinstance(payload.get("token"), str)
                or not 1 <= len(payload["token"]) <= 2048):
            raise InvalidUserCursor("Invalid user cursor")
        return payload["token"]

    def page(self, *, limit=20, query="", cursor=None):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Invalid user page size")
        if not isinstance(query, str) or not _FILTER.fullmatch(query):
            raise ValueError("Invalid user email filter")
        args = {"UserPoolId": self.pool_id, "Limit": limit}
        if query:
            args["Filter"] = 'email ^= "' + query + '"'
        if cursor is not None:
            args["PaginationToken"] = self._verify(cursor, query, limit)
        response = self.client.list_users(**args)
        if (not isinstance(response, dict) or not isinstance(response.get("Users"), list)
                or len(response["Users"]) > limit):
            raise ValueError("Invalid directory response")
        users = []
        for entry in response["Users"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("Username"), str):
                raise ValueError("Invalid directory user")
            attrs = entry.get("Attributes", [])
            if not isinstance(attrs, list) or len(attrs) > 50:
                raise ValueError("Invalid directory attributes")
            approved = {a.get("Name"): a.get("Value") for a in attrs
                        if isinstance(a, dict) and a.get("Name") in ("email", "email_verified")
                        and isinstance(a.get("Value"), str)}
            state = entry.get("UserStatus")
            users.append({"username": _mask_email(entry["Username"]) or entry["Username"][:128],
                          "email_hint": _mask_email(approved.get("email")),
                          "email_verified": approved.get("email_verified") == "true",
                          "enabled": entry.get("Enabled") if type(entry.get("Enabled")) is bool else None,
                          "status": state if state in _STATES else "UNKNOWN",
                          "created_utc": _utc(entry.get("UserCreateDate")),
                          "updated_utc": _utc(entry.get("UserLastModifiedDate"))})
        token = response.get("PaginationToken")
        if token is not None and (not isinstance(token, str) or not 1 <= len(token) <= 2048):
            raise ValueError("Invalid provider cursor")
        return {"status": "ok", "source": "Cognito ListUsers (single bounded page)",
                "retrieved_utc": _utc(datetime.now(timezone.utc)), "users": users,
                "returned": len(users), "total": None, "limit": limit,
                "next_cursor": self._cursor(token, query, limit) if token else None}
