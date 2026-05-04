"""GET /admin/health/database — DB latency + table reachability."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.dependencies import CurrentUser, get_db_client, require_admin
from app.core.postgres_adapter import Client

from .schemas import DatabaseStatus

router = APIRouter()


@router.get("/health/database", response_model=DatabaseStatus)
async def get_database_status(
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Get database connection and performance status.
    """
    try:
        # Test connection and measure latency
        start_time = datetime.utcnow()
        try:
            db_client.table("tenants").select("id").limit(1).execute()
            connected = True
            latency_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        except Exception:
            connected = False
            latency_ms = 0

        # Get table counts (approximation of database size)
        table_count = 0
        tables = ["tenants", "calls", "campaigns", "leads", "user_profiles",
                  "assistant_actions", "connectors", "connector_accounts"]

        for table in tables:
            try:
                db_client.table(table).select("id").limit(1).execute()
                table_count += 1
            except Exception:
                pass

        return DatabaseStatus(
            connected=connected,
            latency_ms=latency_ms,
            pool_size=10,  # Default connection pool
            active_connections=1,  # Single connection in current model
            available_connections=9,
            database_size_mb=0,  # Would need admin access to get
            table_count=table_count,
            last_check=datetime.utcnow().isoformat() + "Z"
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get database status: {str(e)}"
        )
