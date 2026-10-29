"""Tenant-aware authenticated application registry.

This registry is a trusted startup snapshot, not a user registration endpoint.
"""
from dataclasses import dataclass
import hmac


@dataclass(frozen=True)
class TenantApplication:
    tenant_id: str
    application_id: str
    api_key: str

    def __post_init__(self):
        if not self.tenant_id or not self.application_id or not self.api_key:
            raise ValueError("Tenant, application and credential required")


class TenantRegistry:
    def __init__(self, applications: tuple[TenantApplication, ...]):
        if len({app.api_key for app in applications}) != len(applications):
            raise ValueError("Duplicate credential")
        if len({app.application_id for app in applications}) != len(applications):
            raise ValueError("Application identifiers must be globally unique")
        self._applications = tuple(applications)

    def application_ids(self) -> tuple[str, ...]:
        return tuple(app.application_id for app in self._applications)

    def authenticate(self, token: str) -> TenantApplication | None:
        if not token:
            return None
        for application in self._applications:
            if hmac.compare_digest(application.api_key.encode(), token.encode()):
                return application
        return None

    def tenant_for_application(self, application_id: str) -> str | None:
        for application in self._applications:
            if application.application_id == application_id:
                return application.tenant_id
        return None
