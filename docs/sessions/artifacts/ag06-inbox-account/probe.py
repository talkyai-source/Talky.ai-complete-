"""Run from backend with Python; synthetic ports, no provider/DB operations."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.infrastructure.assistant.tools import inbox
from app.infrastructure.connectors.base import ConnectorProviderError


async def run(account):
    original = SimpleNamespace(external_account_id="synthetic-a", provider_name="gmail")
    fresh = SimpleNamespace(external_account_id=account, provider_name="gmail")
    observed = []

    async def read(current):
        observed.append(current.external_account_id)
        if len(observed) == 1:
            raise ConnectorProviderError(provider="gmail", operation="get_email",
                category="authentication", status_code=401, message="Synthetic rejection")
        return {"message_id": "synthetic-message", "read_account": current.external_account_id}

    resolver = AsyncMock(return_value=(fresh, "synthetic-connector", "gmail"))
    result = None
    error = None
    with patch("app.services.connector_resolver.resolve_active_connector", resolver):
        try:
            result = await inbox._call_with_one_auth_refresh(original,
                connector_id="synthetic-connector", tenant_id="synthetic-tenant",
                db_client=object(), operation=read)
        except Exception as exc:
            error = type(exc).__name__
    return {"refresh_account": account, "observed_accounts": observed,
        "result": result, "error_type": error, "resolver_kwargs": resolver.call_args.kwargs}


async def main():
    source = Path(inbox.__file__)
    print(json.dumps({"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "observations": [await run("synthetic-b"), await run("synthetic-a")],
        "provider_calls": 0, "database_calls": 0}, indent=2))


asyncio.run(main())
