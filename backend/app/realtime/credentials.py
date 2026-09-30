"""Resolve the same tenant credential for catalogs, previews and calls."""
import os

async def resolve_openai_key(tenant_id=None):
    if tenant_id:
        from app.domain.services.credential_resolver import get_credential_resolver
        return await get_credential_resolver().resolve("openai", tenant_id=tenant_id)
    return os.getenv("OPENAI_API_KEY")
