"""AI Options config endpoints must acquire their connection WITH a tenant RLS
context.

Live failure 2026-09-02 (prod, after Alembic 0038 forced RLS on
``tenant_ai_configs`` and the app role lost BYPASSRLS): ``GET
/ai-options/config`` returned 500 for every tenant —

    asyncpg.exceptions.InsufficientPrivilegeError: new row violates row-level
    security policy for table "tenant_ai_configs"

because ``get_config`` used a bare ``pool.acquire()``: with no
``app.current_tenant_id`` GUC the SELECT matched zero rows (the row existed),
the handler concluded the tenant had no config, and its bootstrap INSERT was
rejected by the same policy as WITH CHECK. ``save_config`` and the campaign
create/update + assistant voice resolution read the same table the same way.

The fake pool below behaves like that policy: the config row is invisible and
the INSERT is refused unless the connection ran ``SET LOCAL
app.current_tenant_id`` first.
"""
from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

TENANT = "11111111-2222-4333-8444-555555555555"


class _RlsConn:
    """Mimics asyncpg under a forced tenant policy on tenant_ai_configs."""

    def __init__(self, rows_by_tenant: dict[str, dict]):
        self._rows = rows_by_tenant
        self.tenant_guc: str | None = None
        self.bypass = False
        self.executed: list[str] = []
        self.inserted: list[str] = []

    def _visible(self, tenant_id: str) -> bool:
        return self.bypass or self.tenant_guc == tenant_id

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield self

    async def execute(self, sql: str, *args):
        self.executed.append(sql)
        s = " ".join(sql.split())
        if s.startswith("SET LOCAL app.current_tenant_id"):
            self.tenant_guc = s.split("'")[1]
            return "SET"
        if s.startswith("SET LOCAL app.bypass_rls"):
            self.bypass = True
            return "SET"
        if "INSERT INTO tenant_ai_configs" in s:
            tenant_id = str(args[0])
            if not self._visible(tenant_id):
                raise PermissionError(
                    'new row violates row-level security policy for table "tenant_ai_configs"'
                )
            self.inserted.append(tenant_id)
            return "INSERT 0 1"
        return "OK"

    async def fetchrow(self, sql: str, *args):
        self.executed.append(sql)
        if "FROM tenant_ai_configs" in sql:
            tenant_id = str(args[0])
            row = self._rows.get(tenant_id)
            return row if (row is not None and self._visible(tenant_id)) else None
        return None


class _Pool:
    def __init__(self, conn: _RlsConn):
        self.conn = conn

    @contextlib.asynccontextmanager
    async def acquire(self, *a, **kw):
        yield self.conn


def _existing_row() -> dict:
    # Column set _fetch_tenant_config reads; values pick the ElevenLabs path so
    # get_config's auto-correct branches (deepgram/cartesia) stay out of the way.
    return {
        "llm_provider": "groq",
        "llm_model": "openai/gpt-oss-120b",
        "llm_temperature": 0.5,
        "llm_max_tokens": 150,
        "stt_provider": "deepgram",
        "stt_model": "flux-general-en",
        "stt_language": "en",
        "tts_provider": "elevenlabs",
        "tts_model": "eleven_flash_v2_5",
        "tts_voice_id": "lfPTQbwnu1oXQ9g6V0r4",
        "tts_sample_rate": 8000,
        "voice_tuning": "{}",
        "stt_engine": None,
        "pipeline_mode": None,
        "realtime_model": None,
        "realtime_voice": None,
        "realtime_settings": None,
    }


@pytest.mark.asyncio
async def test_get_config_reads_the_tenant_row_under_rls():
    from app.api.v1.endpoints.ai_options.config import get_config

    conn = _RlsConn({TENANT: _existing_row()})
    db_client = SimpleNamespace(pool=_Pool(conn))
    user = SimpleNamespace(tenant_id=TENANT, id="user-1")

    config = await get_config(current_user=user, db_client=db_client)

    # The existing row must be seen — not mistaken for "no config" and
    # re-created — and it must be read under this tenant's GUC.
    assert config.tts_provider == "elevenlabs"
    assert config.tts_voice_id == "lfPTQbwnu1oXQ9g6V0r4"
    assert conn.tenant_guc == TENANT
    assert conn.inserted == []


@pytest.mark.asyncio
async def test_get_config_bootstraps_missing_row_with_tenant_context():
    from app.api.v1.endpoints.ai_options.config import get_config

    conn = _RlsConn({})  # tenant has no config yet
    db_client = SimpleNamespace(pool=_Pool(conn))
    user = SimpleNamespace(tenant_id=TENANT, id="user-1")

    await get_config(current_user=user, db_client=db_client)

    assert conn.inserted == [TENANT]


@pytest.mark.asyncio
async def test_save_config_writes_with_tenant_context(monkeypatch):
    from app.api.v1.endpoints.ai_options import config as cfg_mod
    from app.domain.models.ai_config import AIProviderConfig

    # save_config validates model + voice against the ElevenLabs live catalog;
    # keep the test off the network.
    async def _models():
        return [SimpleNamespace(id="eleven_flash_v2_5")]

    async def _voices():
        return [SimpleNamespace(id="lfPTQbwnu1oXQ9g6V0r4")]

    monkeypatch.setattr(cfg_mod, "get_elevenlabs_tts_models_for_current_key", _models)
    monkeypatch.setattr(cfg_mod, "get_elevenlabs_voices_for_current_key", _voices)

    conn = _RlsConn({})
    db_client = SimpleNamespace(pool=_Pool(conn))
    user = SimpleNamespace(tenant_id=TENANT, id="user-1")
    row = {k: v for k, v in _existing_row().items()
           if k not in {"voice_tuning", "realtime_settings"} and v is not None}
    row["llm_model"] = cfg_mod.GROQ_MODELS[0].id
    body = AIProviderConfig(**row)

    await cfg_mod.save_config(config=body, current_user=user, db_client=db_client)

    assert conn.inserted == [TENANT]
