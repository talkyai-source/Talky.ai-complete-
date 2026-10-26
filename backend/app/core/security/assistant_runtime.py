"""Required, content-free receipts for work owned by assistant socket handlers.

These receipts describe this server runtime, never peer acknowledgement, remote
provider cancellation, or coverage of workers running older application code.
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime
from uuid import UUID, uuid4

from app.core.db_utils import acquire_with_tenant
from app.core.security.platform_admin_guard import lock_user_administration
from app.core.security.principal import PrincipalUnavailable, load_session_principal
from app.domain.services.audit_logger import AuditEvent, AuditLogger

REGISTER_ACTION = "assistant_runtime_registered"
STOP_ACTION = "assistant_runtime_owner_stopped"
RESOURCE_TYPE = "assistant_runtime"
RECEIPT_TIMEOUT_SECONDS = 3.0
CLEANUP_TIMEOUT_SECONDS = 3.0
OWNER_INSTANCE_ID = str(uuid4())
runtime_context = ContextVar("assistant_runtime", default=None)
logger = logging.getLogger(__name__)


def _metadata(row):
    value = row.get("metadata")
    return json.loads(value) if isinstance(value, str) else value


def stop_receipt_matches(registration, stopped) -> bool:
    """Match full audit rows; malformed or incomplete evidence is not proof."""
    try:
        before, after = _metadata(registration), _metadata(stopped)
        if not isinstance(before, dict) or not isinstance(after, dict):
            return False
        if (registration["event_type"] != AuditEvent.SESSION_CREATED.value
                or registration["action"] != REGISTER_ACTION
                or stopped["event_type"] != AuditEvent.SESSION_TERMINATED.value
                or stopped["action"] != STOP_ACTION):
            return False
        if (type(before.get("version")) is not int or before["version"] != 1
                or type(after.get("version")) is not int):
            return False
        identity = ("version", "runtime_id", "user_id", "tenant_id",
                    "security_session_id", "endpoint_kind", "owner_instance_id", "registered_at")
        if any(before.get(key) is None or before[key] != after.get(key) for key in identity):
            return False
        for key in ("runtime_id", "user_id", "tenant_id", "security_session_id", "owner_instance_id"):
            UUID(before[key])
        if before["endpoint_kind"] not in {"chat", "voice"}:
            return False
        for row in (registration, stopped):
            if (row.get("resource_type") != RESOURCE_TYPE
                    or str(row.get("resource_id")) != before["runtime_id"]
                    or str(row.get("tenant_id")) != before["tenant_id"]):
                return False
        start = datetime.fromisoformat(before["registered_at"])
        end = datetime.fromisoformat(after["stopped_at"])
        return (start.utcoffset() is not None and end.utcoffset() is not None and end >= start
                and str(registration["event_id"]) == after.get("registration_event_id")
                and after.get("owner_stopped") is True
                and after.get("cleanup_status") == "complete")
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


class AssistantRuntime:
    def __init__(self, pool, metadata, registration_event_id):
        self.pool = pool
        self.metadata = metadata
        self.registration_event_id = str(registration_event_id)
        self.cleanup_complete = True
        self.owned_tasks = set()
        self.cleanup_tasks = set()
        self.stopped_event_id = None

    @classmethod
    async def register(cls, pool, claims, *, endpoint_kind):
        if endpoint_kind not in {"chat", "voice"}:
            raise ValueError("Unknown assistant runtime kind")
        identity = {"user_id": str(UUID(claims["sub"])),
                    "tenant_id": str(UUID(claims["tenant_id"])),
                    "security_session_id": str(UUID(claims["sid"]))}
        metadata = {"version": 1, "runtime_id": str(uuid4()), **identity,
                    "endpoint_kind": endpoint_kind, "owner_instance_id": OWNER_INSTANCE_ID}
        audit = AuditLogger(pool)
        async with asyncio.timeout(RECEIPT_TIMEOUT_SECONDS):
            async with acquire_with_tenant(pool, None, timeout=RECEIPT_TIMEOUT_SECONDS) as conn:
                await audit.lock_transaction(conn)
                await lock_user_administration(conn)
                principal = await load_session_principal(conn, claims)
                if str(principal.get("tenant_id")) != identity["tenant_id"]:
                    raise PrincipalUnavailable("tenant_changed")
                metadata["registered_at"] = (await conn.fetchval("SELECT clock_timestamp()")).isoformat()
                event_id = await audit.log_in_transaction(
                    conn, event_type=AuditEvent.SESSION_CREATED, action=REGISTER_ACTION,
                    actor_id=identity["user_id"], actor_role=principal.get("role"),
                    tenant_id=identity["tenant_id"], resource_type=RESOURCE_TYPE,
                    resource_id=metadata["runtime_id"], metadata=metadata,
                )
        # No work may start until the required transaction has committed.
        return cls(pool, metadata, event_id)

    def own_task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.owned_tasks.add(task)
        task.add_done_callback(self.owned_tasks.discard)
        task.add_done_callback(_consume_result)
        return task

    async def stop_tasks(self):
        tasks = set(self.owned_tasks)
        for task in tasks:
            if not task.done() and task not in self.cleanup_tasks:
                task.cancel()
        if not tasks:
            return
        try:
            _, pending = await asyncio.wait(tasks, timeout=CLEANUP_TIMEOUT_SECONDS)
        except BaseException:
            self.cleanup_complete = False
            raise
        if pending:
            self.cleanup_complete = False
        # Consume exceptions, but never wait indefinitely for a task that
        # suppresses cancellation; its missing stop receipt stays unknown.
        for task in tasks:
            task.add_done_callback(_consume_result)

    async def record_stopped(self, *, close_frame_status="not_attempted"):
        if (not self.cleanup_complete or any(not task.done() for task in self.owned_tasks)
                or self.stopped_event_id is not None):
            return self.stopped_event_id
        try:
            async with asyncio.timeout(RECEIPT_TIMEOUT_SECONDS):
                async with acquire_with_tenant(self.pool, None, timeout=RECEIPT_TIMEOUT_SECONDS) as conn:
                    audit = AuditLogger(self.pool)
                    await audit.lock_transaction(conn)
                    metadata = {**self.metadata, "registration_event_id": self.registration_event_id,
                                "owner_stopped": True, "cleanup_status": "complete",
                                "close_frame_status": close_frame_status,
                                "stopped_at": (await conn.fetchval("SELECT clock_timestamp()")).isoformat()}
                    event_id = await audit.log_in_transaction(
                        conn, event_type=AuditEvent.SESSION_TERMINATED, action=STOP_ACTION,
                        actor_type="system", tenant_id=self.metadata["tenant_id"],
                        resource_type=RESOURCE_TYPE, resource_id=self.metadata["runtime_id"], metadata=metadata,
                    )
            self.stopped_event_id = str(event_id)
        except Exception:
            # Work has already stopped. A failed/ambiguous receipt is unknown,
            # never a reason to replay work or claim that it is still running.
            logger.warning("Assistant runtime stop receipt unavailable")
        return self.stopped_event_id


def _consume_result(task):
    if not task.cancelled():
        task.exception()


async def complete_cleanup(awaitable):
    """Bound a local cleanup operation and preserve uncertainty on every failure."""
    runtime = runtime_context.get()
    deadline = asyncio.get_running_loop().time() + CLEANUP_TIMEOUT_SECONDS
    task = asyncio.create_task(awaitable)
    if runtime is not None:
        runtime.owned_tasks.add(task)
        runtime.cleanup_tasks.add(task)
        task.add_done_callback(runtime.owned_tasks.discard)
        task.add_done_callback(runtime.cleanup_tasks.discard)
    try:
        done, _ = await asyncio.wait({task}, timeout=CLEANUP_TIMEOUT_SECONDS)
        if not done:
            task.cancel()
            if runtime is not None:
                runtime.cleanup_complete = False
            return False
        task.result()
        return True
    except asyncio.CancelledError:
        # A consumer may be cancelled while it is already closing its stream.
        # Let that exact close finish within the original budget; requesting
        # cancellation alone neither proves cleanup nor makes completed cleanup
        # ambiguous. A second cancellation still fails closed.
        try:
            done, _ = await asyncio.wait({task}, timeout=max(
                0, deadline - asyncio.get_running_loop().time()))
            if not done:
                raise asyncio.TimeoutError()
            task.result()
        except BaseException:
            task.cancel()
            if runtime is not None:
                runtime.cleanup_complete = False
        raise
    except Exception:
        if runtime is not None:
            runtime.cleanup_complete = False
        return False
    finally:
        task.add_done_callback(_consume_result)


@asynccontextmanager
async def owned_stream(stream):
    """Close the exact iterator even when its consumer exits at a yielded item."""
    try:
        yield stream
    finally:
        close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
        if close is None:
            runtime = runtime_context.get()
            if runtime is not None:
                runtime.cleanup_complete = False
        else:
            try:
                closing = close()
            except BaseException:
                runtime = runtime_context.get()
                if runtime is not None:
                    runtime.cleanup_complete = False
                raise
            await complete_cleanup(closing)
