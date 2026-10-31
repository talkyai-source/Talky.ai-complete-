"""Bounded database observation with measured pool/database values."""
from __future__ import annotations
import asyncio
from time import perf_counter
from fastapi import APIRouter, Depends
from app.api.v1.dependencies import CurrentUser, get_db_client, require_platform_admin
from app.core.postgres_adapter import Client
from app.core.db_utils import acquire_with_tenant
from ._shared import _utc_now
from .schemas import DatabaseStatus

router = APIRouter()

@router.get("/health/database", response_model=DatabaseStatus)
async def get_database_status(admin_user: CurrentUser = Depends(require_platform_admin), db_client: Client = Depends(get_db_client)):
    try:
        start = perf_counter()
        async with acquire_with_tenant(db_client.pool, None, timeout=2.0) as conn:
            row = await asyncio.wait_for(conn.fetchrow("""
                SELECT pg_database_size(current_database()) AS size_bytes,
                       (SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'
                         AND table_type='BASE TABLE') AS table_count
            """), timeout=2.0)
        if row is None:
            raise RuntimeError("Database observation missing")
        size = db_client.pool.get_size()
        idle = db_client.pool.get_idle_size()
        return DatabaseStatus(connected=True, latency_ms=max(0, round((perf_counter() - start) * 1000)),
            pool_size=size, active_connections=max(0, size-idle), available_connections=idle,
            database_size_mb=round(int(row["size_bytes"]) / 1024**2, 2), table_count=int(row["table_count"]), last_check=_utc_now())
    except Exception:
        return DatabaseStatus(connected=False, last_check=_utc_now(), error="Database observation failed or timed out; metrics are unavailable.")
