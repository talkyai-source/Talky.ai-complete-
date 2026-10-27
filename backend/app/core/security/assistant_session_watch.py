"""Bounded independent authority checks for an authenticated assistant socket.

Uses the existing campaign-test polling pattern, with a fixed identity and a
bounded DB check. No event bus or tenant-wide cancellation is involved.
"""
import asyncio

from app.core.db_utils import acquire_with_tenant
from app.core.security.principal import PrincipalUnavailable, load_session_principal

AUTH_RECHECK_SECONDS = 15.0
AUTH_CHECK_TIMEOUT_SECONDS = 3.0
CLOSE_TIMEOUT_SECONDS = 1.0


class AssistantSessionWatch:
    def __init__(self, websocket, pool, claims):
        self.websocket, self.pool = websocket, pool
        self.claims = dict(claims)
        self.owner = asyncio.current_task()
        self.terminated = False
        # Diagnostic only: sending a close frame is never peer acknowledgement.
        self.close_frame_status = "not_attempted"
        self.stopping = False
        self.task = asyncio.create_task(self._run(), name="assistant-session-authority")

    async def _check(self):
        async with acquire_with_tenant(self.pool, None) as conn:
            await load_session_principal(conn, self.claims)

    async def _run(self):
        while True:
            await asyncio.sleep(AUTH_RECHECK_SECONDS)
            try:
                await asyncio.wait_for(self._check(), AUTH_CHECK_TIMEOUT_SECONDS)
            except Exception as exc:
                self.terminated = True
                try:
                    await asyncio.wait_for(self.websocket.close(
                        code=1008 if isinstance(exc, PrincipalUnavailable) else 1011,
                        reason="Session unavailable"), CLOSE_TIMEOUT_SECONDS)
                    self.close_frame_status = "sent"
                except asyncio.TimeoutError:
                    self.close_frame_status = "timed_out"
                except asyncio.CancelledError:
                    self.close_frame_status = "cancelled"
                    raise
                except Exception:
                    self.close_frame_status = "failed"
                finally:
                    # Closing alone does not interrupt an idle receive or an
                    # in-flight token stream on every ASGI implementation.
                    if not self.stopping and self.owner is not None and not self.owner.done():
                        self.owner.cancel()
                return

    async def stop(self):
        self.stopping = True
        if not self.task.done():
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
