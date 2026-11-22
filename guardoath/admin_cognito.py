"""Cognito-backed owner authorization for the isolated read-only admin console.

An administrative subject is authorized only after signed token validation AND
fresh provider-side account/group checks. MFA enrollment is not evidence that
MFA occurred on this particular login; a signed, recognized AMR claim is also
required. If the Cognito pool does not emit that evidence, deny until an
approved, independently verifiable MFA mechanism is available.
"""
from __future__ import annotations

import re
from time import time
from typing import Any, Protocol

from guardoath.admin_console import VerifiedAdmin
from guardoath.admin_users import ADMIN_GROUP


class SessionValidator(Protocol):
    pool_id: str
    def verified_claims(self, token: str) -> dict | None: ...


class CognitoAdminClient(Protocol):
    def get_user(self, *, AccessToken: str) -> dict: ...
    def admin_get_user(self, *, UserPoolId: str, Username: str) -> dict: ...
    def admin_list_groups_for_user(self, *, UserPoolId: str, Username: str, Limit: int) -> dict: ...


SUBJECT = re.compile(r"^[0-9a-fA-F-]{20,80}$")
# An explicit, signed authentication-method reference. Never infer this from
# a user's preferred MFA settings, email verification or group membership.
# Cognito's signed "mfa" value means that multiple factors were completed.
# "otp" alone can denote a SINGLE authenticator, not multifactor evidence.
# Token refresh preserves AMR, so require a fresh signed auth_time too.
ADMIN_MFA_MAX_AGE_SECONDS = 900
ADMIN_CLOCK_SKEW_SECONDS = 30


def _has_recent_mfa(claims: dict) -> bool:
    methods = claims.get("amr")
    if (not isinstance(methods, list) or "mfa" not in methods
            or not all(isinstance(v, str) for v in methods)):
        return False
    authenticated = claims.get("auth_time")
    if type(authenticated) is not int:
        return False
    elapsed = int(time()) - authenticated
    return -ADMIN_CLOCK_SKEW_SECONDS <= elapsed <= ADMIN_MFA_MAX_AGE_SECONDS


def _verified_owner_groups(response: Any) -> bool:
    """Require one complete, internally consistent Cognito membership page.

    A matching owner entry in an otherwise malformed or paginated response is
    not sufficient authorization. The caller must never trust token group claims.
    """
    if (not isinstance(response, dict) or
            ("NextToken" in response and response["NextToken"] is not None) or
            not isinstance(response.get("Groups"), list) or
            len(response["Groups"]) > 60):
        return False
    names: set[str] = set()
    for group in response["Groups"]:
        if not isinstance(group, dict):
            return False
        name = group.get("GroupName")
        if (not isinstance(name, str) or not 1 <= len(name) <= 128 or
                name in names):
            return False
        names.add(name)
    return ADMIN_GROUP in names


def verify_admin(token: str, sessions: SessionValidator, cognito: CognitoAdminClient) -> VerifiedAdmin | None:
    """Verify owner role from trusted provider, not user-supplied claims.

    No error details or provider account metadata are returned. Provider errors
    propagate to the caller, which must respond 503 and fail closed.
    """
    claims = sessions.verified_claims(token)
    if not isinstance(claims, dict):
        return None
    subject = claims.get("sub")
    if not isinstance(subject, str) or not SUBJECT.fullmatch(subject) or not _has_recent_mfa(claims):
        return None
    username = claims.get("username")
    if not isinstance(username, str) or not 1 <= len(username) <= 128:
        return None
    # GetUser validates this very access token with Cognito, including revocation.
    current = cognito.get_user(AccessToken=token)
    if not isinstance(current, dict) or current.get("Username") != username:
        return None
    # Never use claims['cognito:groups'] as membership authority. AdminGetUser
    # confirms the person is currently enabled; group fetch is independent.
    user: dict[str, Any] = cognito.admin_get_user(UserPoolId=sessions.pool_id, Username=username)
    if user.get("Enabled") is not True or user.get("UserStatus") not in {"CONFIRMED", "EXTERNAL_PROVIDER"}:
        return None
    attributes = user.get("UserAttributes")
    if not isinstance(attributes, list):
        return None
    subs = [a.get("Value") for a in attributes if isinstance(a, dict) and a.get("Name") == "sub"]
    if len(subs) != 1 or subs[0] != subject:
        return None
    # Bound group list to one provider page. Any truncation is denied, rather
    # than accepting a partial administrative membership view.
    groups = cognito.admin_list_groups_for_user(UserPoolId=sessions.pool_id, Username=username, Limit=60)
    if not _verified_owner_groups(groups):
        return None
    return VerifiedAdmin(subject=subject, roles=frozenset({"owner"}), mfa_verified=True)


def make_verifier(sessions: SessionValidator, cognito: CognitoAdminClient):
    """Inject into create_admin_router(verify_cognito_admin=...)."""
    return lambda token: verify_admin(token, sessions, cognito)
