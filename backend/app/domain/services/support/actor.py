"""Who is acting. Built by the API layer from CurrentUser, never from the body."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SupportActor:
    user_id: str
    tenant_id: str | None
    role: str
    can_report: bool = False
    can_read_team: bool = False
    can_manage: bool = False

    def require_tenant(self) -> str:
        if not self.tenant_id:
            from app.domain.services.support.errors import SupportForbidden

            raise SupportForbidden("Support needs a workspace account.", code="tenant_required")
        return self.tenant_id
