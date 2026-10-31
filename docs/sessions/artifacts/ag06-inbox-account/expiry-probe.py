"""Synthetic status-write seam; no database, provider or credential operations."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.infrastructure.assistant.tools import inbox
from app.infrastructure.connectors.base import ConnectorProviderError
from app.services.connector_resolver import ConnectorNotConnectedError


class DB:
    def __init__(self):
        self.rows = {
            "connector_accounts": {"connector_id": "synthetic-connector", "tenant_id": "synthetic-tenant",
                "external_account_id": "synthetic-replacement-b", "status": "active"},
            "connectors": {"id": "synthetic-connector", "tenant_id": "synthetic-tenant", "status": "active"},
        }
        self.writes = []

    def table(self, name):
        db = self
        class Query:
            def __init__(self):
                self.filters = {}
            def update(self, fields):
                self.fields = fields
                return self
            def eq(self, key, value):
                self.filters[key] = value
                return self
            def execute(self):
                row = db.rows[name]
                matched = all(row.get(k) == v for k, v in self.filters.items())
                if matched:
                    row.update(self.fields)
                db.writes.append({"table": name, "filters": self.filters, "matched": matched})
                return SimpleNamespace(data=[row] if matched else [], error=None)
        return Query()


async def main():
    old = SimpleNamespace(provider_name="gmail", external_account_id="synthetic-original-a",
        list_emails=AsyncMock(side_effect=ConnectorProviderError(provider="gmail",
            operation="list_emails", category="authentication", status_code=401, message="Synthetic")))
    resolver = AsyncMock(side_effect=[(old, "synthetic-connector", "gmail"),
        ConnectorNotConnectedError("email", connector_id="synthetic-connector", reason="refresh_unavailable")])
    db = DB()
    with patch("app.services.connector_resolver.resolve_active_connector", resolver):
        result = await inbox.read_emails("synthetic-tenant", db)
    print(json.dumps({"result": result, "replacement_rows": db.rows, "observed_writes": db.writes,
        "real_database_operations": 0, "provider_calls": 0}, indent=2))


asyncio.run(main())
