"""GET /admin/health/workers — background worker status."""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.dependencies import CurrentUser, get_db_client, require_admin
from app.core.postgres_adapter import Client

from ._shared import _server_start_time
from .schemas import WorkersResponse, WorkerStatus

router = APIRouter()


@router.get("/health/workers", response_model=WorkersResponse)
async def get_workers_status(
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Get status of background workers.

    Returns real worker data from worker_status table if available,
    otherwise returns current process info as single worker.
    """
    try:
        workers = []

        # Try to get worker data from database if table exists
        try:
            worker_response = db_client.table("worker_status").select("*").execute()

            for w in worker_response.data or []:
                last_heartbeat = w.get("last_heartbeat", "")
                uptime = 0
                if w.get("started_at"):
                    try:
                        started = datetime.fromisoformat(w["started_at"].replace("Z", "+00:00").replace("+00:00", ""))
                        uptime = int((datetime.utcnow() - started).total_seconds())
                    except Exception:
                        pass

                processed = w.get("processed_count", 0)
                failed = w.get("failed_count", 0)
                success_rate = ((processed - failed) / processed * 100) if processed > 0 else 100.0

                workers.append(WorkerStatus(
                    id=w.get("id", str(uuid4())),
                    name=w.get("name", "worker"),
                    status=w.get("status", "idle"),
                    current_task=w.get("current_task"),
                    processed_count=processed,
                    failed_count=failed,
                    success_rate=round(success_rate, 1),
                    last_heartbeat=last_heartbeat,
                    uptime_seconds=uptime
                ))
        except Exception:
            # Table doesn't exist, show main process as worker
            pass

        # If no workers from DB, create synthetic worker from main process
        if not workers:
            uptime = int((datetime.utcnow() - _server_start_time).total_seconds())
            workers.append(WorkerStatus(
                id="main-process",
                name="API Server",
                status="idle",
                current_task=None,
                processed_count=0,
                failed_count=0,
                success_rate=100.0,
                last_heartbeat=datetime.utcnow().isoformat() + "Z",
                uptime_seconds=uptime
            ))

        active = len([w for w in workers if w.status != "offline"])
        busy = len([w for w in workers if w.status == "busy"])

        return WorkersResponse(
            workers=workers,
            total_workers=len(workers),
            active_workers=active,
            busy_workers=busy
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get workers status: {str(e)}"
        )
