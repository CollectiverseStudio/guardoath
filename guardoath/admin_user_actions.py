"""Fail-closed owner Cognito sign-in suspension/reactivation with immutable audit evidence.

A DynamoDB attempt is durable before the Cognito mutation. A separate signed
outcome is written only after the target's enabled state is independently
verified. Ambiguous provider/audit failures never return success; a repeated
idempotency key cannot issue a second provider write. Cognito does not support
cross-service transactions, so the attempt receipt remains the recovery trail.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import re

from guardoath.admin_audit_integrity import seal_audit
from guardoath.admin_users import ADMIN_GROUP, _EXACT_EMAIL, _OWNER_SUB
from guardoath.admin_actions import IDEMPOTENCY, REASON


class UserActionConflict(Exception):
    pass


class UserActionUnavailable(Exception):
    pass


class AdminCognitoUserActions:
    def __init__(self, cognito, ddb, pool_id: str, audit_table: str, *,
                 audit_key: bytes, reserve_read, clock=None):
        required = ("list_users", "admin_get_user", "admin_list_groups_for_user",
                    "admin_disable_user", "admin_enable_user")
        if (not all(callable(getattr(cognito, method, None)) for method in required)
                or not callable(getattr(ddb, "put_item", None))
                or not re.fullmatch(r"^[a-z]{2}(?:-[a-z]+)+-\d_[A-Za-z0-9]+$", pool_id or "")
                or not re.fullmatch(r"^[A-Za-z0-9_.-]{3,255}$", audit_table or "")
                or not isinstance(audit_key, bytes) or len(audit_key) < 32
                or not callable(reserve_read)):
            raise ValueError("Independent audited Cognito status control required")
        self.cognito, self.ddb = cognito, ddb
        self.pool_id, self.audit_table, self.audit_key = pool_id, audit_table, audit_key
        self.reserve_read = reserve_read
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _put(self, item):
        self.ddb.put_item(TableName=self.audit_table,
                          Item=seal_audit(item, self.audit_key),
                          ConditionExpression="attribute_not_exists(event_id)")

    def change(self, *, actor: str, email: str, action: str, expected_enabled: bool,
               confirmation: str, reason: str, idempotency_key: str) -> dict:
        if (not isinstance(actor, str) or not _OWNER_SUB.fullmatch(actor)
                or not isinstance(email, str) or len(email) > 254
                or not _EXACT_EMAIL.fullmatch(email) or email.startswith(".")
                or email.endswith(".") or ".." in email
                or action not in ("disable", "enable") or type(expected_enabled) is not bool
                or expected_enabled is not (action == "disable")
                or confirmation != action.upper() + " " + email
                or not isinstance(reason, str) or not REASON.fullmatch(reason)
                or not isinstance(idempotency_key, str) or not IDEMPOTENCY.fullmatch(idempotency_key)):
            raise ValueError("Unconfirmed or invalid Cognito sign-in change")
        # Reuse the bounded directory read rate window, even for mutations.
        self.reserve_read(actor)
        try:
            directory = self.cognito.list_users(
                UserPoolId=self.pool_id, Filter='email = "' + email + '"', Limit=2)
            if (not isinstance(directory, dict) or not isinstance(directory.get("Users"), list)
                    or len(directory["Users"]) != 1 or directory.get("PaginationToken")):
                raise UserActionConflict("Account not uniquely identified")
            entry = directory["Users"][0]
            username = entry.get("Username")
            attrs = entry.get("Attributes")
            if (not isinstance(username, str) or not 1 <= len(username) <= 128
                    or not isinstance(attrs, list) or len(attrs) > 50):
                raise UserActionUnavailable("Account identity unverified")
            emails = [a.get("Value") for a in attrs
                      if isinstance(a, dict) and a.get("Name") == "email"]
            subs = [a.get("Value") for a in attrs
                    if isinstance(a, dict) and a.get("Name") == "sub"]
            if (len(emails) != 1 or not isinstance(emails[0], str)
                    or emails[0].casefold() != email.casefold()
                    or len(subs) != 1 or not isinstance(subs[0], str)
                    or not _OWNER_SUB.fullmatch(subs[0])):
                raise UserActionUnavailable("Account identity unverified")
            if hmac.compare_digest(subs[0], actor):
                raise UserActionConflict("Owner cannot change their own sign-in access")
            # A fresh account read must agree with the unique exact-email result.
            before = self.cognito.admin_get_user(UserPoolId=self.pool_id, Username=username)
            before_attrs = before.get("UserAttributes") if isinstance(before, dict) else None
            if (not isinstance(before_attrs, list)
                    or [a.get("Value") for a in before_attrs
                        if isinstance(a, dict) and a.get("Name") == "sub"] != subs
                    or before.get("Username") != username):
                raise UserActionUnavailable("Cognito preflight identity unverified")
            if (type(before.get("Enabled")) is not bool
                    or before["Enabled"] != expected_enabled
                    or before.get("UserStatus") not in ("CONFIRMED", "EXTERNAL_PROVIDER")):
                raise UserActionConflict("Account state changed; inspect again")
            groups = self.cognito.admin_list_groups_for_user(
                UserPoolId=self.pool_id, Username=username, Limit=60)
            if (not isinstance(groups, dict) or groups.get("NextToken")
                    or not isinstance(groups.get("Groups"), list)
                    or any(not isinstance(g, dict) or not isinstance(g.get("GroupName"), str)
                           for g in groups["Groups"])):
                raise UserActionUnavailable("Account membership unverified")
            if any(g["GroupName"] == ADMIN_GROUP for g in groups["Groups"]):
                raise UserActionConflict("Owner accounts cannot be changed here")
        except (UserActionConflict, UserActionUnavailable):
            raise
        except Exception as exc:
            raise UserActionUnavailable("Cognito preflight unavailable") from exc

        key_source = (actor + "\\0" + idempotency_key).encode()
        intent_id = hmac.new(self.audit_key, b"cognito-status-intent:" + key_source, hashlib.sha256).hexdigest()
        outcome_id = hmac.new(self.audit_key, b"cognito-status-outcome:" + key_source, hashlib.sha256).hexdigest()
        target = hmac.new(self.audit_key, b"cognito-user:" + subs[0].encode(), hashlib.sha256).hexdigest()
        when = self.clock().astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        before_value = "true" if expected_enabled else "false"
        after_value = "false" if expected_enabled else "true"
        common = {
            "tenant_id": {"S": "system"}, "actor_sub": {"S": actor},
            "reason": {"S": reason}, "when_utc": {"S": when},
            "target_fingerprint": {"S": target},
            "old_enabled": {"S": before_value}, "new_enabled": {"S": after_value},
        }
        intent = {**common, "event_id": {"S": intent_id},
                  "operation": {"S": "cognito.user.status.intent"}}
        try:
            self._put(intent)
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code") if isinstance(
                getattr(exc, "response", None), dict) else None
            if code == "ConditionalCheckFailedException":
                raise UserActionConflict("Action key already consumed; inspect account before new request") from exc
            raise UserActionUnavailable("Durable action intent unavailable; no provider write attempted") from exc
        try:
            method = self.cognito.admin_disable_user if action == "disable" else self.cognito.admin_enable_user
            method(UserPoolId=self.pool_id, Username=username)
            observed = self.cognito.admin_get_user(UserPoolId=self.pool_id, Username=username)
            observed_attrs = observed.get("UserAttributes") if isinstance(observed, dict) else None
            if (not isinstance(observed_attrs, list)
                    or [a.get("Value") for a in observed_attrs
                        if isinstance(a, dict) and a.get("Name") == "sub"] != subs
                    or observed.get("Username") != username
                    or observed.get("Enabled") is not (not expected_enabled)):
                raise UserActionUnavailable("Cognito state verification unavailable")
            outcome = {**common, "event_id": {"S": outcome_id},
                       "operation": {"S": "cognito.user.status.outcome"},
                       "related_event_id": {"S": intent_id}}
            self._put(outcome)
        except Exception as exc:
            # The provider may have acted. Never automatically retry or report
            # success after an uncertain verification or audit outcome.
            raise UserActionUnavailable(
                "Account outcome indeterminate; inspect status and audit intent before new action") from exc
        return {"status": "verified", "enabled": not expected_enabled,
                "intent_event_id": intent_id, "audit_event_id": outcome_id,
                "target_fingerprint": target, "source": "Cognito verified account status"}
