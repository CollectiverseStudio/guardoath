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
from guardoath.admin_audit import AdminAuditReader
from guardoath.admin_users import ADMIN_GROUP, _EXACT_EMAIL, _OWNER_SUB
from guardoath.admin_actions import IDEMPOTENCY, REASON


class UserActionConflict(Exception):
    pass


class UserActionUnavailable(Exception):
    pass


def _matches_exact_cognito_identity(attributes: object, email: str, subject: str) -> bool:
    """Refuse changed, missing or ambiguous Cognito email/sub at every read."""
    if (not isinstance(attributes, list) or len(attributes) > 50
            or any(not isinstance(item, dict)
                   or not isinstance(item.get("Name"), str)
                   or not isinstance(item.get("Value"), str)
                   for item in attributes)):
        return False
    emails = [item["Value"] for item in attributes if item["Name"] == "email"]
    subjects = [item["Value"] for item in attributes if item["Name"] == "sub"]
    return (len(emails) == 1 and emails[0].casefold() == email.casefold()
            and subjects == [subject])


class AdminCognitoUserActions:
    def __init__(self, cognito, ddb, pool_id: str, audit_table: str, *,
                 audit_key: bytes, reserve_read, clock=None):
        required = ("list_users", "admin_get_user", "admin_list_groups_for_user",
                    "admin_disable_user", "admin_enable_user")
        if (not all(callable(getattr(cognito, method, None)) for method in required)
                or not callable(getattr(ddb, "put_item", None))
                or not callable(getattr(ddb, "get_item", None))
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

    def _verified_live_state(self, *, actor: str, email: str, fingerprint: str) -> dict:
        """Read current Cognito state only for the signed action's exact target.

        A matching current state cannot establish that an earlier indeterminate
        provider write succeeded. Never return raw email, username or subject.
        """
        try:
            self.reserve_read(actor)
        except Exception as exc:
            raise UserActionUnavailable("Cognito read budget unavailable") from exc
        try:
            directory = self.cognito.list_users(
                UserPoolId=self.pool_id, Filter='email = "' + email + '"', Limit=2)
            if (not isinstance(directory, dict) or not isinstance(directory.get("Users"), list)
                    or len(directory["Users"]) != 1 or directory.get("PaginationToken") is not None):
                raise UserActionConflict("Signed action target no longer uniquely matches the supplied account")
            entry = directory["Users"][0]
            username = entry.get("Username") if isinstance(entry, dict) else None
            attrs = entry.get("Attributes") if isinstance(entry, dict) else None
            if (not isinstance(username, str) or not 1 <= len(username) <= 128
                    or not isinstance(attrs, list) or len(attrs) > 50):
                raise UserActionUnavailable("Cognito target identity unavailable")

            def identities(attributes):
                if not isinstance(attributes, list) or len(attributes) > 50:
                    raise UserActionUnavailable("Cognito attributes unavailable")
                emails = [a.get("Value") for a in attributes
                          if isinstance(a, dict) and a.get("Name") == "email"]
                subs = [a.get("Value") for a in attributes
                        if isinstance(a, dict) and a.get("Name") == "sub"]
                if (len(emails) != 1 or len(subs) != 1
                        or not isinstance(emails[0], str) or emails[0].casefold() != email.casefold()
                        or not isinstance(subs[0], str) or not _OWNER_SUB.fullmatch(subs[0])):
                    raise UserActionConflict("Signed action target does not match current Cognito identity")
                return subs[0]
            sub = identities(attrs)
            actual = hmac.new(self.audit_key, b"cognito-user:" + sub.encode(),
                              hashlib.sha256).hexdigest()
            if not hmac.compare_digest(actual, fingerprint):
                raise UserActionConflict("Signed action reference belongs to another account")
            observed = self.cognito.admin_get_user(UserPoolId=self.pool_id, Username=username)
            if (not isinstance(observed, dict) or observed.get("Username") != username
                    or identities(observed.get("UserAttributes")) != sub
                    or type(entry.get("Enabled")) is not bool
                    or type(observed.get("Enabled")) is not bool
                    or entry["Enabled"] is not observed["Enabled"]
                    or not isinstance(observed.get("UserStatus"), str)
                    or entry.get("UserStatus") != observed["UserStatus"]):
                raise UserActionUnavailable("Cognito state changed during independent inspection")
            current = observed["Enabled"]
        except (UserActionConflict, UserActionUnavailable):
            raise
        except Exception as exc:
            raise UserActionUnavailable("Current Cognito status unavailable") from exc
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise UserActionUnavailable("Current Cognito observation time unavailable")
        return {"current_provider_state_verified": True, "current_enabled": current,
                "current_observed_at_utc": now.astimezone(timezone.utc).isoformat(
                    timespec="seconds").replace("+00:00", "Z")}

    def inspect_receipt(self, *, actor: str, idempotency_key: str,
                        email: str | None = None) -> dict:
        """Verify owner-bound history; optionally compare a fresh exact Cognito read.

        No receipt inspection ever retries the original mutation. Live status
        does not resolve the causal outcome of an indeterminate signed intent.
        """
        if (not isinstance(actor, str) or not _OWNER_SUB.fullmatch(actor)
                or not isinstance(idempotency_key, str)
                or not IDEMPOTENCY.fullmatch(idempotency_key)
                or (email is not None and (not isinstance(email, str) or len(email) > 254
                     or not _EXACT_EMAIL.fullmatch(email) or email.startswith(".")
                     or email.endswith(".") or ".." in email))):
            raise ValueError("Invalid owner action reference or exact account email")
        # Must match the original mutation's literal backslash-zero separator.
        # Changing this canonical byte string makes all signed history unreachable.
        key_source = (actor + "\\0" + idempotency_key).encode()
        intent_id = hmac.new(self.audit_key, b"cognito-status-intent:" + key_source,
                             hashlib.sha256).hexdigest()
        outcome_id = hmac.new(self.audit_key, b"cognito-status-outcome:" + key_source,
                              hashlib.sha256).hexdigest()
        try:
            reader = AdminAuditReader(self.ddb, self.audit_table, verify_key=self.audit_key)
            intent, outcome = reader.read(intent_id), reader.read(outcome_id)
        except Exception as exc:
            raise UserActionUnavailable("Signed account-action audit unavailable") from exc
        if intent is None:
            if outcome is not None:
                raise UserActionUnavailable("Orphaned account-action outcome")
            return {"status": "not_found", "intent_event_id": None,
                    "outcome_event_id": None, "current_provider_state_verified": False,
                    "note": "No signed intent found; no account state inferred"}
        before = intent.get("change") or {}
        if (intent.get("operation") != "cognito.user.status.intent"
                or intent.get("actor_sub") != actor
                or intent.get("integrity_status") != "signed_verified"
                or before.get("field") != "cognito_enabled"):
            raise UserActionUnavailable("Account-action intent integrity unavailable")
        if outcome is None:
            history = {"status": "indeterminate", "intent_event_id": intent_id,
                       "outcome_event_id": None, "current_provider_state_verified": False,
                       "note": "Signed intent exists without a verified outcome; Cognito may have acted"}
        else:
            after = outcome.get("change") or {}
            if (outcome.get("operation") != "cognito.user.status.outcome"
                    or outcome.get("integrity_status") != "signed_verified"
                    or outcome.get("actor_sub") != actor
                    or after.get("related_event_id") != intent_id
                    or after.get("field") != "cognito_enabled"
                    or after.get("before") is not before.get("before")
                    or after.get("after") is not before.get("after")
                    or after.get("target_fingerprint") != before.get("target_fingerprint")
                    or outcome.get("reason") != intent.get("reason")):
                raise UserActionUnavailable("Account-action outcome does not match intent")
            history = {"status": "outcome_recorded", "intent_event_id": intent_id,
                       "outcome_event_id": outcome_id, "enabled_at_verification": after["after"],
                       "current_provider_state_verified": False,
                       "note": "Historical signed outcome only; inspect live Cognito state separately"}
        if email is None:
            return history
        live = self._verified_live_state(
            actor=actor, email=email, fingerprint=before["target_fingerprint"])
        matches = (live["current_enabled"] == history["enabled_at_verification"]
                   if history["status"] == "outcome_recorded" else None)
        return {**history, **live, "current_matches_recorded_outcome": matches,
                "note": ("Independent Cognito snapshot only; a matching enabled state does "
                         "not prove the historical mutation's cause or resolve an "
                         "indeterminate provider outcome")}

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
        # Consume idempotency in the independent audit store BEFORE any
        # Cognito provider call. The eventual conditional PutItem is still
        # required to arbitrate concurrent requests racing this read.
        key_source = (actor + "\\0" + idempotency_key).encode()
        intent_id = hmac.new(self.audit_key, b"cognito-status-intent:" + key_source, hashlib.sha256).hexdigest()
        outcome_id = hmac.new(self.audit_key, b"cognito-status-outcome:" + key_source, hashlib.sha256).hexdigest()
        try:
            prior = self.ddb.get_item(
                TableName=self.audit_table, Key={"event_id": {"S": intent_id}},
                ConsistentRead=True)
            if not isinstance(prior, dict):
                raise UserActionUnavailable("Idempotency lookup unavailable")
            if prior.get("Item") is not None:
                raise UserActionConflict("Action key already consumed; inspect account before new request")
        except (UserActionConflict, UserActionUnavailable):
            raise
        except Exception as exc:
            raise UserActionUnavailable("Idempotency lookup unavailable") from exc

        # Reuse the bounded directory read rate window, even for mutations.
        try:
            self.reserve_read(actor)
        except Exception as exc:
            raise UserActionUnavailable("Cognito request budget unavailable") from exc
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
            if (not _matches_exact_cognito_identity(before_attrs, email, subs[0])
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
            if (not _matches_exact_cognito_identity(observed_attrs, email, subs[0])
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
