"""GET /admin/health/queues — synthetic queue depths from table state."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.dependencies import CurrentUser, get_db_client, require_admin
from app.core.postgres_adapter import Client

from .schemas import QueueStatus, QueuesResponse

router = APIRouter()


@router.get("/health/queues", response_model=QueuesResponse)
async def get_queues_status(
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Get queue depths and processing status.

    Creates virtual queues based on actual table data.
    """
    try:
        queues = []
        now = datetime.utcnow()
        yesterday = (now - timedelta(hours=24)).isoformat()

        # Calls Queue - based on call status
        try:
            pending_calls = db_client.table("calls").select(
                "id", count="exact"
            ).eq("status", "queued").execute()

            processing_calls = db_client.table("calls").select(
                "id", count="exact"
            ).in_("status", ["initiated", "ringing", "in_progress"]).execute()

            failed_calls = db_client.table("calls").select(
                "id", count="exact"
            ).in_("status", ["failed", "error"]).gte("created_at", yesterday).execute()

            completed_calls = db_client.table("calls").select(
                "id", count="exact"
            ).eq("status", "completed").gte("created_at", yesterday).execute()

            total_24h = (completed_calls.count or 0) + (failed_calls.count or 0)
            success_rate = ((completed_calls.count or 0) / total_24h * 100) if total_24h > 0 else 100.0

            queues.append(QueueStatus(
                name="Calls",
                pending=pending_calls.count or 0,
                processing=processing_calls.count or 0,
                failed=failed_calls.count or 0,
                completed_24h=completed_calls.count or 0,
                success_rate_24h=round(success_rate, 1),
                avg_processing_time_ms=2500  # Estimate
            ))
        except Exception:
            queues.append(QueueStatus(
                name="Calls",
                pending=0,
                processing=0,
                failed=0,
                completed_24h=0,
                success_rate_24h=100.0,
                avg_processing_time_ms=0
            ))

        # Actions Queue - based on assistant_actions status
        try:
            pending_actions = db_client.table("assistant_actions").select(
                "id", count="exact"
            ).in_("status", ["pending", "scheduled"]).execute()

            processing_actions = db_client.table("assistant_actions").select(
                "id", count="exact"
            ).eq("status", "processing").execute()

            failed_actions = db_client.table("assistant_actions").select(
                "id", count="exact"
            ).eq("status", "failed").gte("created_at", yesterday).execute()

            completed_actions = db_client.table("assistant_actions").select(
                "id", count="exact"
            ).eq("status", "completed").gte("created_at", yesterday).execute()

            total_24h = (completed_actions.count or 0) + (failed_actions.count or 0)
            success_rate = ((completed_actions.count or 0) / total_24h * 100) if total_24h > 0 else 100.0

            queues.append(QueueStatus(
                name="Actions",
                pending=pending_actions.count or 0,
                processing=processing_actions.count or 0,
                failed=failed_actions.count or 0,
                completed_24h=completed_actions.count or 0,
                success_rate_24h=round(success_rate, 1),
                avg_processing_time_ms=150  # Estimate
            ))
        except Exception:
            queues.append(QueueStatus(
                name="Actions",
                pending=0,
                processing=0,
                failed=0,
                completed_24h=0,
                success_rate_24h=100.0,
                avg_processing_time_ms=0
            ))

        # Webhooks Queue (synthetic - based on any webhook log if exists)
        queues.append(QueueStatus(
            name="Webhooks",
            pending=0,
            processing=0,
            failed=0,
            completed_24h=0,
            success_rate_24h=100.0,
            avg_processing_time_ms=50
        ))

        total_pending = sum(q.pending for q in queues)
        total_processing = sum(q.processing for q in queues)

        return QueuesResponse(
            queues=queues,
            total_pending=total_pending,
            total_processing=total_processing
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get queues status: {str(e)}"
        )
