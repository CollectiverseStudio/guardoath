"""Trusted resource-scoped authorization decision for proposed agent actions.

Caller must provide identity, tenant and grants from an authenticated server-side
source, not user prompt text. The host still enforces authorization at dispatch.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    permissions: frozenset[str]

    def __post_init__(self):
        if not self.tenant_id or not self.permissions:
            raise ValueError("Trusted principal context required")
        if any(not isinstance(p, str) or not p for p in self.permissions):
            raise ValueError("Invalid permission")


def authorize(principal: Principal, *, tenant_id: str, permission: str) -> bool:
    if not tenant_id or not permission:
        return False
    return principal.tenant_id == tenant_id and permission in principal.permissions
