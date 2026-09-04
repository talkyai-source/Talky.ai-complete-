"""
Campaign Test WebSocket — talk to the REAL campaign agent from the browser.

This is NOT a demo agent. It reuses the exact telephony path a live phone
call takes:

    tenant AI-Options  ->  build_telephony_session_config(gateway_type="browser")
                       ->  VoiceOrchestrator.create_voice_session(config)
                       ->  BrowserMediaGateway  ->  this WebSocket

Because config is resolved through ``get_tenant_ai_config_resolver()`` (which is
cache-bypassed) and ``build_telephony_session_config``, whatever the tenant
picked in AI Options — cascaded vs realtime (gpt-realtime) pipeline, LLM, STT,
TTS provider/voice, persona, knowledge — is honored here identically to a real
call, and a change to AI Options takes effect on the very next connection.

The only per-call knob is first-speaker (``?first_speaker=agent|user``), the
same choice a real Start offers: agent-first greets immediately (realtime:
greet_on_start; cascaded: streamed greeting), caller-first waits for the user.

A test session DOES get a ``calls`` row, flagged ``is_test`` (Alembic 0017), so
that everything hanging off a call — recording, transcript, prompt version,
feedback voice note, conversation review — can actually be exercised from the
test button. Nothing bills it: every query that counts money, dial capacity or
abuse says ``AND NOT is_test`` explicitly, and ``billable_calls`` excludes it.

The row's id is deliberately ``voice_session.call_id``; see ``_record_test_call``
for why anything else silently loses the transcript.
"""

import json
import asyncio
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.core.security.rbac import (
    Permission,
    check_permission,
    get_effective_permissions,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Campaign Test"])


class CampaignTestDirectionConflict(RuntimeError):
    """The campaign became non-outbound before test-call persistence."""


class CampaignTestUnavailable(RuntimeError):
    """The durable test-call boundary could not be proven."""


# Small concurrency cap so a stuck test tab can't pin an unbounded number of
# realtime/TTS provider sockets. RFC 6455 close 1013 = "Try Again Later".
_MAX_CONCURRENT_TEST = 8
_test_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _test_semaphore
    if _test_semaphore is None:
        _test_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_TEST)
    return _test_semaphore


# ---------------------------------------------------------------------------
# Auth helpers — mirror assistant_ws.py: cookie first, first-frame fallback.
# ---------------------------------------------------------------------------

def _read_cookie_token(websocket: WebSocket) -> Optional[str]:
    """Read the ``talky_at`` HttpOnly cookie from the WS handshake."""
    raw = websocket.cookies.get("talky_at")
    if not raw:
        return None
    stripped = raw.strip()
    return stripped or None


async def _resolve_ws_token(websocket: WebSocket) -> Optional[str]:
    """Resolve the auth token without exposing it in the URL.

    1. ``talky_at`` HttpOnly cookie (preferred — same surface as REST).
    2. First frame ``{"type":"auth","token":"…"}`` for clients that can't
       carry the cookie (bearer-fallback mode). 5s wait after accept().
    """
    cookie_token = _read_cookie_token(websocket)
    if cookie_token:
        logger.info("campaign_test_ws auth surface=cookie")
        return cookie_token

    # WHICH SURFACE FAILED, NOT JUST THAT ONE DID (2026-08-23)
    #
    # "no auth frame within 5s" was the only signal this function emitted, and
    # it conflates two very different failures: the talky_at cookie never
    # arrived, or it arrived and was rejected. Three separate incidents (MFA
    # login not issuing talky_at, passkey login not issuing it, and the 15-min
    # expiry) all surfaced as that one line, and each took a log-dive to tell
    # apart. Cookie NAMES only — never a value, these are credentials.
    logger.info(
        "campaign_test_ws no talky_at on handshake; cookies_present=%s origin=%r "
        "— falling back to first-frame bearer",
        sorted(websocket.cookies.keys()) or "NONE",
        websocket.headers.get("origin"),
    )

    try:
        first_frame = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.info(
            "campaign_test_ws: no auth frame within 5s — neither cookie nor "
            "bearer frame; the client had no credential to offer"
        )
        return None
    except WebSocketDisconnect:
        return None
    except Exception as e:  # noqa: BLE001
        logger.info("campaign_test_ws: failed to parse first frame: %s", e)
        return None

    if not isinstance(first_frame, dict) or first_frame.get("type") != "auth":
        return None
    token = first_frame.get("token")
    if not isinstance(token, str) or not token.strip():
        return None
    logger.info("campaign_test_ws auth surface=bearer_frame")
    return token


def _is_origin_allowed(websocket: WebSocket) -> bool:
    """Reject cross-origin browser upgrades. Non-browser clients (no Origin)
    are allowed — they don't carry the browser cookie."""
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    from app.core.config import get_settings

    return origin in get_settings().allowed_origins


async def _resolve_user_tenant(db_pool, user_id: str) -> Optional[str]:
    """Resolve a signed JWT subject before the WS has a tenant context.

    The compatibility ``.table()`` adapter is tenant-scoped.  A WebSocket has
    not passed through TenantMiddleware, so using that adapter for this
    bootstrap query installs the nil tenant and hides the user's own profile.
    Use the trusted pooled path that REST authentication uses, with an explicit
    subject predicate and user audit context; the caller installs the returned
    tenant context immediately afterwards.

    ``None`` means the signed subject has no tenant-backed profile.  Database
    failures deliberately propagate so the endpoint can distinguish an auth
    miss from a temporary backend failure.
    """
    from app.core.db_utils import acquire_with_tenant

    async with acquire_with_tenant(db_pool, None, user_id=user_id) as conn:
        row = await conn.fetchrow(
            "SELECT tenant_id FROM user_profiles WHERE id = $1",
            user_id,
        )
    tenant_id = row.get("tenant_id") if row else None
    return str(tenant_id) if tenant_id else None


async def _fetch_campaign_row(db_pool, tenant_id: str, campaign_id: str):
    """Fetch one campaign row as a dict, scoped to ``tenant_id`` (IDOR guard).

    Returns ``None`` on a miss or on any error, and the caller then refuses the
    connection with 1008 "Campaign not found" — a tenant can never open a test
    session against somebody else's campaign.

    2026-08-27: this used to be imported from
    ``app.domain.services.telephony.lifecycle``, but the inbound refactor moved
    that path onto a pinned admission snapshot and deleted the helper, leaving
    this endpoint importing a symbol that no longer exists (ImportError on every
    connection). The query is reproduced here, where its only caller lives.
    """
    if db_pool is None:
        return None
    try:
        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(db_pool, None) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM campaigns WHERE id = $1 AND tenant_id = $2",
                campaign_id, tenant_id,
            )
        return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning(
            "campaign_test_campaign_fetch_failed tenant=%s campaign=%s err=%s",
            str(tenant_id)[:8], str(campaign_id)[:8], exc,
        )
        return None


async def _record_test_call(
    container, tenant_id, campaign_id, voice_session, config
) -> str:
    """Write a flagged ``calls`` row for a campaign test session.

    Why a row at all: recordings, transcripts, the prompt version on the call,
    feedback voice notes and conversation reviews all reference ``calls(id)``.
    Without one, none of them can be exercised from the test button, which
    defeats the purpose of having a test button.

    Why it is safe: ``is_test`` is TRUE, and every query that counts money,
    dial capacity or abuse excludes it explicitly (Alembic 0017 lists them).
    A test session is billed nothing, consumes no dial slot, burns no lead
    attempt and cannot trip abuse detection.

    The campaign row lock and insert share the tenant-aware transaction opened
    by ``acquire_with_tenant``. This serializes against outbound-to-inbound
    conversion: a test must never continue when its durable row was refused.
    """
    pool = getattr(container, "db_pool", None)
    if pool is None:
        raise CampaignTestUnavailable("campaign-test database pool is unavailable")
    try:
        from app.core.db_utils import acquire_with_tenant

        # THE ROW ID MUST BE THE VOICE-SESSION ID (2026-08-23)
        #
        # Minting a fresh uuid4 here is what stopped transcripts appearing.
        # turn_ender flushes each turn with
        # `UPDATE calls ... WHERE id = <target>`, and for a browser session
        # `_resolve_transcript_target_call_id` deliberately returns None (this
        # session is not in the telephony map), so the target falls back to
        # `session.call_id`. Against an unrelated random row id that matched
        # ZERO rows, every turn, with no error raised — precisely the defect
        # call_transcript_persister.py was written to fix for outbound calls,
        # reintroduced here from the other direction.
        call_uuid = str(getattr(voice_session, "call_id", "") or uuid.uuid4())
        talklee_id = getattr(voice_session, "talklee_call_id", None) or call_uuid
        call_uuid_value = uuid.UUID(call_uuid)
        tenant_uuid = uuid.UUID(str(tenant_id))
        campaign_uuid = uuid.UUID(str(campaign_id))
        async with acquire_with_tenant(pool, str(tenant_id)) as conn:
            campaign = await conn.fetchrow(
                """
                SELECT direction
                  FROM campaigns
                 WHERE id = $1
                   AND tenant_id = $2
                   FOR SHARE
                """,
                campaign_uuid,
                tenant_uuid,
            )
            if campaign is None:
                raise CampaignTestUnavailable(
                    "campaign disappeared before test-call persistence"
                )
            direction = str(campaign.get("direction", "")).strip().lower()
            if direction != "outbound":
                raise CampaignTestDirectionConflict(
                    "campaign is no longer outbound"
                )
            await conn.execute(
                """
                INSERT INTO calls (
                    id, tenant_id, campaign_id, phone_number, status, direction,
                    talklee_call_id, is_test,
                    prompt_template, prompt_version, prompt_hash, created_at
                ) VALUES ($1,$2,$3,$4,'in_progress','outbound',$5,TRUE,$6,$7,$8,NOW())
                """,
                call_uuid_value,
                tenant_uuid,
                campaign_uuid,
                "browser-test",
                str(talklee_id),
                getattr(config, "prompt_template", None),
                getattr(config, "prompt_version", None),
                getattr(config, "prompt_hash", None),
            )
        logger.info(
            "campaign_test_call_recorded call=%s campaign=%s prompt=%s — flagged "
            "is_test, excluded from billing, concurrency and abuse",
            call_uuid[:8], str(campaign_id)[:8],
            getattr(config, "prompt_version", None),
        )
        return call_uuid
    except CampaignTestDirectionConflict:
        logger.info(
            "campaign_test_call_direction_conflict campaign=%s",
            str(campaign_id)[:8],
        )
        raise
    except CampaignTestUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — normalize DB uncertainty below
        if getattr(exc, "constraint_name", None) in {
            "calls_outbound_campaign_guard",
            "calls_test_outbound_campaign_guard",
        }:
            logger.info(
                "campaign_test_call_direction_constraint campaign=%s",
                str(campaign_id)[:8],
            )
            raise CampaignTestDirectionConflict(
                "campaign is no longer outbound"
            ) from exc
        logger.warning("campaign_test_call_record_failed campaign=%s",
                       str(campaign_id)[:8], exc_info=True)
        raise CampaignTestUnavailable(
            "test-call persistence could not be confirmed"
        ) from exc


async def _finalise_test_call(container, tenant_id, call_id, started_at) -> None:
    """Close the test row out so it looks like a finished call in the UI.

    Duration is recorded because the call detail page shows it — it is NOT
    billed, because minutes_quota sums only rows where NOT is_test.
    """
    if not call_id:
        return
    pool = getattr(container, "db_pool", None)
    if pool is None:
        return
    try:
        from app.core.db_utils import acquire_with_tenant

        seconds = max(0, int(time.time() - started_at))
        async with acquire_with_tenant(pool, str(tenant_id)) as conn:
            await conn.execute(
                """UPDATE calls
                      SET status = 'completed',
                          duration_seconds = $2,
                          ended_at = NOW()
                    WHERE id = $1""",
                uuid.UUID(str(call_id)), seconds,
            )
        logger.info("campaign_test_call_finalised call=%s seconds=%s (not billed)",
                    str(call_id)[:8], seconds)
    except Exception:  # noqa: BLE001
        logger.warning("campaign_test_call_finalise_failed call=%s",
                       str(call_id)[:8], exc_info=True)


async def _persist_test_transcript(voice_session, tenant_id, call_id, container) -> None:
    """Write the test session's transcript to ``calls`` + ``transcripts``.

    A phone call gets this from lifecycle's teardown. A browser test session
    calls ``orchestrator.end_session()`` directly and never passes through that
    path, so nothing was ever written and the call detail page showed an empty
    transcript no matter how long you talked.

    ``save_call_transcript_on_hangup`` returns early unless ``_dialer_call_id``
    is set. That binding normally comes from ``bind_telephony_call``'s
    ``external_call_uuid`` lookup, which has no meaning for a browser session —
    there is no PBX channel. Setting it explicitly is how you aim the persist at
    a row you already know the id of.

    This writes the ``transcripts`` row specifically: ``GET
    /calls/{id}/transcript`` reads that table FIRST and only falls back to
    ``calls.transcript``, so without it the richer view (turns, word counts)
    stays empty even once the incremental flush starts landing.

    Never raises — a missing transcript must not break teardown.
    """
    try:
        from app.services.scripts.call_transcript_persister import (
            save_call_transcript_on_hangup,
        )

        pipeline = getattr(voice_session, "pipeline", None)
        transcript_service = getattr(pipeline, "transcript_service", None)
        if transcript_service is None:
            # Realtime sessions have no cascaded pipeline; the bridge stashes
            # its TranscriptService on the session itself.
            transcript_service = getattr(voice_session, "transcript_service", None)
        if transcript_service is None:
            logger.info(
                "campaign_test_transcript_skipped call=%s — no transcript service",
                str(call_id)[:8],
            )
            return

        voice_session._dialer_call_id = str(call_id)
        voice_session._dialer_tenant_id = str(tenant_id)
        await save_call_transcript_on_hangup(
            voice_session=voice_session,
            transcript_service=transcript_service,
            db_pool=container.db_pool if container.is_initialized else None,
        )
        logger.info("campaign_test_transcript_persisted call=%s", str(call_id)[:8])
    except Exception:  # noqa: BLE001 — see docstring
        logger.warning(
            "campaign_test_transcript_failed call=%s", str(call_id)[:8], exc_info=True
        )


@router.websocket("/ws/campaign-test/{campaign_id}")
async def campaign_test_websocket(
    websocket: WebSocket,
    campaign_id: str,
    first_speaker: str = Query(
        "agent",
        description="'agent' (agent greets first) or 'user' (caller speaks first)",
    ),
    allow_barge_in: bool = Query(
        False,
        description=(
            "Keep barge-in on for this browser session. OFF by default: without "
            "a carrier's echo cancellation the microphone hears the agent's own "
            "speech and cuts it off mid-sentence every turn. Turn this on only "
            "when wearing headphones."
        ),
    ),
):
    """Browser WebSocket that runs the real agent for ``campaign_id``.

    Same transport contract as ``/ws/ask-ai`` (binary PCM16 both ways + JSON
    control frames), but the agent is the tenant's live campaign agent.
    """
    # Reject cross-origin upgrades BEFORE accepting.
    if not _is_origin_allowed(websocket):
        origin = websocket.headers.get("origin")
        logger.warning("campaign_test_ws: rejecting cross-origin upgrade from %r", origin)
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    await websocket.accept()

    sem = _get_semaphore()
    if sem._value == 0:
        await websocket.send_json({"type": "error", "message": "Server at capacity, please retry shortly"})
        await websocket.close(code=1013)
        return

    # ── Auth: resolve token → user → tenant ─────────────────────────────
    resolved_token = await _resolve_ws_token(websocket)
    if not resolved_token:
        # ``code`` is what the client retries on. It cannot use the 1008 close
        # code: this frame is sent BEFORE the close, so the browser's onmessage
        # fires first and the socket is already "accepted" by the time onclose
        # runs. Matching on the message TEXT would break the moment anyone
        # rewords it, so the contract is this stable slug.
        await websocket.send_json({
            "type": "error",
            "code": "auth_required",
            "message": "Your session has expired. Reload the page and sign in again.",
        })
        await websocket.close(code=1008, reason="Missing auth")
        return

    from app.core.jwt_security import JWTValidationError, decode_and_validate_token

    try:
        payload = decode_and_validate_token(resolved_token)
    except JWTValidationError as jwt_err:
        logger.info("campaign_test_ws: token verification failed: %s", jwt_err.detail)
        # Same slug: a token that arrived but is expired is the same problem
        # from the user's side, and the same refresh-and-retry fixes it.
        await websocket.send_json({
            "type": "error",
            "code": "auth_required",
            "message": "Your session has expired. Reload the page and sign in again.",
        })
        await websocket.close(code=1008, reason="Invalid token")
        return

    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        await websocket.send_json({"type": "error", "message": "Invalid token: missing subject."})
        await websocket.close(code=1008, reason="Invalid token")
        return

    from app.api.v1.dependencies import get_db_client

    try:
        db_client = get_db_client()
        tenant_id = await _resolve_user_tenant(db_client.pool, user_id)
    except Exception:  # noqa: BLE001 — return a stable, non-sensitive WS error
        logger.error("campaign_test_ws: profile lookup failed", exc_info=True)
        await websocket.send_json({
            "type": "error",
            "code": "profile_lookup_failed",
            "message": "Unable to load your user profile. Please try again.",
        })
        await websocket.close(code=1011, reason="Profile lookup failed")
        return

    if not tenant_id:
        await websocket.send_json({
            "type": "error",
            "code": "profile_not_found",
            "message": "User profile not found.",
        })
        await websocket.close(code=1008, reason="No tenant")
        return

    # RLS tenant context for any .table() lookups on this task.
    from app.core.security.tenant_isolation import set_current_tenant_id

    set_current_tenant_id(tenant_id)

    # ── Fetch the campaign row, scoped to this tenant (IDOR guard) ───────
    from app.core.container import get_container

    container = get_container()
    if not container.is_initialized:
        await websocket.send_json({"type": "error", "message": "Backend not ready."})
        await websocket.close(code=1011, reason="Container not initialized")
        return

    # WebSockets do not run FastAPI's HTTP dependency chain. Resolve effective
    # grants explicitly so a read-only or subsequently revoked user cannot
    # consume provider capacity or persist test calls/transcripts.
    try:
        permissions = await get_effective_permissions(
            container.db_pool,
            user_id,
            tenant_id,
        )
    except Exception as permission_err:  # noqa: BLE001 — authorization fails closed
        logger.error(
            "campaign_test_ws: permission lookup failed tenant=%s user=%s err_type=%s",
            str(tenant_id)[:8],
            str(user_id)[:8],
            type(permission_err).__name__,
        )
        await websocket.send_json(
            {
                "type": "error",
                "code": "authorization_unavailable",
                "message": "Authorization is temporarily unavailable. Please retry.",
            }
        )
        await websocket.close(code=1011, reason="Authorization unavailable")
        return
    if not check_permission(permissions, Permission.CAMPAIGNS_UPDATE):
        await websocket.send_json(
            {
                "type": "error",
                "code": "permission_denied",
                "required": Permission.CAMPAIGNS_UPDATE.value,
                "message": "You do not have permission to test this campaign.",
            }
        )
        await websocket.close(code=1008, reason="Permission denied")
        return

    campaign_row = await _fetch_campaign_row(container.db_pool, tenant_id, campaign_id)
    if campaign_row is None:
        await websocket.send_json({"type": "error", "message": "Campaign not found."})
        await websocket.close(code=1008, reason="Campaign not found")
        return
    if str(campaign_row.get("direction", "outbound")).strip().lower() != "outbound":
        await websocket.send_json(
            {
                "type": "error",
                "code": "inbound_campaign_managed_separately",
                "message": (
                    "Inbound campaigns must be tested through the inbound "
                    "campaign lifecycle."
                ),
                "campaign_ids": [str(campaign_id)],
            }
        )
        await websocket.close(code=1008, reason="Inbound campaign")
        return

    # ── Direction and opening mode are two facts. This tests an OUTBOUND
    #    campaign, full stop; first_speaker only decides who opens. Chosen
    #    BEFORE create_voice_session so the realtime bridge is built with the
    #    correct greet_on_start. ────────────────────────────────────────────
    from app.domain.services.voice_orchestrator import (
        Direction,
        opening_mode_from_first_speaker,
    )
    from app.domain.services.telephony_session_config import (
        build_telephony_session_config,
    )
    from app.domain.services.tenant_ai_config_resolver import (
        get_tenant_ai_config_resolver,
    )
    from app.domain.services.voice_tuning import get_voice_tuning_resolver

    fs = "user" if (first_speaker or "").strip().lower() == "user" else "agent"
    direction = Direction.OUTBOUND
    opening_mode = opening_mode_from_first_speaker(fs)

    logger.info(
        "campaign_test_ws start campaign=%s tenant=%s direction=%s opening_mode=%s "
        "first_speaker=%s allow_barge_in=%s",
        str(campaign_id)[:8], str(tenant_id)[:8], direction.value, opening_mode,
        fs, allow_barge_in,
    )

    voice_session = None
    receiver_task: Optional[asyncio.Task] = None
    test_call_id = None
    test_started_at = time.time()

    async with sem:
        try:
            # Resolve the tenant's LIVE AI Options exactly like a phone call.
            # These resolvers are cache-bypassed, so an AI-Options edit takes
            # effect on the next connection (requirement: test agent reacts to
            # AI Options).
            ai_cfg = await get_tenant_ai_config_resolver().for_tenant_async(tenant_id)
            vt = await get_voice_tuning_resolver().for_tenant_async(tenant_id)

            config = build_telephony_session_config(
                gateway_type="browser",
                campaign=campaign_row,
                direction=direction,
                opening_mode=opening_mode,
                ai_config_override=ai_cfg,
                voice_tuning_override=vt,
                allow_browser_barge_in=allow_barge_in,
            )

            orchestrator = container.voice_orchestrator
            voice_session = await orchestrator.create_voice_session(config)

            # ── Give the test call a real row (Alembic 0017) ──────────────
            # Without one, nothing that hangs off a call could be tested from
            # here: recordings, transcripts, the prompt version on the row,
            # feedback voice notes and conversation reviews all reference
            # calls(id). The row is flagged is_test, and every query that
            # counts money, dial capacity or abuse excludes it explicitly —
            # minutes_quota, billing, dialer concurrency, the per-lead daily
            # cap, all five abuse checks and telephony observability.
            #
            # Created AFTER the session exists. A final locked direction check
            # prevents a conversion race from leaving an outbound browser
            # session running without an accepted outbound test-call row.
            try:
                test_call_id = await _record_test_call(
                    container, tenant_id, campaign_id, voice_session, config
                )
            except CampaignTestDirectionConflict:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "inbound_campaign_managed_separately",
                        "message": (
                            "Inbound campaigns must be tested through the inbound "
                            "campaign lifecycle."
                        ),
                        "campaign_ids": [str(campaign_id)],
                    }
                )
                await websocket.close(code=1008, reason="Inbound campaign")
                return
            except CampaignTestUnavailable:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "campaign_test_unavailable",
                        "message": (
                            "Campaign testing is temporarily unavailable. "
                            "Please try again."
                        ),
                    }
                )
                await websocket.close(code=1011, reason="Campaign test unavailable")
                return

            # Per-call first-speaker on the session (phone path sets both).
            # Also opt this test call into the natural silence handling so it
            # behaves like a real call: a gentle "Hello?" after ~10s of caller
            # silence, and auto-close after 60s. (Real phone calls get this by
            # gateway_type; the browser test call opts in explicitly.)
            try:
                voice_session._first_speaker = fs
                voice_session._enable_silence_monitor = True
                _cs = getattr(voice_session, "call_session", None)
                if _cs is not None:
                    _cs._first_speaker = fs
                    _cs._enable_silence_monitor = True
            except Exception:  # noqa: BLE001
                pass

            call_id = voice_session.call_id
            gateway = voice_session.media_gateway
            is_realtime = getattr(voice_session, "realtime_bridge", None) is not None

            # Callee-first cascaded: make sure the callee-speaks-first directive
            # leads the prompt. opening_mode="callee_first" already composed it,
            # so this is an idempotent no-op belt-and-braces — matches the
            # phone path. It is NOT inbound framing: the directive itself says
            # "OUTBOUND CALL — CALLEE SPEAKS FIRST".
            if not is_realtime and fs == "user":
                try:
                    from app.domain.services.telephony.modes.caller_first import (
                        select_inbound_base_prompt,
                    )

                    select_inbound_base_prompt(voice_session)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("campaign_test_ws inbound prompt swap failed: %s", exc)

            # ── Campaign knowledge — the same call prewarm makes for a phone
            #    call (prewarm.py, "Campaign knowledge" block). This endpoint
            #    skips prewarm, and until 2026-09-02 nothing else injected the
            #    knowledge base, so the test agent knew nothing about the
            #    company while the docstring above promised parity. Fail-soft,
            #    same as prewarm: a knowledge failure must never stop a test.
            try:
                from app.services.scripts.knowledge.session_inject import (
                    apply_campaign_knowledge,
                )

                await apply_campaign_knowledge(
                    getattr(voice_session, "call_session", None),
                    campaign_row,
                    pool=container.db_pool,
                )
                logger.info(
                    "campaign_test_knowledge_applied campaign=%s mode=%s",
                    str(campaign_id)[:8],
                    getattr(getattr(voice_session, "call_session", None), "knowledge_mode", None),
                )
            except Exception as _kb_exc:  # noqa: BLE001
                logger.warning(
                    "campaign_test_knowledge_inject_failed campaign=%s err=%s",
                    str(campaign_id)[:8], _kb_exc,
                )

            # Rates come off the gateway AFTER create — realtime forces 8 kHz.
            out_rate = getattr(gateway, "_sample_rate", config.gateway_sample_rate)
            in_rate = getattr(gateway, "_input_sample_rate", out_rate)

            await websocket.send_json(
                {
                    "type": "ready",
                    "call_id": call_id,
                    "campaign_id": str(campaign_id),
                    "sample_rate": out_rate,
                    "input_sample_rate": in_rate,
                    "audio_format": "s16le",
                    "pipeline_mode": "realtime" if is_realtime else "cascaded",
                    "first_speaker": fs,
                }
            )

            async def _receive_messages() -> None:
                while gateway.is_session_active(call_id):
                    try:
                        message = await asyncio.wait_for(websocket.receive(), timeout=30.0)
                        message_type = message.get("type")

                        if message_type == "websocket.disconnect":
                            break
                        if message_type != "websocket.receive":
                            continue

                        audio_data = message.get("bytes")
                        if isinstance(audio_data, (bytes, bytearray)):
                            if not audio_data:
                                continue
                            await gateway.on_audio_received(call_id, bytes(audio_data))
                            continue

                        text_data = message.get("text")
                        if not text_data:
                            continue
                        try:
                            data = json.loads(text_data)
                        except json.JSONDecodeError:
                            continue
                        if data.get("type") == "end_call":
                            await gateway.on_call_ended(call_id, "user_ended")
                            break
                        if data.get("type") == "playback_complete":
                            mark = getattr(gateway, "mark_playback_complete", None)
                            if callable(mark):
                                mark(call_id)
                            continue
                        # {"type":"auth"} frames (sent by cookie-mode clients that
                        # also send a bearer frame) are ignored here.

                    except asyncio.TimeoutError:
                        try:
                            await websocket.send_json({"type": "heartbeat"})
                        except (WebSocketDisconnect, RuntimeError):
                            break
                        continue
                    except WebSocketDisconnect:
                        break
                    except RuntimeError as e:
                        if "disconnect message has been received" in str(e):
                            break
                        raise

            # ── Run the leg — the ONE branch that differs from Ask AI ───────
            if is_realtime:
                # Realtime: wire the browser transport, then let the bridge pump
                # caller audio -> model and model audio -> gateway. greet_on_start
                # (set at bridge-build time from Direction) owns the greeting.
                await gateway.on_call_started(call_id, {"websocket": websocket})
                voice_session.pipeline_task = asyncio.create_task(
                    voice_session.realtime_bridge.run()
                )
                receiver_task = asyncio.create_task(_receive_messages())
            else:
                # Cascaded: start_pipeline calls on_call_started + runs STT/LLM/TTS.
                await orchestrator.start_pipeline(voice_session, websocket)
                receiver_task = asyncio.create_task(_receive_messages())
                if fs == "agent":
                    from app.domain.services.telephony.config import (
                        _build_outbound_greeting,
                    )
                    from app.domain.models.conversation import Message, MessageRole

                    greeting = _build_outbound_greeting(voice_session)
                    await orchestrator.send_greeting(voice_session, greeting, websocket)
                    # Seed history so the LLM doesn't re-greet on the first turn.
                    try:
                        voice_session.call_session.conversation_history.append(
                            Message(role=MessageRole.ASSISTANT, content=greeting)
                        )
                    except Exception:  # noqa: BLE001
                        pass
                # caller-first: send nothing; the pipeline reacts to the first turn.

            await receiver_task

        except WebSocketDisconnect:
            logger.info("campaign_test_ws disconnected campaign=%s", str(campaign_id)[:8])
        except Exception as e:  # noqa: BLE001
            logger.error("campaign_test_ws error: %s", e, exc_info=True)
            try:
                await websocket.send_json({"type": "error", "message": str(e)})
                await websocket.close(code=1011, reason="server error")
            except Exception:  # noqa: BLE001
                pass
        finally:
            if receiver_task and not receiver_task.done():
                receiver_task.cancel()
                try:
                    await receiver_task
                except asyncio.CancelledError:
                    pass
            # BEFORE teardown, like the phone path: end_session cancels the
            # pipeline, and the transcript buffer lives on that pipeline's
            # transcript_service. Persist first or there is nothing left to read.
            if voice_session and test_call_id:
                await _persist_test_transcript(
                    voice_session, tenant_id, test_call_id, container
                )
            if voice_session:
                await container.voice_orchestrator.end_session(voice_session)
            # Close the flagged row out so the call detail page shows a finished
            # call you can play, review and leave a voice note on. The duration
            # is recorded for display only — minutes_quota sums NOT is_test.
            await _finalise_test_call(
                container, tenant_id, test_call_id, test_started_at
            )
            logger.info("campaign_test_ws session ended campaign=%s", str(campaign_id)[:8])
