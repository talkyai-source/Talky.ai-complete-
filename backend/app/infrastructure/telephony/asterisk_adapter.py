"""
Asterisk implementation of the generic CallControlAdapter interface.

Architecture
------------
Asterisk (B2BUA)
  └── ARI (Asterisk REST Interface) ─── controls channels / bridges
       └── ExternalMedia channel ─────── RTP to/from C++ Voice Gateway
            └── C++ Voice Gateway ─────── sends audio chunks to backend via HTTP callback
                                          receives TTS audio via POST /v1/sessions/{id}/tts

Audio path (inbound call → AI pipeline):
  Caller → Asterisk → ExternalMedia (UnicastRTP) → C++ Gateway (UDP)
         → POST /api/v1/sip/telephony/audio/{session_id} → VoicePipelineService (STT→LLM→TTS)
         → POST /v1/sessions/{session_id}/tts on C++ Gateway → Caller hears AI response

Call control path:
  AsteriskAdapter.originate_call()  ─→ ARI POST /channels
  AsteriskAdapter.hangup()          ─→ ARI DELETE /channels/{id}
  AsteriskAdapter.transfer()        ─→ supervised ARI target leg in bridge
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import random
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional
from urllib.parse import quote

import aiohttp

from app.domain.interfaces.call_control_adapter import CallControlAdapter
from app.domain.services.telephony.config import AUDIO_CALLBACK_BATCH_FRAMES

# How long after an interrupt an ACCEPTED TTS chunk is still attributed to the
# cancelled utterance rather than to the next legitimate turn.
#
# 0.75s is chosen from the measured turn budget, not picked round: after a
# barge-in the agent cannot legitimately speak again until the caller stops
# (Flux eot_timeout alone is 500ms), the LLM answers (p50 TTFT 788ms on
# 2026-08-13) and TTS returns its first chunk. The floor on a genuine next
# utterance is therefore comfortably above a second, so audio arriving inside
# 750ms of the interrupt is the old generation, not the new one.
#
# This is a MEASUREMENT boundary, never a gate — nothing is blocked or delayed
# by it. If the counters it feeds ever come back non-zero, the fix is to mint
# the post-rotation utterance id lazily at the start of the next turn instead
# of eagerly during the interrupt; until then, changing behaviour here would be
# guessing at a problem we have not shown exists.
_RESUME_WINDOW_S = 0.75

logger = logging.getLogger(__name__)


def _masked_number(value: Any) -> str:
    """Return a correlation-safe ANI/DID representation for logs."""
    text = "" if value is None else str(value).strip()
    if not text:
        return "-"
    tail = text[-4:]
    return f"***{tail}"


# Max lifetime of a C++ gateway media session. This was hardcoded to 300000 ms
# (5 min), which silently killed the agent's audio at exactly 5 minutes on every
# real call while the SIP channel + caller RTP stayed perfectly healthy — the
# caller heard dead air. There is no reason to cap a live answered call at 5 min;
# default to 2 hours (the gateway's own default) and make it env-tunable.
_SESSION_FINAL_TIMEOUT_MS = int(os.getenv("TELEPHONY_SESSION_FINAL_TIMEOUT_MS", "7200000"))

# ARI ``reason_code`` values are Q.850 causes. Asterisk maps these to the SIP
# responses callers and upstream carriers understand: 1 -> 404, 17 -> 486,
# 42 -> 5xx congestion (normally 503), and 21 -> 603. Keep the vocabulary
# explicit so a newly introduced admission reason cannot accidentally become
# retryable congestion and create a carrier retry storm.
_INBOUND_NOT_FOUND_REASONS = frozenset(
    {
        "invalid_did",
        "unknown_did",
        "ambiguous_did",
        "tenant_conflict",
        "did_not_verified",
    }
)
_INBOUND_BUSY_REASONS = frozenset({"max_active_calls_reached"})
_INBOUND_TRANSIENT_REASONS = frozenset(
    {
        "trunk_not_ready",
        "concurrency_policy_missing",
        "admission_timeout",
        "admission_callback_error",
        "admission_dependency_unavailable",
        "routing_dependency_unavailable",
        "callback_unavailable",
        "finalizer_unavailable",
        "answer_persist_unavailable",
        "empty_admission_decision",
        "incomplete_admission_decision",
        "transfer_runtime_unavailable",
    }
)


class TtsDeliveryError(RuntimeError):
    """Raised by send_tts_audio when a TTS packet could NOT be delivered to the
    caller (no live gateway session, or the gateway POST failed / timed out).

    Surfacing this as an exception — instead of swallowing it — lets the media
    gateway's send loop treat the packet as *not played*: it will not advance
    ``chunks_sent`` / ``total_bytes_sent`` / ``first_tts_logged`` for audio the
    caller never actually heard, so the transcript/history stop claiming a
    sentence was spoken while the caller sat in silence. Every call site in
    ``telephony_media_gateway`` already wraps ``send_tts_audio`` in try/except,
    so raising cannot crash the audio path.
    """


@dataclass
class _UnicastRtpCacheEntry:
    created_key: str
    remote_ip: Optional[str] = None
    remote_port: Optional[int] = None
    cached_at: float = 0.0


@dataclass
class _AsteriskTransferLeg:
    """One supervised blind-transfer target owned by a live parent call.

    The target is kept in the parent's existing mixing bridge.  The AI media
    leg is detached only after Asterisk reports that the target answered, so a
    busy/no-answer outcome leaves the caller connected to the agent and can be
    handled by the configured failure policy.
    """

    parent_id: str
    target_id: str
    bridge_id: str
    destination: str
    endpoint: str
    mode: str
    future: asyncio.Future
    connected: bool = False
    handoff_started: bool = False
    target_in_bridge: bool = False
    terminal_dispatched: bool = False
    # A failed/no-answer target is not publishable as terminal until ARI proves
    # that target absent.  Keeping the payload here lets the proof owner resolve
    # the public future exactly once, after it is safe for domain code to release
    # the linked-leg concurrency lease.
    pending_failure: Optional[Dict[str, Any]] = None
    # The provider ID committed before ARI create. Normally identical to
    # target_id; retained separately so a protocol-violating returned-ID
    # mismatch can clean the actual channel while settling the exact durable
    # leg identity.
    persisted_target_id: Optional[str] = None
    # The pre-created DB identity and the authoritative ARI identity are kept
    # separately when Asterisk returns a different channel id.  The actual id
    # is not dialled and cannot publish Answer until the rebind callback has
    # committed it to the exact transfer child.
    requested_target_id: Optional[str] = None
    provider_identity_rebound: bool = False
    answer_pending_identity_persist: bool = False
    # Metrics are tied to this state object so duplicate/reordered ARI events,
    # cleanup retries, and parent/target terminal races cannot double-count one
    # accepted provider attempt or its final outcome.
    metrics_started_at: float = 0.0
    metrics_attempt_recorded: bool = False
    metrics_terminal_recorded: bool = False


# Q.850 cause code → a snake-case string the outcome resolver recognises
# (see outcome_resolver._BUSY_CAUSES / _NO_ANSWER_CAUSES / _REJECT_CAUSES).
# Only the codes that change a dial outcome are mapped; everything else falls
# through to the resolver's session-state heuristic.
_Q850_CAUSE_TEXT: Dict[int, str] = {
    1: "unallocated_number",  # not active / invalid → INVALID/UNREACHABLE
    17: "user_busy",  # busy
    18: "no_user_response",  # no answer
    19: "no_answer",  # no answer (user alerted, no pickup)
    20: "no_answer",  # subscriber absent / phone off
    21: "call_rejected",  # declined
    22: "unallocated_number",  # number changed
    27: "destination_out_of_order",  # destination unreachable
    28: "unallocated_number",  # invalid number format
    34: "switch_congestion",  # no circuit available
    38: "switch_congestion",  # network out of order
    42: "switch_congestion",  # switching equipment congestion
    44: "switch_congestion",  # requested channel unavailable
}

# Only responses that prove the Answer operation could not have been applied
# may turn a durable answer intent back into a pre-answer release.  Throttling,
# timeout, conflict/precondition, and server errors can be emitted after an
# uncertain upstream commit and therefore remain manual-reconciliation holds.
_DEFINITIVE_ANSWER_REJECTION_STATUSES = frozenset({400, 401, 403, 404, 405})


class _AriResponseError(RuntimeError):
    """A definitive non-success response received from ARI.

    Transport errors remain ordinary exceptions because the request may have
    reached Asterisk.  Keeping response failures distinct lets the inbound
    Answer fence release a proven non-answer while holding an unknown outcome
    for carrier reconciliation.
    """

    def __init__(self, method: str, path: str, status: int, body: str) -> None:
        self.method = method
        self.path = path
        self.status = int(status)
        super().__init__(f"ARI {method} {path} → {status}: {body[:300]}")


class AsteriskAdapter(CallControlAdapter):
    """
    CallControlAdapter backed by Asterisk ARI + C++ Voice Gateway.

    Depends on:
      - Asterisk with ARI enabled (ari.conf, http.conf)
      - services/voice-gateway-cpp running on ASTERISK_GATEWAY_BASE_URL
    """

    def __init__(
        self,
        ari_host: str | None = None,
        ari_port: int | None = None,
        ari_username: str | None = None,
        ari_password: str | None = None,
        gateway_base_url: str | None = None,
        app_name: str | None = None,
        gateway_rtp_ip: str | None = None,
    ) -> None:
        self._ari_host = ari_host or os.getenv("ASTERISK_ARI_HOST", "127.0.0.1")
        self._ari_port = int(ari_port or os.getenv("ASTERISK_ARI_PORT", "8088"))
        self._ari_username = ari_username or os.getenv("ASTERISK_ARI_USER", "talky")
        self._ari_password = ari_password or os.getenv(
            "ASTERISK_ARI_PASSWORD", "talky_local_only_change_me"
        )
        if self._ari_password in ("talky_local_only_change_me", "talky", "admin", "password", ""):
            logger.warning(
                "AsteriskAdapter: ARI password is a known default — "
                "set ASTERISK_ARI_PASSWORD env var in production"
            )
        self._gateway_base_url = (
            gateway_base_url or os.getenv("ASTERISK_GATEWAY_BASE_URL", "http://127.0.0.1:18080")
        ).rstrip("/")
        self._app_name = app_name or os.getenv("ASTERISK_ARI_APP", "talky_ai")
        self._gateway_rtp_ip = gateway_rtp_ip or os.getenv("ASTERISK_GATEWAY_RTP_IP", "127.0.0.1")

        self._session: Optional[aiohttp.ClientSession] = None
        # Separate from _session on purpose. _session carries session-level
        # BasicAuth for ARI, and aiohttp refuses to send a per-request
        # Authorization header on a session that already has auth=
        # ("Cannot combine AUTHORIZATION header with AUTH argument or
        # credentials encoded in URL"). The C++ gateway authenticates with a
        # Bearer token, so it needs a session with no default credentials.
        self._gateway_session: Optional[aiohttp.ClientSession] = None
        self._connected_flag: bool = False
        self._ws_task: Optional[asyncio.Task] = None
        self._stop_event: asyncio.Event = asyncio.Event()

        # Active sessions: channel_id → session metadata dict
        self._active_sessions: Dict[str, Dict[str, Any]] = {}
        # channel_id → external_channel_id (UnicastRTP)
        self._ext_channels: Dict[str, str] = {}
        # channel_id → bridge_id
        self._bridges: Dict[str, str] = {}
        # channel_id → gateway session_id
        self._gateway_sessions: Dict[str, str] = {}
        # Outbound channels waiting for callee to answer:
        # channel_id → {"bridge_id": str, "listen_port": int, "session_id": str}
        self._pending_outbound: Dict[str, Dict[str, Any]] = {}
        # ChannelStateChange(Up) events that arrived before _on_outbound_stasis_start
        # ran (race condition when StasisStart is delayed in the ARI WebSocket queue).
        self._preemptive_up_channels: set = set()
        # Channel IDs originated by originate_call() — used as the primary
        # routing decision in StasisStart so we don't depend on Asterisk
        # reliably passing appArgs through PJSIP trunks.
        self._originated_channels: set[str] = set()
        self._originated_channel_order: list[str] = []

        # Q.850 / SIP hangup cause (as Asterisk's cause_txt string) captured
        # off the terminal ARI event, keyed by channel id. The outcome resolver
        # reads this (via get_hangup_cause) to tell no-answer / busy / rejected
        # apart from an agent-side hangup — without it every unanswered call was
        # mislabelled and never got its no-answer +24h reschedule. Bounded: an
        # entry is popped when consumed, and stale ones are cleaned on hangup.
        self._hangup_causes: Dict[str, str] = {}
        # Same-channel ChannelDestroyed is authoritative absence proof. Keep a
        # bounded handoff set so a preceding StasisEnd cleanup task can consume
        # the later proof instead of polling or timing out after already
        # claiming the terminal-event burst.
        self._destroyed_channel_ids: set[str] = set()
        # First authoritative parent-leg absence time. ChannelDestroyed records
        # it at event receipt; confirmation polling records it when inventory
        # first proves absence. Lifecycle consumes this frozen monotonic clock
        # before any gateway/bridge/provider cleanup can inflate billable time.
        self._terminal_at_monotonic: Dict[str, float] = {}
        # Wall-clock companion to the monotonic measurement. PostgreSQL needs
        # an absolute terminal timestamp that survives a worker crash; keeping
        # it beside the frozen duration boundary prevents recovery from ever
        # substituting its later restart time for the real PBX absence time.
        self._terminal_at_utc: Dict[str, str] = {}

        # Global event callbacks
        self._call_arrived_callbacks: Dict[str, Callable] = {}
        self._call_ended_callbacks: Dict[str, Callable] = {}
        # Generic new-call callback (used when call_id is not yet known at registration time)
        self._on_new_call: Optional[Callable] = None
        self._on_any_call_end: Optional[Callable] = None
        # Optional ringing-phase callback.  Fired once an outbound channel is
        # parked in the mixing bridge and is waiting for the callee to answer.
        # Used by the telephony bridge to pre-warm STT/TTS/LLM providers
        # during the 2–10 s of otherwise idle ring time, so that first-turn
        # latency after answer matches subsequent turns.
        self._on_ringing: Optional[Callable] = None
        # Optional EARLY-ringing callback. Fired on ChannelStateChange(Ringing)
        # — the carrier's 180, which arrives seconds BEFORE StasisStart on an
        # originated channel. Used purely for live-status transparency (the
        # UI's "Dialing" → "Ringing"); provider warmup stays on _on_ringing.
        self._on_early_ringing: Optional[Callable] = None
        self._early_ring_emitted: set[str] = set()
        # Channels whose call-end callback has been dispatched — prevents the
        # pre-Stasis terminal arm double-firing after StasisEnd already
        # dispatched teardown for the same channel (terminal events arrive in
        # bursts: ChannelHangupRequest + StasisEnd + ChannelDestroyed).
        self._end_dispatched: dict[str, None] = {}
        self._on_outbound_channel_alias: Optional[Callable] = None

        # Inbound admission runs while the PJSIP channel is still unanswered.
        # The callback is owned by the domain lifecycle and must return an
        # allowed/admitted decision before this adapter allocates any media.
        self._on_inbound_admission: Optional[Callable] = None
        self._on_inbound_rejection_persist: Optional[Callable] = None
        self._on_inbound_answered_persist: Optional[Callable] = None
        self._on_inbound_terminal_proof_persist: Optional[Callable] = None
        self._on_inbound_admission_finalize: Optional[Callable] = None
        self._inbound_admissions: Dict[str, Dict[str, Any]] = {}
        self._inbound_setup_inflight: set[str] = set()
        self._inbound_setup_tasks: Dict[str, asyncio.Task] = {}
        # Post-answer hand-off into the domain lifecycle.  Media setup can
        # finish several event-loop turns before lifecycle registers its local
        # VoiceSession, so ownership fencing must be able to cancel this exact
        # gap and transfer the locally-created ARI resources to cleanup.
        self._inbound_handoff_tasks: Dict[str, asyncio.Task] = {}
        self._inbound_handoff_accepted: set[str] = set()
        self._preanswer_hangup_tasks: Dict[str, asyncio.Task] = {}
        # PBX legs rejected before a durable inbound admission exists (for
        # example callback timeout, an uncorrelated trunk leg, or an orphaned
        # transfer target). They still need a tracked proof owner; a single
        # best-effort DELETE can leave a live/billable channel behind.
        self._unclaimed_hangup_tasks: Dict[str, asyncio.Task] = {}
        # Logical parent id -> the one task allowed to process a burst of
        # StasisEnd/ChannelDestroyed/ChannelHangupRequest events. Asterisk
        # commonly emits all three for one leg; without an atomic claim each
        # event could tear down resources and dispatch lifecycle settlement.
        self._terminal_cleanup_tasks: Dict[str, asyncio.Task] = {}
        # Channels whose admission must not be released by StasisEnd while a
        # setup-failure cleanup task is still proving deletion of every ARI
        # resource created after answer.
        self._inbound_cleanup_pending: set[str] = set()
        self._inbound_cleanup_retry_s = max(
            0.05,
            float(os.getenv("INBOUND_CLEANUP_RETRY_S", "2.0")),
        )
        self._inbound_admission_timeout_s = max(
            0.1,
            float(os.getenv("INBOUND_ADMISSION_TIMEOUT_S", "5.0")),
        )
        self._inbound_rejection_persist_timeout_s = max(
            0.1,
            float(os.getenv("INBOUND_REJECTION_PERSIST_TIMEOUT_S", "1.0")),
        )

        # Supervised transfer state.  A blind transfer is represented by a
        # second ARI channel in the existing bridge, rather than a redirect of
        # the caller channel.  That preserves the caller/AI conversation until
        # the target actually answers and gives us authoritative busy,
        # no-answer, and timeout outcomes.
        self._transfers_by_parent: Dict[str, _AsteriskTransferLeg] = {}
        self._transfers_by_target: Dict[str, _AsteriskTransferLeg] = {}
        # The generic telephony dashboard expects terminal attempts and
        # successes from the active adapter. Keep process-local cumulative
        # totals because completed Asterisk legs are deliberately removed from
        # the live ownership maps once cleanup is proven.
        self._transfer_metrics_terminal_attempts: int = 0
        self._transfer_metrics_successes: int = 0
        # Transfer setup and termination share this fence. A termination marks
        # the root synchronously, then waits for any in-flight ARI create/dial
        # section before snapshotting/deleting legs. This prevents a target
        # channel from being created immediately after absence was proved.
        self._transfer_setup_lock = asyncio.Lock()
        self._termination_fenced_call_ids: set[str] = set()
        self._transfer_handoff_tasks: Dict[str, asyncio.Task] = {}
        self._transfer_failure_cleanup_tasks: Dict[str, asyncio.Task] = {}
        self._on_transfer_connected: Optional[Callable] = None
        self._on_transfer_answered_persist: Optional[Callable] = None
        self._on_transfer_provider_identity_persist: Optional[Callable] = None
        self._on_transfer_cleanup_confirmed: Optional[Callable] = None
        self._transfer_answer_timeout_s = max(
            5.0,
            min(120.0, float(os.getenv("ASTERISK_TRANSFER_ANSWER_TIMEOUT_S", "30"))),
        )
        # Operator/runtime termination is not complete when ARI merely accepts
        # DELETE.  Keep the confirmation window inside the execution-plan's
        # five-second clean-hangup SLO and poll Asterisk's channel inventory for
        # authoritative absence of every owned human leg.
        self._hangup_confirm_timeout_s = max(
            0.1,
            min(5.0, float(os.getenv("ASTERISK_HANGUP_CONFIRM_TIMEOUT_S", "5.0"))),
        )
        self._hangup_confirm_poll_s = max(
            0.05,
            min(1.0, float(os.getenv("ASTERISK_HANGUP_CONFIRM_POLL_S", "0.1"))),
        )

        # RTP port allocator (32000–32999, matching Day 5 defaults)
        self._rtp_port_start = int(os.getenv("ASTERISK_RTP_PORT_START", "32000"))
        self._rtp_port_end = int(os.getenv("ASTERISK_RTP_PORT_END", "32999"))
        self._rtp_next = self._rtp_port_start
        self._rtp_in_use: set[int] = set()
        self._rtp_lock = asyncio.Lock()
        self._channel_varset_cache: Dict[tuple[str, str], _UnicastRtpCacheEntry] = {}
        self._channel_varset_cache_ttl_s = max(
            1.0,
            float(os.getenv("ASTERISK_CHANNELVARSET_CACHE_TTL_S", "120")),
        )

        # Per-call TTS error counters (suppresses log spam when Gateway session
        # is not running — logs first error and every 50th thereafter).
        self._tts_error_counts: Dict[str, int] = {}

        # Per-call TTS utterance identity (VG-13). Every /tts/play chunk is
        # stamped with the current utterance_id + a monotonically increasing
        # chunk_seq; interrupt_tts rotates the id, and the gateway rejects (409)
        # any straggler chunk still carrying the retired id — so a delayed HTTP
        # delivery can never re-speak audio from before a barge-in.
        #
        # THAT GUARANTEE HAS A GAP, AND WE NOW MEASURE IT (2026-08-18). It holds
        # for a chunk whose payload was already stamped before the rotation. A
        # chunk that enters send_tts_audio AFTER the rotation reads the FRESH id
        # from this same dict, so the gateway accepts it — the cancelled
        # generation would resume under a new identity, indistinguishable from
        # the next legitimate turn. Several layers are supposed to make that
        # impossible (the in-flight task is cancelled and awaited before the
        # rotation, and the Python send buffer is cleared first), but "supposed
        # to" was exactly the standard of evidence that lost two calls on
        # 2026-08-13. The counters below turn it into an observation. See
        # _RESUME_WINDOW_S and the interrupt_audio_audit line at call teardown.
        self._tts_utterances: Dict[str, Dict[str, Any]] = {}

        # Inbound ingress metadata + the durable admission snapshot are keyed
        # by PBX channel id.  Raw ANI/DID values are never emitted to logs.
        self._inbound_call_meta: Dict[str, Dict[str, Any]] = {}
        self._inbound_debug_dumped: bool = False

    def _track_originated_channel(self, channel_id: str) -> None:
        if not channel_id:
            return
        if channel_id not in self._originated_channels:
            self._originated_channel_order.append(channel_id)
        self._originated_channels.add(channel_id)

    def _discard_originated_channel(self, channel_id: str) -> None:
        self._originated_channels.discard(channel_id)
        try:
            self._originated_channel_order.remove(channel_id)
        except ValueError:
            pass

    def _consume_oldest_originated_channel(self) -> Optional[str]:
        while self._originated_channel_order:
            channel_id = self._originated_channel_order.pop(0)
            if channel_id in self._originated_channels:
                self._originated_channels.discard(channel_id)
                return channel_id
        if not self._originated_channels:
            return None
        channel_id = next(iter(self._originated_channels))
        self._originated_channels.discard(channel_id)
        return channel_id

    async def _correlate_trunk_leg(self, actual_channel_id: str) -> Optional[str]:
        """Pair a trunk-created Stasis leg with the SPECIFIC origination it
        belongs to, and consume that origination.

        Deterministic path: read ``CHANNEL(linkedid)`` off the leg. Because we
        originate with ``channelId=<pre_id>``, the origination channel's
        uniqueid — and therefore the whole call's linkedid — is that pre_id, so
        the linkedid returned here equals the pending origination id even though
        the leg's own id differs. This lets two calls dialing at once map to the
        right parents regardless of the order their legs enter Stasis.

        There is deliberately no FIFO fallback. A genuine inbound PJSIP call
        can arrive while outbound originations are pending; guessing the oldest
        pending id would then attach one caller's audio/session to another
        tenant's outbound call. An uncorrelated leg is rejected by the caller.

        Returns the origination id to alias to (already removed from the pending
        set), or ``None`` when there is nothing to consume.
        """
        linked: Optional[str] = None
        try:
            res = await self._ari(
                "GET",
                f"/channels/{actual_channel_id}/variable",
                params={"variable": "CHANNEL(linkedid)"},
            )
            linked = str((res or {}).get("value") or "") or None
        except Exception as exc:  # noqa: BLE001 — correlation failure is fail-closed
            logger.debug(
                "AsteriskAdapter: linkedid read failed for trunk leg %s: %s",
                actual_channel_id[:12],
                exc,
            )

        if linked and linked in self._originated_channels:
            self._discard_originated_channel(linked)
            return linked

        logger.error(
            "AsteriskAdapter: trunk leg %s could not be correlated to a "
            "pending origination (linkedid_present=%s) — refusing to guess",
            actual_channel_id[:12],
            bool(linked),
        )
        return None

    async def _start_trunk_leg(self, channel_id: str) -> None:
        """Correlate a trunk-created leg to its origination, then run the normal
        outbound-stasis setup. Runs as its own task because the correlation
        needs an async ARI read; the consume + alias happen here (post-read) so
        concurrent legs each claim their OWN origination."""
        pre_id = await self._correlate_trunk_leg(channel_id)
        if pre_id is None:
            self._schedule_unclaimed_hangup(
                channel_id,
                reason="uncorrelated_outbound_trunk_leg",
            )
            return
        self._emit_outbound_channel_alias(pre_id, channel_id)
        logger.info(
            f"AsteriskAdapter: matched trunk-created channel "
            f"{channel_id[:12]} to originated {pre_id[:12]}"
        )
        await self._on_outbound_stasis_start(channel_id)

    def get_hangup_cause(self, channel_id: str) -> Optional[str]:
        """Return (and consume) the captured Q.850 hangup cause for a channel.

        The lifecycle's call-ended hook calls this so the outcome resolver can
        classify no-answer / busy / rejected from the real PBX cause instead of
        defaulting to an agent-side hangup. Popped so the map stays bounded.
        """
        return self._hangup_causes.pop(channel_id, None)

    def _emit_outbound_channel_alias(self, original_call_id: str, actual_call_id: str) -> None:
        if (
            not self._on_outbound_channel_alias
            or not original_call_id
            or not actual_call_id
            or original_call_id == actual_call_id
        ):
            return
        try:
            result = self._on_outbound_channel_alias(original_call_id, actual_call_id)
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)
        except Exception as exc:
            logger.warning(
                "AsteriskAdapter: outbound channel alias callback failed "
                "original=%s actual=%s error=%s",
                original_call_id[:12],
                actual_call_id[:12],
                exc,
            )

    # ------------------------------------------------------------------
    # CallControlAdapter interface — identity
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "asterisk"

    @property
    def connected(self) -> bool:
        return self._connected_flag

    def get_transfer_metrics(self) -> Dict[str, Any]:
        """Expose Asterisk transfer state through the generic dashboard API.

        ``attempts`` follows the existing interface convention: it counts
        terminal attempts, so the dashboard success ratio never includes an
        operation that is still ringing or awaiting cleanup proof.
        """

        inflight = sum(
            1
            for leg in self._transfers_by_parent.values()
            if leg.metrics_attempt_recorded and not leg.metrics_terminal_recorded
        )
        return {
            "attempts": self._transfer_metrics_terminal_attempts,
            "successes": self._transfer_metrics_successes,
            "inflight": inflight,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, config: Optional[Dict[str, Any]] = None) -> None:
        if config:
            self._ari_host = config.get("ari_host", self._ari_host)
            self._ari_port = int(config.get("ari_port", self._ari_port))
            self._ari_username = config.get("ari_username", self._ari_username)
            self._ari_password = config.get("ari_password", self._ari_password)
            self._gateway_base_url = config.get("gateway_base_url", self._gateway_base_url)

        connector = aiohttp.TCPConnector()
        self._session = aiohttp.ClientSession(
            connector=connector,
            auth=aiohttp.BasicAuth(self._ari_username, self._ari_password),
        )

        # Verify ARI is reachable
        try:
            async with self._session.get(
                f"http://{self._ari_host}:{self._ari_port}/ari/asterisk/info",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status not in (200, 201):
                    raise RuntimeError(f"ARI info returned {resp.status}")
        except Exception as exc:
            await self._session.close()
            raise RuntimeError(f"AsteriskAdapter: cannot reach ARI: {exc}") from exc

        self._connected_flag = True
        self._stop_event.clear()

        # Start ARI WebSocket event listener
        self._ws_task = asyncio.create_task(self._ari_event_listener())
        logger.info(
            f"AsteriskAdapter connected to ARI at {self._ari_host}:{self._ari_port} "
            f"app={self._app_name}"
        )

    def owned_call_ids(self) -> set[str]:
        """Snapshot every locally-owned human-call root known to this adapter.

        Transfer targets are intentionally represented by their parent root;
        ``hangup_confirmed(parent)`` expands that root to every linked leg under
        one proof deadline. Setup/ringing channels are included even before a
        VoiceSession exists so shutdown cannot overlook the pre-lifecycle gap.
        """

        owned: set[str] = set()
        for mapping in (
            self._active_sessions,
            self._pending_outbound,
            self._inbound_admissions,
            self._inbound_call_meta,
            self._inbound_setup_tasks,
            self._inbound_handoff_tasks,
            self._preanswer_hangup_tasks,
            self._unclaimed_hangup_tasks,
        ):
            owned.update(str(value) for value in mapping if value)
        owned.update(str(value) for value in self._originated_channels if value)
        owned.update(str(value) for value in self._inbound_setup_inflight if value)
        owned.update(str(value) for value in self._inbound_cleanup_pending if value)
        owned.update(str(parent_id) for parent_id in self._transfers_by_parent if parent_id)
        return owned

    def recovery_excluded_channel_ids(self) -> set[str]:
        """Snapshot locally managed channel IDs an inverse scan must not adopt.

        ``owned_call_ids`` intentionally returns logical roots.  The inverse
        ARI reconciler also sees application-owned transfer and ExternalMedia
        channels, so include those physical IDs here along with narrow
        pre-dispatch race buffers.  A successor starts with empty maps and can
        still discover true crash orphans; a live owner never races its own
        setup/teardown work.
        """

        excluded = self.owned_call_ids()
        excluded.update(str(value) for value in self._ext_channels.values() if value)
        excluded.update(str(value) for value in self._transfers_by_target if value)
        excluded.update(str(value) for value in self._preemptive_up_channels if value)
        excluded.update(str(value) for value in self._gateway_sessions if value)
        return excluded

    def fence_ownership_loss(self) -> None:
        """Immediately disable ARI dispatch before asynchronous cleanup runs."""

        self._connected_flag = False
        self._stop_event.set()
        if self._ws_task is not None and not self._ws_task.done():
            self._ws_task.cancel()

    async def disconnect(
        self,
        *,
        drain_timeout_s: float = 5.5,
        force_handoff: bool = False,
    ) -> Dict[str, Any]:
        """Boundedly drain local cleanup owners and close ARI.

        ``force_handoff`` is used only during process shutdown after the durable
        Redis/DB obligations have been preserved. If proof cannot finish inside
        the bounded window, retry tasks are cancelled but their identity maps
        are deliberately retained for diagnostics and successor recovery.
        Ordinary callers receive an error and the adapter reconnects its event
        listener instead of silently abandoning live channels.
        """
        loop = asyncio.get_running_loop()
        drain_timeout_s = max(0.1, min(10.0, float(drain_timeout_s)))
        deadline = loop.time() + drain_timeout_s

        async def _await_tasks(
            tasks: list[asyncio.Task],
            *,
            cancel_first: bool = False,
        ) -> bool:
            pending = [task for task in dict.fromkeys(tasks) if not task.done()]
            if not pending:
                return True
            if cancel_first:
                for task in pending:
                    task.cancel()
            remaining = max(0.0, deadline - loop.time())
            if remaining <= 0:
                return False
            _done, still_pending = await asyncio.wait(pending, timeout=remaining)
            return not still_pending

        # Fence event dispatch first, but keep the ARI HTTP session alive until
        # every in-flight setup has transferred ownership to its cleanup task
        # and that cleanup has authoritatively removed all post-answer media.
        # Closing the session first would turn a graceful shutdown into an
        # untracked live caller/bridge plus a prematurely released reservation.
        self.fence_ownership_loss()

        setup_tasks = [task for task in self._inbound_setup_tasks.values() if not task.done()]
        await _await_tasks(setup_tasks, cancel_first=True)

        pending_handoff_tasks = [
            task
            for channel_id, task in self._inbound_handoff_tasks.items()
            if channel_id not in self._inbound_handoff_accepted and not task.done()
        ]
        accepted_handoff_tasks = [
            task
            for channel_id, task in self._inbound_handoff_tasks.items()
            if channel_id in self._inbound_handoff_accepted and not task.done()
        ]
        await _await_tasks(pending_handoff_tasks, cancel_first=True)
        # Once lifecycle explicitly accepts ownership, cancellation belongs to
        # lifecycle teardown. Let its finite initialization finish instead of
        # running adapter-only release against an already-registered session.
        await _await_tasks(accepted_handoff_tasks)

        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

        # Setup cancellation creates one of these tasks synchronously in its
        # CancelledError handler. Never cancel them: they own the admission,
        # RTP port and deterministic ARI resource IDs until deletion is proven.
        # Graceful shutdown waits fail-closed if ARI is unavailable instead of
        # reporting settlement while a caller may still be live.
        drained = True
        while loop.time() < deadline:
            cleanup_tasks = [
                task for task in self._preanswer_hangup_tasks.values() if not task.done()
            ]
            unclaimed_tasks = [
                task for task in self._unclaimed_hangup_tasks.values() if not task.done()
            ]
            terminal_tasks = [
                task for task in self._terminal_cleanup_tasks.values() if not task.done()
            ]
            transfer_handoff_tasks = [
                task for task in self._transfer_handoff_tasks.values() if not task.done()
            ]
            transfer_failure_tasks = [
                task for task in self._transfer_failure_cleanup_tasks.values() if not task.done()
            ]
            drain_tasks = list(
                dict.fromkeys(
                    cleanup_tasks
                    + unclaimed_tasks
                    + terminal_tasks
                    + transfer_handoff_tasks
                    + transfer_failure_tasks
                )
            )
            if not drain_tasks:
                break
            if not await _await_tasks(drain_tasks):
                drained = False
                break

        deferred_ids = self.owned_call_ids()
        if self._inbound_cleanup_pending:
            deferred_ids.update(self._inbound_cleanup_pending)
        for leg in self._transfers_by_parent.values():
            deferred_ids.add(leg.parent_id)
            deferred_ids.add(leg.target_id)

        if (not drained or deferred_ids) and not force_handoff:
            # Keep serving ARI events if an operator attempted a stop without
            # proving every owned channel absent. Setup already in cancellation
            # continues through its explicit cleanup owner.
            self._stop_event.clear()
            self._connected_flag = True
            if self._session is not None and (self._ws_task is None or self._ws_task.done()):
                self._ws_task = asyncio.create_task(self._ari_event_listener())
            pending = ",".join(sorted(value[:12] for value in deferred_ids))
            raise RuntimeError(
                "AsteriskAdapter: refusing to disconnect with unconfirmed "
                f"channel ownership: {pending or 'cleanup_task'}"
            )

        if force_handoff:
            retry_tasks = list(
                dict.fromkeys(
                    [
                        *self._preanswer_hangup_tasks.values(),
                        *self._unclaimed_hangup_tasks.values(),
                        *self._terminal_cleanup_tasks.values(),
                        *self._transfer_handoff_tasks.values(),
                        *self._transfer_failure_cleanup_tasks.values(),
                    ]
                )
            )
            for task in retry_tasks:
                if not task.done():
                    task.cancel()
            if retry_tasks:
                await asyncio.gather(*retry_tasks, return_exceptions=True)

        # Never resolve unfinished transfer futures as terminal merely because
        # this process is disconnecting. Their persisted leg/lease remains the
        # successor's cleanup obligation.
        if self._session:
            await self._session.close()
            self._session = None
        if self._gateway_session:
            await self._gateway_session.close()
            self._gateway_session = None
        summary = {
            "status": "deferred" if deferred_ids else "disconnected",
            "deferred": len(deferred_ids),
            "deferred_call_ids": sorted(deferred_ids),
        }
        logger.info(
            "AsteriskAdapter disconnected status=%s deferred=%d",
            summary["status"],
            summary["deferred"],
        )
        return summary

    async def health_check(self) -> bool:
        """Require both PBX control and a protocol-compatible media gateway."""
        try:
            connector = aiohttp.TCPConnector()
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(connector=connector) as sess:

                async def _ari_ok() -> bool:
                    async with sess.get(
                        f"http://{self._ari_host}:{self._ari_port}/ari/asterisk/info",
                        auth=aiohttp.BasicAuth(self._ari_username, self._ari_password),
                        timeout=timeout,
                    ) as resp:
                        return resp.status == 200

                async def _gateway_ok() -> bool:
                    async with sess.get(
                        f"{self._gateway_base_url}/health",
                        timeout=timeout,
                    ) as resp:
                        if resp.status != 200:
                            return False
                        try:
                            payload = await resp.json(content_type=None)
                        except Exception:
                            return False
                        return self._gateway_health_payload_is_compatible(payload)

                ari_ok, gateway_ok = await asyncio.gather(_ari_ok(), _gateway_ok())
                return bool(ari_ok and gateway_ok)
        except Exception:
            return False

    @staticmethod
    def _gateway_health_payload_is_compatible(payload: Any) -> bool:
        """Fail closed when controller and gateway protocols/codecs drift."""
        if not isinstance(payload, dict):
            return False
        codecs = payload.get("codecs")
        callback_versions = payload.get("callback_protocol_versions")
        return (
            payload.get("status") == "ok"
            and payload.get("io_loop_healthy") is True
            and payload.get("protocol_version") == 2
            and isinstance(codecs, list)
            and "pcmu" in codecs
            and isinstance(callback_versions, list)
            and 2 in callback_versions
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ari_url(self, path: str) -> str:
        return f"http://{self._ari_host}:{self._ari_port}/ari{path}"

    async def _ari(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        ok: tuple = (200, 201, 204),
        return_status: bool = False,
    ) -> Any:
        if not self._session:
            raise RuntimeError("AsteriskAdapter not connected")
        async with self._session.request(
            method,
            self._ari_url(path),
            params=params,
            json=json_body,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status not in ok:
                body = await resp.text()
                raise _AriResponseError(method, path, resp.status, body)
            try:
                payload = await resp.json(content_type=None)
            except Exception:
                payload = {}
            return (resp.status, payload) if return_status else payload

    async def list_active_channel_ids(self) -> Optional[set]:
        """Return the set of channel IDs Asterisk currently has up.

        Ground truth for the session watchdog's zombie reconcile: a local
        voice session whose call_id is NOT in this set corresponds to a
        channel Asterisk has already torn down — i.e. a ChannelDestroyed
        event we missed. Such a session must be force-ended so it releases
        its global concurrency slot (otherwise the slot leaks until a long
        inactivity timeout, and ~10 leaks block ALL outbound calls).

        Returns ``None`` (not an empty set) when ARI can't be queried, so
        the caller can distinguish "no channels" from "couldn't check" and
        skip the reconcile rather than wrongly tearing down live calls.
        """
        if not self._session:
            return None
        try:
            channels = await self._ari("GET", "/channels")
        except Exception as exc:  # noqa: BLE001
            logger.debug("ari_list_channels_failed err=%s", exc)
            return None
        if not isinstance(channels, list):
            return None
        ids: set = set()
        for ch in channels:
            cid = ch.get("id") if isinstance(ch, dict) else None
            if cid:
                ids.add(cid)
        return ids

    def gateway_session_map(self) -> Dict[str, str]:
        """Snapshot of channel_id → C++ gateway session_id for live media legs.

        A copy, so the watchdog reading it can never mutate the adapter's
        routing state.
        """
        return dict(self._gateway_sessions)

    async def list_active_gateway_session_ids(self) -> Optional[set]:
        """Return the session IDs the C++ media gateway says it is running.

        MEDIA ground truth, the mirror of ``list_active_channel_ids``'s SIP
        ground truth. A call whose channel is up but whose gateway session is
        gone (gateway restart/crash, a reaped session, a start that failed
        after the channel answered) is *silent dead air*: Asterisk still
        reports the channel live and bills it, every existing sweep votes
        healthy, and the caller hears nothing.

        Returns ``None`` (not an empty set) when the gateway can't be queried,
        so the caller can tell "no sessions" from "couldn't check" and skip the
        reconcile rather than tearing down every live call on a blip.
        """
        if not self._session:
            return None
        try:
            payload = await self._gateway("GET", "/v1/sessions")
        except Exception as exc:  # noqa: BLE001 — same contract as the ARI list
            logger.debug("gateway_list_sessions_failed err=%s", exc)
            return None
        sessions = payload.get("sessions") if isinstance(payload, dict) else None
        if not isinstance(sessions, list):
            return None
        ids: set = set()
        for entry in sessions:
            if not isinstance(entry, dict):
                continue
            sid = entry.get("session_id")
            state = str(entry.get("state") or "").strip().lower()
            # Failed/stopped sessions remain visible for evidence until the C++
            # reaper removes them. They are not live media and must not keep an
            # answered SIP call out of the dead-media watchdog.
            if sid and state in {"created", "starting", "buffering", "active", "degraded"}:
                ids.add(str(sid))
        return ids

    async def list_recoverable_application_channel_ids(self) -> Optional[set[str]]:
        """Return application-owned human channels eligible for inverse recovery.

        Redis-ledger recovery starts from local durable identity.  This inverse
        inventory closes the earlier crash window where Asterisk admitted a
        channel into this Stasis application but the process died before its
        first Redis write.  The application membership endpoint scopes the
        result to Talky; the channel inventory supplies names so ExternalMedia
        (``UnicastRTP/*``) legs are never mistaken for billable human roots.

        ``None`` is deliberately different from an empty set: any query or
        response-shape ambiguity makes the caller perform no recovery work.
        """

        if not self._session:
            return None
        try:
            application = await self._ari(
                "GET",
                f"/applications/{quote(self._app_name, safe='')}",
            )
            channels = await self._ari("GET", "/channels")
        except Exception as exc:  # noqa: BLE001 - inventory is fail-closed
            logger.warning("ari_application_channel_inventory_failed err=%s", exc)
            return None
        if not isinstance(application, dict) or not isinstance(channels, list):
            return None
        application_ids = application.get("channel_ids")
        if not isinstance(application_ids, list):
            return None

        channel_names: dict[str, str] = {}
        for channel in channels:
            if not isinstance(channel, dict):
                continue
            channel_id = str(channel.get("id") or "").strip()
            if channel_id:
                channel_names[channel_id] = str(channel.get("name") or "").strip()

        recoverable: set[str] = set()
        for raw_id in application_ids:
            channel_id = str(raw_id or "").strip()
            if not channel_id or channel_id not in channel_names:
                continue
            # This deployment admits human carrier legs through PJSIP. Local,
            # snoop, and ExternalMedia channels are internal plumbing and must
            # never become inverse-recovery roots merely because they share
            # the Stasis application.
            if not channel_names[channel_id].lower().startswith("pjsip/"):
                continue
            recoverable.add(channel_id)
        return recoverable

    async def _gateway(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        ok: tuple = (200,),
    ) -> Any:
        if not self._session:
            raise RuntimeError("AsteriskAdapter not connected")
        # Bearer auth for the C++ gateway control plane (VG-18). The gateway
        # requires "Authorization: Bearer <token>" on session/control endpoints;
        # sending the same env var from here keeps the pair in lockstep. The
        # production startup gates on both sides reject an unset token.
        headers: Optional[Dict[str, str]] = None
        token = os.getenv("VOICE_GATEWAY_AUTH_TOKEN", "").strip()
        if token:
            headers = {"Authorization": f"Bearer {token}"}
        # Must NOT reuse self._session: it was built with
        # auth=aiohttp.BasicAuth(...) for ARI, and aiohttp raises
        # "Cannot combine AUTHORIZATION header with AUTH argument or
        # credentials encoded in URL" as soon as a request on that session also
        # carries an Authorization header. Before VOICE_GATEWAY_AUTH_TOKEN was
        # set no header was attached, so this stayed latent -- and the
        # production gate (prod_gate.py:282) now REQUIRES that token, which
        # makes every gateway call fail and drops every admitted inbound call
        # at session start.
        if self._gateway_session is None or self._gateway_session.closed:
            self._gateway_session = aiohttp.ClientSession()
        async with self._gateway_session.request(
            method,
            f"{self._gateway_base_url}{path}",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status not in ok:
                body = await resp.text()
                raise RuntimeError(f"Gateway {method} {path} → {resp.status}: {body[:300]}")
            try:
                return await resp.json(content_type=None)
            except Exception:
                return {}

    @staticmethod
    def _seal_gateway_session_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Attach the canonical SHA-256 used for idempotent session creation."""

        unsigned = dict(payload)
        unsigned.pop("config_digest", None)
        canonical = json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        sealed = dict(unsigned)
        sealed["config_digest"] = hashlib.sha256(canonical).hexdigest()
        return sealed

    async def _start_gateway_session(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Start once, or prove an existing session has the exact same config.

        A different configuration for the same id is a 409 from protocol v2
        and is never accepted.  Production also requires the gateway to echo
        the id and digest; development/test retains an explicit compatibility
        path for older fakes while a backend/gateway pair is upgraded atomically.
        """

        sealed = self._seal_gateway_session_payload(payload)
        ack = await self._gateway(
            "POST",
            "/v1/sessions/start",
            payload=sealed,
            ok=(200,),
        )
        environment = os.getenv("ENVIRONMENT", "development").strip().lower()
        require_ack = os.getenv(
            "VOICE_GATEWAY_REQUIRE_CONFIG_ACK",
            "true" if environment in {"production", "prod"} else "false",
        ).strip().lower() not in {"false", "0", "no", "off"}
        if not isinstance(ack, dict) or not ack:
            if require_ack:
                raise RuntimeError("Gateway start returned no verifiable acknowledgement")
            logger.warning(
                "gateway_start_ack_unverified session=%s environment=%s",
                str(sealed.get("session_id") or "")[:20],
                environment,
            )
            return {}
        if ack.get("status") not in {"started", "already_exists"}:
            raise RuntimeError(f"Gateway returned invalid start status: {ack.get('status')!r}")
        if ack.get("protocol_version") != 2:
            raise RuntimeError("Gateway start acknowledgement protocol_version mismatch")
        if ack.get("codec") != sealed.get("codec"):
            raise RuntimeError("Gateway start acknowledgement codec mismatch")
        if ack.get("session_id") != sealed["session_id"]:
            raise RuntimeError("Gateway start acknowledgement session_id mismatch")
        if ack.get("config_digest") != sealed["config_digest"]:
            raise RuntimeError("Gateway start acknowledgement config_digest mismatch")
        return ack

    @staticmethod
    def _tts_queue_config() -> Dict[str, Any]:
        """Optional cap on the C++ gateway's TTS queue (frames; 20ms each).

        The gateway defaults to 400 frames = ~8s of audio, which reads alarming
        — but it is a CEILING, not a working depth. Python paces egress to
        ``TELEPHONY_TTS_TARGET_AHEAD_S`` (0.3s), so measured live depth is ~15
        frames and `/stats` reported ``tts_queue_depth_frames: 0``. Lowering it
        therefore changes nothing in steady state, while adding a real risk:
        any path that bursts (the pre-synth greeting sends 119-170 chunks) would
        start DROPPING frames — audio loss, to fix a problem that is not
        occurring.

        The primary protection against stale audio is generation invalidation
        (VG-13 utterance_id rotation + 409 on late chunks), which is already in
        place and verified.

        So this is exposed as a lever, not a default: set
        ``VOICE_GATEWAY_TTS_MAX_QUEUE_FRAMES=100`` to test a tighter cap without
        a deploy. Unset (the default) leaves the gateway's own 400.
        """
        raw = os.getenv("VOICE_GATEWAY_TTS_MAX_QUEUE_FRAMES", "").strip()
        if not raw:
            return {}
        try:
            frames = int(raw)
        except ValueError:
            logger.warning(
                "VOICE_GATEWAY_TTS_MAX_QUEUE_FRAMES=%r is not an integer — "
                "ignoring, gateway default applies",
                raw,
            )
            return {}
        if frames <= 0:
            return {}
        return {"tts_max_queue_frames": frames}

    @staticmethod
    def _stt_reorder_config() -> Dict[str, Any]:
        """VG-01 (fixes garbled spoken emails/phone digits): deliver caller RTP
        to STT in SEQUENCE order via the gateway's reorder window instead of raw
        network-arrival order. Adds ~window*20ms (default 60-80ms) of STT
        latency — inaudible, but digits/spelled letters stop getting shuffled by
        UDP jitter. ENABLED by default; instant kill-switch WITHOUT a redeploy:
        set VOICE_GATEWAY_STT_REORDER=false in .env and restart talky-api.
        Window/hold are gateway-validated (window 1-25, hold >= window*20, <=1000).
        """
        enabled = os.getenv("VOICE_GATEWAY_STT_REORDER", "true").strip().lower()
        if enabled in ("0", "false", "no", "off"):
            return {}
        return {
            "stt_reorder_enabled": True,
            "stt_reorder_window_frames": int(os.getenv("VOICE_GATEWAY_STT_REORDER_WINDOW", "3")),
            "stt_reorder_hold_ms": int(os.getenv("VOICE_GATEWAY_STT_REORDER_HOLD_MS", "80")),
        }

    async def _alloc_rtp_port(self) -> int:
        async with self._rtp_lock:
            span = self._rtp_port_end - self._rtp_port_start + 1
            for _ in range(span):
                candidate = self._rtp_next
                self._rtp_next += 1
                if self._rtp_next > self._rtp_port_end:
                    self._rtp_next = self._rtp_port_start
                if candidate not in self._rtp_in_use:
                    self._rtp_in_use.add(candidate)
                    return candidate
            raise RuntimeError("AsteriskAdapter: no free RTP port available")

    async def _release_rtp_port(self, port: int) -> None:
        async with self._rtp_lock:
            self._rtp_in_use.discard(port)

    def _channel_created_key(self, channel: Optional[Dict[str, Any]]) -> str:
        if not channel:
            return ""
        for key in ("creationtime", "creationTime", "created_at"):
            value = channel.get(key)
            if value:
                return str(value)
        return ""

    def _channel_cache_key(self, channel_id: str, created_key: str) -> tuple[str, str]:
        return (channel_id, created_key)

    def _purge_expired_channel_varset_cache(self, now: float) -> None:
        expired = [
            key
            for key, entry in self._channel_varset_cache.items()
            if now - entry.cached_at > self._channel_varset_cache_ttl_s
        ]
        for key in expired:
            self._channel_varset_cache.pop(key, None)

    def _update_channel_varset_cache(
        self,
        *,
        channel_id: str,
        channel: Optional[Dict[str, Any]],
        variable: str,
        value: Any,
        now: float,
    ) -> None:
        if variable not in {"UNICASTRTP_LOCAL_ADDRESS", "UNICASTRTP_LOCAL_PORT"}:
            return

        self._purge_expired_channel_varset_cache(now)
        created_key = self._channel_created_key(channel)
        key = self._channel_cache_key(channel_id, created_key)
        entry = self._channel_varset_cache.get(key)
        if not entry:
            entry = _UnicastRtpCacheEntry(created_key=created_key, cached_at=now)
            self._channel_varset_cache[key] = entry

        entry.cached_at = now
        if variable == "UNICASTRTP_LOCAL_ADDRESS":
            entry.remote_ip = str(value or "127.0.0.1")
        elif variable == "UNICASTRTP_LOCAL_PORT":
            try:
                entry.remote_port = int(value)
            except (TypeError, ValueError):
                entry.remote_port = None

    def _cache_unicastrtp_local(
        self,
        *,
        channel_id: str,
        channel: Optional[Dict[str, Any]],
        remote_ip: str,
        remote_port: int,
        now: float,
    ) -> None:
        self._update_channel_varset_cache(
            channel_id=channel_id,
            channel=channel,
            variable="UNICASTRTP_LOCAL_ADDRESS",
            value=remote_ip,
            now=now,
        )
        self._update_channel_varset_cache(
            channel_id=channel_id,
            channel=channel,
            variable="UNICASTRTP_LOCAL_PORT",
            value=remote_port,
            now=now,
        )

    def _get_cached_unicastrtp_local(
        self,
        *,
        channel_id: str,
        channel: Optional[Dict[str, Any]],
        now: float,
    ) -> Optional[tuple[str, int]]:
        self._purge_expired_channel_varset_cache(now)
        created_key = self._channel_created_key(channel)

        candidates: list[_UnicastRtpCacheEntry] = []
        if created_key:
            entry = self._channel_varset_cache.get(self._channel_cache_key(channel_id, created_key))
            if entry:
                candidates.append(entry)
        else:
            for (cached_channel_id, _), entry in self._channel_varset_cache.items():
                if cached_channel_id == channel_id:
                    candidates.append(entry)

        if not candidates:
            return None

        freshest = max(candidates, key=lambda item: item.cached_at)
        if freshest.remote_ip and freshest.remote_port:
            return freshest.remote_ip, freshest.remote_port
        return None

    async def _resolve_unicastrtp_local(
        self,
        *,
        channel_id: str,
        channel: Optional[Dict[str, Any]],
    ) -> tuple[str, int]:
        loop = asyncio.get_running_loop()
        now = loop.time()
        cached = self._get_cached_unicastrtp_local(channel_id=channel_id, channel=channel, now=now)
        if cached:
            return cached

        addr_var = await self._ari(
            "GET",
            f"/channels/{channel_id}/variable",
            params={"variable": "UNICASTRTP_LOCAL_ADDRESS"},
        )
        remote_ip = str(addr_var.get("value", "") or "127.0.0.1")

        remote_port = 0
        for attempt in range(6):
            port_var = await self._ari(
                "GET",
                f"/channels/{channel_id}/variable",
                params={"variable": "UNICASTRTP_LOCAL_PORT"},
            )
            raw_port = port_var.get("value", 0)
            try:
                remote_port = int(raw_port) if raw_port else 0
            except (TypeError, ValueError):
                remote_port = 0
            if remote_port:
                break
            if attempt < 5:
                await asyncio.sleep(0.1)

        if not remote_port:
            raise RuntimeError(
                f"UNICASTRTP_LOCAL_PORT returned 0 after retries for " f"channel={channel_id[:12]}"
            )

        self._cache_unicastrtp_local(
            channel_id=channel_id,
            channel=channel,
            remote_ip=remote_ip,
            remote_port=remote_port,
            now=loop.time(),
        )
        return remote_ip, remote_port

    def _drop_channel_varset_cache(self, channel_id: str) -> None:
        stale_keys = [key for key in self._channel_varset_cache if key[0] == channel_id]
        for key in stale_keys:
            self._channel_varset_cache.pop(key, None)

    # ------------------------------------------------------------------
    # ARI WebSocket event listener
    # ------------------------------------------------------------------

    async def _ari_event_listener(self) -> None:
        """Connect to the ARI WebSocket and dispatch events."""
        import aiohttp

        api_key = f"{self._ari_username}:{self._ari_password}"
        ws_url = (
            f"ws://{self._ari_host}:{self._ari_port}/ari/events"
            f"?app={self._app_name}&api_key={api_key}"
        )
        safe_url = ws_url.replace(api_key, f"{self._ari_username}:***")
        logger.info("AsteriskAdapter: connecting ARI WS %s", safe_url)

        connector = aiohttp.TCPConnector()
        _reconnect_attempts = 0
        async with aiohttp.ClientSession(connector=connector) as sess:
            while not self._stop_event.is_set():
                try:
                    async with sess.ws_connect(
                        ws_url,
                        heartbeat=20,
                        timeout=aiohttp.ClientWSTimeout(ws_close=5),
                    ) as ws:
                        _reconnect_attempts = 0
                        logger.info("AsteriskAdapter: ARI WS connected")
                        async for msg in ws:
                            if self._stop_event.is_set():
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                import json

                                try:
                                    event = json.loads(msg.data)
                                    await self._handle_ari_event(event)
                                except Exception as exc:
                                    logger.debug(f"ARI event parse error: {exc}")
                            elif msg.type in (
                                aiohttp.WSMsgType.ERROR,
                                aiohttp.WSMsgType.CLOSED,
                            ):
                                break
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    if self._stop_event.is_set():
                        return
                    _reconnect_attempts += 1
                    delay = min(0.25 * (2 ** (_reconnect_attempts - 1)), 30.0) * (
                        0.5 + random.random()
                    )
                    logger.warning(
                        "AsteriskAdapter: ARI WS disconnected (attempt=%d, retry_in=%.1fs) — %s",
                        _reconnect_attempts,
                        delay,
                        exc,
                        extra={
                            "ari_reconnect_attempt": _reconnect_attempts,
                            "retry_delay_s": round(delay, 2),
                        },
                    )
                    await asyncio.sleep(delay)

    def _schedule_inbound_start(self, channel_id: str, event: Dict[str, Any]) -> None:
        existing = self._inbound_setup_tasks.get(channel_id)
        cleanup = self._preanswer_hangup_tasks.get(channel_id)
        if (
            self._stop_event.is_set()
            or channel_id in self._active_sessions
            or channel_id in self._inbound_setup_inflight
            or channel_id in self._inbound_admissions
            or channel_id in self._inbound_cleanup_pending
            or (existing is not None and not existing.done())
            or (cleanup is not None and not cleanup.done())
        ):
            logger.info("inbound_setup_duplicate_ignored channel=%s", channel_id[:12])
            return
        task = asyncio.create_task(self._on_stasis_start(channel_id, event))
        self._inbound_setup_tasks[channel_id] = task

        def _done(done: asyncio.Task) -> None:
            if self._inbound_setup_tasks.get(channel_id) is done:
                self._inbound_setup_tasks.pop(channel_id, None)
            if not done.cancelled() and done.exception() is not None:
                logger.error(
                    "inbound_setup_task_failed channel=%s err=%s",
                    channel_id[:12],
                    done.exception(),
                )

        task.add_done_callback(_done)

    def _schedule_terminal_cleanup(
        self,
        owner_id: str,
        cleanup_factory: Callable[[], Coroutine[Any, Any, None]],
        *,
        reason: str,
    ) -> bool:
        """Atomically claim one logical call's terminal-event burst."""

        if owner_id in self._end_dispatched:
            logger.debug(
                "terminal_cleanup_duplicate_ignored channel=%s reason=%s",
                owner_id[:12],
                reason,
            )
            return False

        self._end_dispatched[owner_id] = None
        try:
            task = asyncio.create_task(
                cleanup_factory(),
                name=f"asterisk-terminal:{owner_id}",
            )
        except Exception:
            self._end_dispatched.pop(owner_id, None)
            raise
        self._terminal_cleanup_tasks[owner_id] = task

        def _done(done: asyncio.Task) -> None:
            if self._terminal_cleanup_tasks.get(owner_id) is done:
                self._terminal_cleanup_tasks.pop(owner_id, None)
            if not done.cancelled() and done.exception() is not None:
                logger.error(
                    "terminal_cleanup_task_failed channel=%s reason=%s err=%s",
                    owner_id[:12],
                    reason,
                    done.exception(),
                )

        task.add_done_callback(_done)
        return True

    async def _cancel_inbound_setup_for_terminal(
        self,
        channel_id: str,
        *,
        reason: str,
    ) -> None:
        """Hand a setup-only terminal event to setup's cleanup owner."""

        setup_task = self._inbound_setup_tasks.get(channel_id)
        if setup_task is not None and not setup_task.done():
            setup_task.cancel()
            await asyncio.gather(setup_task, return_exceptions=True)

        handoff_task = self._inbound_handoff_tasks.get(channel_id)
        if (
            handoff_task is not None
            and not handoff_task.done()
            and channel_id not in self._inbound_handoff_accepted
        ):
            handoff_task.cancel()
            await asyncio.gather(handoff_task, return_exceptions=True)

        # Cancellation synchronously schedules deterministic resource cleanup.
        # If no task existed but an admission is still cached, retain the same
        # fail-closed ownership rule and prove the parent absent before release.
        cleanup_task = self._preanswer_hangup_tasks.get(channel_id)
        if (
            channel_id in self._inbound_admissions
            and channel_id not in self._active_sessions
            and (cleanup_task is None or cleanup_task.done())
            and channel_id not in self._inbound_cleanup_pending
        ):
            self._schedule_preanswer_hangup_and_release(
                channel_id,
                reason=f"terminal_during_setup:{reason}",
            )

    @staticmethod
    def _record_transfer_attempt_once(leg: _AsteriskTransferLeg) -> None:
        if leg.metrics_attempt_recorded:
            return
        # Flip before touching the observability dependency: metrics must never
        # make telephony fail, and a retry after an exporter failure would make
        # the counter's semantics depend on an unrelated subsystem.
        leg.metrics_started_at = time.monotonic()
        leg.metrics_attempt_recorded = True
        try:
            from app.infrastructure.metrics.inbound_metrics import (
                record_asterisk_transfer_attempt,
            )

            record_asterisk_transfer_attempt()
        except Exception:
            pass

    def _record_transfer_terminal_once(
        self,
        leg: _AsteriskTransferLeg,
        *,
        outcome: str,
        reason: str,
    ) -> None:
        if not leg.metrics_attempt_recorded or leg.metrics_terminal_recorded:
            return
        leg.metrics_terminal_recorded = True
        self._transfer_metrics_terminal_attempts += 1
        if outcome == "connected":
            self._transfer_metrics_successes += 1
        duration = max(0.0, time.monotonic() - leg.metrics_started_at)
        try:
            from app.infrastructure.metrics.inbound_metrics import (
                record_asterisk_transfer_terminal,
            )

            record_asterisk_transfer_terminal(outcome, reason, duration)
        except Exception:
            pass
        self._sync_transfer_inflight_metric()

    @staticmethod
    def _record_transfer_cleanup(scope: str, result: str) -> None:
        try:
            from app.infrastructure.metrics.inbound_metrics import (
                record_asterisk_transfer_cleanup,
            )

            record_asterisk_transfer_cleanup(scope, result)
        except Exception:
            pass

    def _sync_transfer_inflight_metric(self) -> None:
        try:
            from app.infrastructure.metrics.inbound_metrics import (
                set_asterisk_transfer_inflight,
            )

            # The parent map is authoritative and cannot double-count a target
            # alias. Connected linked legs remain adapter-owned for teardown,
            # but are no longer an in-flight transfer operation once their
            # exactly-once terminal outcome has been published.
            inflight = sum(
                1
                for leg in self._transfers_by_parent.values()
                if leg.metrics_attempt_recorded and not leg.metrics_terminal_recorded
            )
            set_asterisk_transfer_inflight(inflight)
        except Exception:
            pass

    def _transfer_provider_identity_conflict(
        self,
        leg: _AsteriskTransferLeg,
        actual_target_id: str,
    ) -> Optional[str]:
        """Return a local ownership collision before accepting an ARI alias."""

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}", actual_target_id):
            return "invalid_actual_provider_leg_id"
        if actual_target_id == leg.parent_id:
            return "parent_channel_alias"
        existing = self._transfers_by_target.get(actual_target_id)
        if existing is not None and existing is not leg:
            return "transfer_target_alias"
        if (
            actual_target_id in self._active_sessions
            or actual_target_id in self._inbound_admissions
            or actual_target_id in self._pending_outbound
            or actual_target_id in self._originated_channels
            or actual_target_id in self._ext_channels.values()
            or actual_target_id in self._bridges.values()
        ):
            return "owned_channel_alias"
        if actual_target_id in self._destroyed_channel_ids:
            return "destroyed_channel_alias"
        return None

    @staticmethod
    def _transfer_provider_identity_payload(
        leg: _AsteriskTransferLeg,
    ) -> Dict[str, Any]:
        if not leg.provider_identity_rebound:
            return {}
        return {
            "planned_provider_leg_id": leg.requested_target_id,
            "provider_leg_id": leg.persisted_target_id or leg.target_id,
            "provider_leg_id_rebound": True,
        }

    def _drop_transfer_indexes(self, leg: _AsteriskTransferLeg) -> None:
        # Failure outcomes become terminal only when the proof owner is ready
        # to drop the durable parent/target relationship. Success is recorded
        # earlier, at the confirmed human handoff, and this fallback therefore
        # cannot turn a later normal hangup into a second outcome.
        if leg.metrics_attempt_recorded and not leg.metrics_terminal_recorded:
            reason = str((leg.pending_failure or {}).get("error") or "target_unavailable")
            self._record_transfer_terminal_once(
                leg,
                outcome="failed",
                reason=reason,
            )
        if self._transfers_by_parent.get(leg.parent_id) is leg:
            self._transfers_by_parent.pop(leg.parent_id, None)
        if self._transfers_by_target.get(leg.target_id) is leg:
            self._transfers_by_target.pop(leg.target_id, None)
        self._sync_transfer_inflight_metric()

    def _resolve_transfer_failure(
        self,
        leg: _AsteriskTransferLeg,
        error: str,
        *,
        detail: Optional[str] = None,
    ) -> None:
        """Resolve a pre-answer target failure while keeping the caller live."""
        if leg.connected or leg.future.done():
            return
        payload: Dict[str, Any] = {
            "status": "failed",
            "call_id": leg.parent_id,
            "target_call_id": leg.persisted_target_id or leg.target_id,
            "destination": leg.destination,
            "mode": leg.mode,
            "error": error,
        }
        payload.update(self._transfer_provider_identity_payload(leg))
        if leg.persisted_target_id and leg.persisted_target_id != leg.target_id:
            payload["actual_target_call_id"] = leg.target_id
        if detail:
            payload["message"] = detail
        if leg.pending_failure is None:
            leg.pending_failure = payload
        # A failed/no-answer target may still exist after ARI accepts DELETE.
        # Keep its parent/target relationship discoverable until inventory (or
        # a 404) proves the child absent. The caller/AI stays live meanwhile.
        self._schedule_transfer_failure_cleanup(leg)

    def _schedule_transfer_failure_cleanup(self, leg: _AsteriskTransferLeg) -> None:
        existing = self._transfer_failure_cleanup_tasks.get(leg.parent_id)
        if existing is not None and not existing.done():
            return

        async def _run() -> None:
            try:
                while self._transfers_by_parent.get(leg.parent_id) is leg:
                    try:
                        cleanup_confirmed = await self.hangup_many_confirmed(
                            (leg.target_id,),
                            fence_root=False,
                        )
                    except Exception:
                        self._record_transfer_cleanup("target", "error")
                        raise
                    if cleanup_confirmed:
                        callback = self._on_transfer_cleanup_confirmed
                        if callback is not None:
                            try:
                                result = callback(
                                    leg.parent_id,
                                    leg.persisted_target_id or leg.target_id,
                                    str(
                                        (leg.pending_failure or {}).get("error")
                                        or "target_unavailable"
                                    ),
                                )
                                if asyncio.iscoroutine(result):
                                    await result
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                self._record_transfer_cleanup(
                                    "target",
                                    "domain_finalize_failed",
                                )
                                # PBX proof alone is not the domain commit: keep
                                # indexes/task ownership until the durable child
                                # leg and its lease have converged.
                                logger.error(
                                    "transfer_cleanup_domain_finalize_failed "
                                    "parent=%s target=%s err=%s",
                                    leg.parent_id[:12],
                                    leg.target_id[:12],
                                    exc,
                                )
                                await asyncio.sleep(max(0.05, self._inbound_cleanup_retry_s))
                                continue
                        self._record_transfer_cleanup("target", "confirmed")
                        self._drop_transfer_indexes(leg)
                        if not leg.future.done():
                            failure_result = dict(
                                leg.pending_failure
                                or {
                                    "status": "failed",
                                    "call_id": leg.parent_id,
                                    "target_call_id": leg.target_id,
                                    "error": "target_unavailable",
                                }
                            )
                            failure_result["target_termination_confirmed"] = True
                            failure_result["caller_media_retained"] = (
                                leg.parent_id in self._active_sessions
                                and leg.parent_id in self._gateway_sessions
                                and leg.parent_id in self._ext_channels
                            )
                            leg.future.set_result(failure_result)
                        return
                    self._record_transfer_cleanup("target", "unconfirmed")
                    logger.critical(
                        "AsteriskAdapter: failed transfer target cleanup "
                        "unconfirmed parent=%s target=%s",
                        leg.parent_id[:12],
                        leg.target_id[:12],
                    )
                    # Return a truthful nonterminal result after the first
                    # bounded proof attempt so the HTTP request does not hang
                    # forever. Domain settlement treats cleanup_pending as an
                    # active leg and retains its lease; this retry task (or a
                    # successor recovery owner) remains responsible for proof.
                    if not leg.future.done():
                        leg.future.set_result(
                            {
                                "status": "cleanup_pending",
                                "call_id": leg.parent_id,
                                "target_call_id": leg.target_id,
                                "provider_leg_id": leg.target_id,
                                "destination": leg.destination,
                                "mode": leg.mode,
                                "error": "target_termination_unconfirmed",
                            }
                        )
                    await asyncio.sleep(max(0.05, self._inbound_cleanup_retry_s))
            finally:
                current = self._transfer_failure_cleanup_tasks.get(leg.parent_id)
                if current is asyncio.current_task():
                    self._transfer_failure_cleanup_tasks.pop(leg.parent_id, None)

        self._transfer_failure_cleanup_tasks[leg.parent_id] = asyncio.create_task(
            _run(),
            name=f"asterisk-transfer-failure-cleanup:{leg.parent_id}",
        )

    async def _detach_ai_media_for_transfer(
        self,
        leg: _AsteriskTransferLeg,
    ) -> None:
        """Remove only the AI media leg after the human target answered."""
        parent_id = leg.parent_id
        session_info = self._active_sessions.get(parent_id) or {}
        session_id = self._gateway_sessions.get(parent_id)
        ext_channel_id = self._ext_channels.get(parent_id)
        listen_port = session_info.get("listen_port")

        # Stop generated/received audio first so no AI packet can leak into the
        # now-human conversation while ARI removes the ExternalMedia channel.
        gateway_stop_error: Optional[Exception] = None
        if session_id:
            try:
                await self._gateway(
                    "POST",
                    "/v1/sessions/stop",
                    payload={"session_id": session_id, "reason": "transfer_connected"},
                )
            except Exception as exc:
                gateway_stop_error = exc
                logger.warning(
                    "AsteriskAdapter: transfer gateway stop failed parent=%s err=%s",
                    parent_id[:12],
                    exc,
                )

        if ext_channel_id:
            self._drop_channel_varset_cache(ext_channel_id)
            try:
                await self._ari(
                    "POST",
                    f"/bridges/{leg.bridge_id}/removeChannel",
                    params={"channel": ext_channel_id},
                    ok=(200, 204, 404, 409, 422),
                )
            except Exception as exc:
                logger.warning(
                    "AsteriskAdapter: transfer media bridge detach request failed "
                    "parent=%s channel=%s err=%s",
                    parent_id[:12],
                    ext_channel_id[:12],
                    exc,
                )
            # A removeChannel acknowledgement (and even DELETE 204) is not
            # proof that AI media can no longer reach the human bridge. Reuse
            # the bounded ARI inventory proof and retain every identity/map if
            # it cannot establish absence.
            if not await self.hangup_many_confirmed(
                (ext_channel_id,),
                fence_root=False,
            ):
                raise RuntimeError("transfer_ai_media_detach_unconfirmed")
            self._drop_channel_varset_cache(ext_channel_id)
            if self._ext_channels.get(parent_id) == ext_channel_id:
                self._ext_channels.pop(parent_id, None)

        # A failed gateway stop is not a confirmed teardown. Keep the session
        # identity and RTP-port ownership intact so terminal cleanup (or a
        # successor recovery owner) can retry it. ExternalMedia absence above
        # prevents AI audio leakage while this retryable obligation remains.
        if gateway_stop_error is not None:
            raise RuntimeError("transfer_gateway_stop_unconfirmed") from gateway_stop_error

        if session_id and self._gateway_sessions.get(parent_id) == session_id:
            self._gateway_sessions.pop(parent_id, None)

        if isinstance(listen_port, int):
            await self._release_rtp_port(listen_port)
        if session_info:
            session_info["session_id"] = None
            session_info["listen_port"] = None
            session_info["media_detached_for_transfer"] = True
            session_info["transfer_target_id"] = leg.target_id

    async def _complete_transfer_connection(
        self,
        leg: _AsteriskTransferLeg,
    ) -> None:
        """Commit the live bridge hand-off exactly once on target answer."""
        if leg.handoff_started:
            return
        leg.handoff_started = True
        try:
            # Keep the ringing target outside the caller/AI bridge. On answer,
            # first prove AI ExternalMedia absent, then add the human target;
            # this avoids any interval where AI packets can leak into the
            # caller/representative conversation.
            await self._detach_ai_media_for_transfer(leg)

            if not leg.target_in_bridge:
                await self._ari(
                    "POST",
                    f"/bridges/{leg.bridge_id}/addChannel",
                    params={"channel": leg.target_id},
                    ok=(200, 204, 209),
                )
                leg.target_in_bridge = True

            # Provider Answer and the live human bridge must become durable
            # before any waiter can observe success. Without this ordering, a
            # process crash after set_result() can leave the child as
            # ``initiated`` and restart settlement would release a genuinely
            # billable answered leg as zero seconds.
            answer_callback = self._on_transfer_answered_persist
            if answer_callback is None:
                raise RuntimeError("transfer_answer_persistence_unavailable")
            answer_result = answer_callback(leg.parent_id, leg.target_id)
            if asyncio.iscoroutine(answer_result):
                await answer_result

            logger.info(
                "AsteriskAdapter: supervised transfer connected parent=%s target=%s",
                leg.parent_id[:12],
                leg.target_id[:12],
            )
            if (
                self._transfers_by_parent.get(leg.parent_id) is leg
                and not leg.terminal_dispatched
                and not leg.future.done()
            ):
                completed_result = {
                    "status": "completed",
                    "call_id": leg.parent_id,
                    "target_call_id": leg.target_id,
                    "provider_leg_id": leg.target_id,
                    "destination": leg.destination,
                    "mode": leg.mode,
                    "answered": True,
                    "handoff_confirmed": True,
                }
                completed_result.update(self._transfer_provider_identity_payload(leg))
                leg.future.set_result(completed_result)
                self._record_transfer_terminal_once(
                    leg,
                    outcome="connected",
                    reason="answered",
                )

            # Artifact persistence/pipeline teardown may be slow. The public
            # transfer result above is now safe because ExternalMedia absence
            # and the human bridge are already proved; do not let a slow
            # callback race the fixed dial-answer timeout into a false failure.
            callback = self._on_transfer_connected
            if callback is not None:
                try:
                    result = callback(leg.parent_id, leg.target_id)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as exc:  # telephony success remains authoritative
                    logger.error(
                        "AsteriskAdapter: transfer-connected callback failed "
                        "parent=%s target=%s err=%s",
                        leg.parent_id[:12],
                        leg.target_id[:12],
                        exc,
                    )
        except Exception as exc:
            # Once the target is Up, never re-attach the AI.  A bridge/detach
            # failure is terminal and both human legs are ended fail-closed.
            logger.error(
                "AsteriskAdapter: transfer handoff failed parent=%s target=%s err=%s",
                leg.parent_id[:12],
                leg.target_id[:12],
                exc,
            )
            if leg.pending_failure is None:
                leg.pending_failure = {
                    "status": "failed",
                    "call_id": leg.parent_id,
                    "target_call_id": leg.persisted_target_id or leg.target_id,
                    "provider_leg_id": leg.persisted_target_id or leg.target_id,
                    "destination": leg.destination,
                    "mode": leg.mode,
                    "error": "transfer_handoff_failed",
                    "message": str(exc),
                }
                leg.pending_failure.update(self._transfer_provider_identity_payload(leg))
            self._schedule_terminal_cleanup(
                leg.parent_id,
                lambda: self._handle_transfer_target_terminal(
                    leg,
                    cause="transfer_handoff_failed",
                ),
                reason="transfer_handoff_failed",
            )

    def _mark_transfer_answered(self, leg: _AsteriskTransferLeg) -> None:
        """Atomically publish provider Answer before slow artifact teardown."""
        if leg.persisted_target_id != leg.target_id:
            # ARI events can overtake the DB rebind callback. Remember the
            # provider proof, but never bridge/publish the handoff until the
            # authoritative actual channel id is durable.
            leg.answer_pending_identity_persist = True
            return
        if (
            leg.connected
            or leg.future.done()
            or leg.pending_failure is not None
            or leg.terminal_dispatched
        ):
            return
        leg.connected = True

        async def _run_handoff() -> None:
            try:
                await self._complete_transfer_connection(leg)
            finally:
                current = self._transfer_handoff_tasks.get(leg.parent_id)
                if current is asyncio.current_task():
                    self._transfer_handoff_tasks.pop(leg.parent_id, None)

        task = asyncio.create_task(
            _run_handoff(),
            name=f"asterisk-transfer-handoff:{leg.parent_id}",
        )
        self._transfer_handoff_tasks[leg.parent_id] = task

    async def _handle_transfer_target_terminal(
        self,
        leg: _AsteriskTransferLeg,
        *,
        cause: str,
    ) -> None:
        if leg.terminal_dispatched:
            return
        leg.terminal_dispatched = True
        handoff_task = self._transfer_handoff_tasks.get(leg.parent_id)
        if (
            handoff_task is not None
            and handoff_task is not asyncio.current_task()
            and not handoff_task.done()
        ):
            handoff_task.cancel()
            await asyncio.gather(handoff_task, return_exceptions=True)
        if not leg.connected:
            self._resolve_transfer_failure(leg, cause or "target_unavailable")
            return

        # A connected transfer is one logical call.  Whichever human leg ends
        # first ends the parent too. Keep this tracked terminal owner alive
        # until Asterisk proves *both* legs absent; only then may ordinary
        # lifecycle persistence release billing/concurrency.
        while self._transfers_by_parent.get(leg.parent_id) is leg:
            if await self._cleanup_transfer_for_parent(leg.parent_id):
                break
            await asyncio.sleep(max(0.05, self._inbound_cleanup_retry_s))
        await self._on_stasis_end(
            leg.parent_id,
            f"transfer_target_terminal:{cause or 'unknown'}",
            absence_proven=True,
        )

    async def _cleanup_transfer_for_parent(self, parent_id: str) -> bool:
        """Prove the parent and target absent before dropping linked-leg state."""

        leg = self._transfers_by_parent.get(parent_id)
        if leg is None:
            return True
        handoff_task = self._transfer_handoff_tasks.get(parent_id)
        if (
            handoff_task is not None
            and handoff_task is not asyncio.current_task()
            and not handoff_task.done()
        ):
            handoff_task.cancel()
            await asyncio.gather(handoff_task, return_exceptions=True)
        failure_cleanup = self._transfer_failure_cleanup_tasks.get(parent_id)
        if (
            failure_cleanup is not None
            and failure_cleanup is not asyncio.current_task()
            and not failure_cleanup.done()
        ):
            # Parent termination now owns both legs. Fence the target-only
            # retry owner before taking the all-leg snapshot.
            failure_cleanup.cancel()
            await asyncio.gather(failure_cleanup, return_exceptions=True)
        if not leg.future.done():
            leg.pending_failure = leg.pending_failure or {
                "status": "failed",
                "call_id": parent_id,
                "target_call_id": leg.target_id,
                "error": "caller_hung_up",
            }
        try:
            confirmed = await self.hangup_many_confirmed(
                (parent_id, leg.target_id),
            )
        except Exception:
            self._record_transfer_cleanup("linked", "error")
            raise
        if not confirmed:
            self._record_transfer_cleanup("linked", "unconfirmed")
            logger.critical(
                "AsteriskAdapter: linked transfer termination unconfirmed "
                "parent=%s target=%s; lifecycle settlement deferred",
                parent_id[:12],
                leg.target_id[:12],
            )
            return False
        self._record_transfer_cleanup("linked", "confirmed")
        # Do not destroy the only in-memory relationship until all-leg proof
        # exists. This lets retries and graceful disconnect keep finding the
        # child after a DELETE 204 whose channel remains live.
        self._drop_transfer_indexes(leg)
        if not leg.future.done():
            leg.future.set_result(
                dict(
                    leg.pending_failure
                    or {
                        "status": "failed",
                        "call_id": parent_id,
                        "target_call_id": leg.target_id,
                        "error": "caller_hung_up",
                    }
                )
            )
        return True

    async def _handle_ari_event(self, event: Dict[str, Any]) -> None:
        """Process ARI events and drive the session lifecycle."""
        event_type = str(event.get("type", ""))
        channel = event.get("channel") or {}
        channel_id = str(channel.get("id") or "")
        channel_name = str(channel.get("name") or "")
        loop = asyncio.get_running_loop()

        if event_type in {"ChannelVarset", "ChannelVarSet"} and channel_id:
            self._update_channel_varset_cache(
                channel_id=channel_id,
                channel=channel,
                variable=str(event.get("variable") or ""),
                value=event.get("value"),
                now=loop.time(),
            )
            return

        if event_type == "StasisStart":
            if self._stop_event.is_set():
                logger.info(
                    "stasis_start_ignored_during_disconnect channel=%s",
                    channel_id[:12],
                )
                return
            args: List[str] = event.get("args", [])
            # Skip UnicastRTP (external media) channels
            if channel_name.startswith("UnicastRTP/"):
                return

            arg0 = str(args[0]).strip().lower() if args else ""
            if arg0 == "transfer_target":
                # `/channels/create` places the supervised target in this same
                # Stasis app before `/dial`.  It is media for the existing
                # parent bridge, never a new AI/inbound call.
                parent_id = str(args[1]).strip() if len(args) > 1 else ""
                leg = self._transfers_by_target.get(channel_id)
                if leg is None and parent_id:
                    # Lookup by parent only to classify a protocol mismatch; it
                    # never authorizes rewriting the pre-persisted target ID.
                    leg = self._transfers_by_parent.get(parent_id)
                if leg is None:
                    logger.error(
                        "AsteriskAdapter: orphan transfer target rejected channel=%s",
                        channel_id[:12],
                    )
                    self._schedule_unclaimed_hangup(
                        channel_id,
                        reason="orphan_transfer_target",
                    )
                    return
                # ``target_id`` becomes the returned ARI id before the durable
                # rebind callback runs. StasisStart may race that callback;
                # classification may use the candidate, while Answer remains
                # gated on the durable identity in ``_mark_transfer_answered``.
                expected_target = leg.target_id
                if channel_id != expected_target or parent_id != leg.parent_id:
                    logger.error(
                        "AsteriskAdapter: transfer target identity mismatch "
                        "channel=%s expected=%s parent=%s expected_parent=%s",
                        channel_id[:12],
                        expected_target[:12],
                        parent_id[:12],
                        leg.parent_id[:12],
                    )
                    self._schedule_unclaimed_hangup(
                        channel_id,
                        reason="transfer_target_identity_mismatch",
                    )
                    return
                return

            # --- Outbound routing decision ---
            # Three ways to identify an outbound channel:
            # 1. channel_id matches a pre-generated ID in _originated_channels
            # 2. appArgs[0] == "outbound" (unreliable through PJSIP trunks)
            # 3. _originated_channels is non-empty — when originating through
            #    a PJSIP trunk (e.g. PJSIP/1002@lan-pbx), Asterisk creates
            #    a NEW channel for the trunk leg with a different ID than the
            #    one we requested.  The pre-generated ID never enters Stasis;
            #    the trunk-created channel does.  If we have any pending
            #    originated IDs, this StasisStart is almost certainly the
            #    trunk-created leg of that origination.
            is_our_originated = channel_id in self._originated_channels
            is_trunk_leg = (
                not is_our_originated
                and len(self._originated_channels) > 0
                and channel_name.startswith("PJSIP/")
            )

            # An explicit dialplan direction wins over every heuristic.  This
            # is what prevents a real inbound PJSIP leg from being consumed as
            # the "oldest" outbound call merely because an origination is
            # ringing at the same time.
            if arg0 == "inbound":
                self._schedule_inbound_start(channel_id, event)
            elif is_our_originated or arg0 == "outbound" or is_trunk_leg:
                if is_trunk_leg:
                    # A trunk-created leg entered Stasis with an id different
                    # from the one we requested. Match it to its SPECIFIC
                    # origination via CHANNEL(linkedid) — a deterministic key
                    # that ties every leg of a call together — instead of
                    # guessing the oldest pending origination (FIFO), which
                    # cross-wires when two calls are dialing at once and their
                    # legs enter Stasis / are answered out of origination order.
                    # The correlating read is async, so the consume + alias +
                    # outbound-start all run inside the task (see below).
                    asyncio.create_task(self._start_trunk_leg(channel_id))
                    return
                elif (
                    not is_our_originated
                    and arg0 == "outbound"
                    and len(self._originated_channels) == 1
                ):
                    stale_id = self._consume_oldest_originated_channel()
                    if stale_id is not None:
                        self._emit_outbound_channel_alias(stale_id, channel_id)
                else:
                    self._discard_originated_channel(channel_id)
                asyncio.create_task(self._on_outbound_stasis_start(channel_id))
            else:
                # Unknown Stasis calls still pass through the same fail-closed
                # inbound admission callback. Missing ingress/DID metadata will
                # be denied; there is no default agent fallback.
                self._schedule_inbound_start(channel_id, event)

        elif event_type == "ChannelStateChange":
            # Fired when a channel transitions state, e.g. Ring → Up (callee answered).
            channel_state = str(channel.get("state") or "").lower()
            if channel_state == "up":
                transfer_leg = self._transfers_by_target.get(channel_id)
                if transfer_leg is not None:
                    self._mark_transfer_answered(transfer_leg)
                elif channel_id in self._pending_outbound:
                    asyncio.create_task(self._on_outbound_answered(channel_id))
                else:
                    # StasisStart processing may be pending as a create_task that
                    # hasn't run yet (ARI WS delivers events faster than tasks are
                    # scheduled).  Record the Up event so _on_outbound_stasis_start
                    # can fire _on_outbound_answered immediately after parking.
                    logger.debug(
                        f"AsteriskAdapter: ChannelStateChange(Up) arrived before "
                        f"StasisStart processed for channel={channel_id[:12]} — saved for later"
                    )
                    self._preemptive_up_channels.add(channel_id)
            elif channel_state in ("ringing", "ring"):
                # Carrier 180 — the callee's phone just started ringing. Arrives
                # well before StasisStart, so surface it for live-status now.
                # Only for OUR outbound channels; once per channel.
                self._dispatch_early_ringing(channel_id)

        elif event_type == "Dial":
            # Some carriers (observed on Blaze) never flip the channel state to
            # Ringing, so ChannelStateChange alone misses the entire ring phase
            # and the UI sits on "Dialing" until pickup. Asterisk still emits
            # Dial events with dialstatus progression ("" → RINGING → ANSWER)
            # for channels subscribed to the app — use RINGING as the same
            # early-ringing signal (deduped, so double delivery is harmless).
            dial_status = str(event.get("dialstatus") or "").upper()
            peer = event.get("peer") or {}
            _dial_ch = str(peer.get("id") or channel_id or "")
            transfer_leg = self._transfers_by_target.get(_dial_ch)
            if transfer_leg is not None:
                if dial_status == "ANSWER":
                    self._mark_transfer_answered(transfer_leg)
                elif dial_status in {
                    "BUSY",
                    "NOANSWER",
                    "CANCEL",
                    "CHANUNAVAIL",
                    "CONGESTION",
                    "DONTCALL",
                    "TORTURE",
                }:
                    self._resolve_transfer_failure(
                        transfer_leg,
                        dial_status.lower(),
                    )
                return
            if dial_status == "RINGING":
                self._dispatch_early_ringing(_dial_ch)

        elif event_type in ("StasisEnd", "ChannelDestroyed", "ChannelHangupRequest"):
            if event_type == "ChannelDestroyed" and channel_id:
                if len(self._destroyed_channel_ids) >= 4000:
                    self._destroyed_channel_ids.clear()
                self._destroyed_channel_ids.add(channel_id)
                # Asterisk also emits ChannelDestroyed for ExternalMedia,
                # outbound and transfer-target legs. Only the admitted inbound
                # parent is a billing boundary; recording every child leaked
                # clocks and could evict the live parent's proof.
                if self._is_inbound_parent_channel(channel_id):
                    self._record_terminal_at_monotonic(channel_id)
            # Capture the hangup cause (Q.850) BEFORE we tear anything down so
            # the outcome resolver can classify no-answer / busy / rejected
            # instead of defaulting to an agent-side hangup. ChannelDestroyed
            # carries `cause` (int) + `cause_txt` (e.g. "No Answer", "User
            # busy"); the channel's `hangupsource`/`cause` may also be present.
            _cause_txt = event.get("cause_txt") or channel.get("cause_txt")
            if not _cause_txt:
                _cause_int = event.get("cause")
                if _cause_int is None:
                    _cause_int = channel.get("cause")
                if _cause_int is not None:
                    _cause_txt = (
                        _Q850_CAUSE_TEXT.get(int(_cause_int))
                        if str(_cause_int).lstrip("-").isdigit()
                        else None
                    )
            if _cause_txt and channel_id not in self._hangup_causes:
                # Bound memory: get_hangup_cause() pops entries it consumes, but a
                # call that ends pre-answer with no voice session is never
                # consumed, so cap the map and evict the oldest half (dicts keep
                # insertion order) to prevent unbounded growth over a long run.
                if len(self._hangup_causes) >= 2000:
                    for _old in list(self._hangup_causes)[:1000]:
                        self._hangup_causes.pop(_old, None)
                # Keep the first (most authoritative) terminal cause for a
                # channel — StasisEnd + ChannelDestroyed can both fire.
                self._hangup_causes[channel_id] = str(_cause_txt)

            transfer_target = self._transfers_by_target.get(channel_id)
            if transfer_target is not None:
                if transfer_target.connected:
                    self._schedule_terminal_cleanup(
                        transfer_target.parent_id,
                        lambda: self._handle_transfer_target_terminal(
                            transfer_target,
                            cause=str(_cause_txt or event_type).strip().lower(),
                        ),
                        reason=f"transfer_target:{event_type}",
                    )
                else:
                    await self._handle_transfer_target_terminal(
                        transfer_target,
                        cause=str(_cause_txt or event_type).strip().lower(),
                    )
                return
            # Drop any preemptive Up record for channels that are now gone.
            self._preemptive_up_channels.discard(channel_id)
            self._early_ring_emitted.discard(channel_id)
            self._discard_originated_channel(channel_id)
            # Clean up pending outbound channels that were never answered.
            if channel_id in self._pending_outbound:
                self._schedule_terminal_cleanup(
                    channel_id,
                    lambda: self._cleanup_pending_outbound(
                        channel_id,
                        absence_proven=event_type == "ChannelDestroyed",
                    ),
                    reason=event_type,
                )
            elif self._adapter_owns_inbound_handoff(channel_id):
                # Before lifecycle acknowledges VoiceSession registration, the
                # adapter remains the only resource/quota owner even though its
                # media maps may already be populated.
                self._schedule_terminal_cleanup(
                    channel_id,
                    lambda: self._cancel_inbound_setup_for_terminal(
                        channel_id,
                        reason=event_type,
                    ),
                    reason=event_type,
                )
            elif channel_id in self._active_sessions:
                self._schedule_terminal_cleanup(
                    channel_id,
                    lambda: self._on_stasis_end(
                        channel_id,
                        event_type,
                        absence_proven=event_type == "ChannelDestroyed",
                    ),
                    reason=event_type,
                )
            elif channel_id in self._ext_channels.values():
                # External channel ended — find and clean up parent
                parent = next(
                    (k for k, v in self._ext_channels.items() if v == channel_id),
                    None,
                )
                if parent:
                    if self._adapter_owns_inbound_handoff(parent):
                        self._schedule_terminal_cleanup(
                            parent,
                            lambda: self._cancel_inbound_setup_for_terminal(
                                parent,
                                reason=event_type,
                            ),
                            reason=event_type,
                        )
                    else:
                        self._schedule_terminal_cleanup(
                            parent,
                            # The event proves only the ExternalMedia channel
                            # absent. The parent human leg still needs its own
                            # DELETE-404/inventory proof.
                            lambda: self._on_stasis_end(
                                parent,
                                event_type,
                                absence_proven=False,
                            ),
                            reason=event_type,
                        )
            elif (
                event_type == "ChannelDestroyed"
                and channel_id not in self._end_dispatched
                and channel_id.startswith("talky-out")
                and self._on_any_call_end is not None
            ):
                # PRE-STASIS terminal: a channel WE originated died without
                # ever entering Stasis (busy / no-answer / rejected / carrier
                # failure — with app-originate, StasisStart only fires on
                # answer). It has no _pending_outbound entry and no session,
                # so none of the arms above fires the end callback — without
                # this, the call row sits in dialing/ringing until a reaper
                # sweeps it minutes later. Signal the end NOW so status +
                # outcome (from the captured Q.850 cause) land in real time.
                logger.info(
                    f"AsteriskAdapter: pre-answer terminal channel={channel_id[:12]} "
                    f"— dispatching call-end for real-time outcome"
                )
                self._schedule_terminal_cleanup(
                    channel_id,
                    lambda: self._on_any_call_end(channel_id),
                    reason=event_type,
                )
            # Bound the dedupe map (insertion-ordered dict; evict oldest half).
            if len(self._end_dispatched) >= 2000:
                for _old in list(self._end_dispatched)[:1000]:
                    self._end_dispatched.pop(_old, None)

    async def _on_outbound_stasis_start(self, channel_id: str) -> None:
        """
        Handle an outbound call entering Stasis (callee is still ringing).

        Creates the mixing bridge and adds the outbound channel to it, then
        stores the pending state.  The ExternalMedia channel and C++ gateway
        session are NOT started here — they are deferred to _on_outbound_answered
        so that no RTP timeout fires while we are waiting for the callee to pick up.
        """
        logger.info(f"AsteriskAdapter: outbound call ringing channel={channel_id[:12]}")
        listen_port = await self._alloc_rtp_port()
        session_id = f"asterisk-{uuid.uuid4().hex}"
        bridge_id = ""

        try:
            # 1. Create mixing bridge
            bridge = await self._ari("POST", "/bridges", params={"type": "mixing"})
            bridge_id = bridge.get("id", "")
            if not bridge_id:
                raise RuntimeError("ARI bridge create returned no id")

            # 2. Add outbound channel to bridge (starts ringing the remote party)
            await self._ari(
                "POST",
                f"/bridges/{bridge_id}/addChannel",
                params={"channel": channel_id},
                ok=(200, 204, 209),
            )

            # Park the metadata — _on_outbound_answered will complete the setup
            self._pending_outbound[channel_id] = {
                "bridge_id": bridge_id,
                "listen_port": listen_port,
                "session_id": session_id,
            }

            # Fire the ringing-phase callback FIRST — before checking for
            # preemptive Up — so the bridge can start pre-warming STT/TTS/LLM
            # connections regardless of whether the callee already answered.
            # This fixes a critical race: when ChannelStateChange(Up) arrives
            # before StasisStart is processed, the old code returned early and
            # _on_ringing was never called, forcing a 2+ second answer-path
            # warmup and causing the user's first "hello" to be lost.
            if self._on_ringing is not None:
                asyncio.create_task(self._on_ringing(channel_id))

            # Race condition: the callee may have already answered while
            # StasisStart was sitting in the ARI WebSocket queue.  If so,
            # ChannelStateChange(Up) was stored in _preemptive_up_channels;
            # we must fire _on_outbound_answered right now instead of waiting.
            if channel_id in self._preemptive_up_channels:
                self._preemptive_up_channels.discard(channel_id)
                logger.info(
                    f"AsteriskAdapter: outbound call already answered (preemptive Up) "
                    f"channel={channel_id[:12]} — completing media setup immediately"
                )
                asyncio.create_task(self._on_outbound_answered(channel_id))
                return

            logger.info(
                f"AsteriskAdapter: outbound channel parked, waiting for answer "
                f"channel={channel_id[:12]} bridge={bridge_id[:12]} rtp_port={listen_port}"
            )

        except Exception as exc:
            logger.error(f"AsteriskAdapter: outbound stasis start failed: {exc}")
            if bridge_id:
                try:
                    await self._ari("DELETE", f"/bridges/{bridge_id}", ok=(200, 204, 404))
                except Exception:
                    pass
            await self._release_rtp_port(listen_port)

    async def _on_outbound_answered(self, channel_id: str) -> None:
        """
        Complete ExternalMedia + C++ gateway setup once the callee answers.

        Called when ChannelStateChange fires with state=Up for a pending
        outbound channel.  At this point RTP will flow immediately, so the
        gateway startup timeout won't expire before audio arrives.
        """
        pending = self._pending_outbound.pop(channel_id, None)
        if not pending:
            return

        bridge_id = pending["bridge_id"]
        listen_port = pending["listen_port"]
        session_id = pending["session_id"]
        ext_channel_id = ""

        logger.info(
            "t_answer channel=%s rtp_port=%s",
            channel_id[:12],
            listen_port,
            extra={"call_id": channel_id, "t_answer_ms": 0},
        )
        logger.info(
            f"AsteriskAdapter: outbound call answered — completing media setup "
            f"channel={channel_id[:12]} rtp_port={listen_port}"
        )

        try:
            loop = asyncio.get_running_loop()
            _t_setup_start = loop.time()

            # 3. Create ExternalMedia channel pointing at C++ Gateway RTP listener.
            # This one must run first — steps 4/5/6 all need ext_channel_id.
            ext_data = await self._ari(
                "POST",
                "/channels/externalMedia",
                params={
                    "app": self._app_name,
                    "external_host": f"{self._gateway_rtp_ip}:{listen_port}",
                    "format": "ulaw",
                    "encapsulation": "rtp",
                    "transport": "udp",
                    "connection_type": "client",
                    "direction": "both",
                },
            )
            ext_channel_id = ext_data.get("id", "")
            if not ext_channel_id:
                raise RuntimeError("ARI externalMedia returned no channel id")

            # 4/5/6. addChannel + two UNICASTRTP_LOCAL_* GETs are independent of
            # each other (they only share the ext_channel_id dependency), so run
            # them concurrently.  Saves ~200 ms on a typical outbound answer
            # (ASTERISK-26771: each ARI request has ~50-200 ms baseline latency).
            add_coro = self._ari(
                "POST",
                f"/bridges/{bridge_id}/addChannel",
                params={"channel": ext_channel_id},
                ok=(200, 204, 209),
            )
            _, (remote_ip, remote_port) = await asyncio.gather(
                add_coro,
                self._resolve_unicastrtp_local(channel_id=ext_channel_id, channel=ext_data),
            )

            # 6. Start C++ Gateway session — call is already answered so RTP is
            #    flowing immediately; no startup-timeout risk.
            await self._start_gateway_session(
                {
                    "session_id": session_id,
                    "listen_ip": self._gateway_rtp_ip,
                    "listen_port": listen_port,
                    "remote_ip": remote_ip,
                    "remote_port": remote_port,
                    "codec": "pcmu",
                    "ptime_ms": 20,
                    "echo_enabled": False,
                    "startup_no_rtp_timeout_ms": 10000,  # 10s — call is live
                    "active_no_rtp_timeout_ms": 15000,  # 15s silence timeout
                    # Maximum call lifetime (two hours by default).
                    "session_final_timeout_ms": _SESSION_FINAL_TIMEOUT_MS,
                    # Loopback has no jitter; one frame is 20ms (formerly 3).
                    "jitter_buffer_prefetch_frames": 1,
                    # 2 frames = 40ms = Deepgram Flux's optimal chunk size. Was 4 (80ms) when
                    # we re-batched downstream; now we hand off frames at Flux's native rate so
                    # there's no re-chunking jitter and per-call resamples drop by 50%.
                    # Sourced from telephony.config so the gap detector, which
                    # derives its threshold from the same constant, cannot drift
                    # out of step with it again (it did: this went 4 -> 2 while
                    # the detector kept assuming 80ms).
                    "audio_callback_batch_frames": AUDIO_CALLBACK_BATCH_FRAMES,
                    # Asterisk tells us the exact source tuple through
                    # UNICASTRTP_LOCAL_*. Never let an arbitrary first packet
                    # teach the gateway a different source.
                    "enforce_rtp_source": True,
                    # VG-01 sequence-ordered STT tap (see _stt_reorder_config).
                    **self._stt_reorder_config(),
                    # Optional TTS queue cap (see _tts_queue_config).
                    **self._tts_queue_config(),
                    "audio_callback_url": (
                        f"{os.getenv('BACKEND_INTERNAL_URL', 'http://127.0.0.1:8000')}"
                        f"/api/v1/sip/telephony/audio/{session_id}"
                    ),
                }
            )

            # Track the session
            self._active_sessions[channel_id] = {
                "session_id": session_id,
                "listen_port": listen_port,
                "bridge_id": bridge_id,
                "direction": "outbound",
            }
            self._ext_channels[channel_id] = ext_channel_id
            self._bridges[channel_id] = bridge_id
            self._gateway_sessions[channel_id] = session_id

            _setup_ms = (loop.time() - _t_setup_start) * 1000.0
            logger.info(
                "ari_setup_done channel=%s session=%s rtp_port=%s remote=%s:%s setup_ms=%.0f",
                channel_id[:12],
                session_id,
                listen_port,
                remote_ip,
                remote_port,
                _setup_ms,
                extra={
                    "call_id": channel_id,
                    "ari_setup_ms": round(_setup_ms),
                    "session_id": session_id,
                },
            )

            # 7. Notify callbacks so the AI pipeline can start
            cb = self._call_arrived_callbacks.get(channel_id)
            if cb:
                asyncio.create_task(cb(channel_id))
            elif self._on_new_call:
                asyncio.create_task(self._on_new_call(channel_id))

        except Exception as exc:
            logger.error(f"AsteriskAdapter: outbound answered setup failed: {exc}")
            if session_id:
                try:
                    await self._gateway(
                        "POST",
                        "/v1/sessions/stop",
                        payload={"session_id": session_id, "reason": "setup_failed"},
                    )
                except Exception:
                    pass
            if ext_channel_id:
                self._drop_channel_varset_cache(ext_channel_id)
                try:
                    await self._ari("DELETE", f"/channels/{ext_channel_id}", ok=(200, 204, 404))
                except Exception:
                    pass
            if bridge_id:
                try:
                    await self._ari("DELETE", f"/bridges/{bridge_id}", ok=(200, 204, 404))
                except Exception:
                    pass
            try:
                await self._ari("DELETE", f"/channels/{channel_id}", ok=(200, 204, 404))
            except Exception:
                pass
            await self._release_rtp_port(listen_port)

    def _extract_inbound_meta(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Pull the called DID + dialplan context (+ caller number) out of an
        inbound StasisStart event, DEFENSIVELY — the exact field that carries
        the DID varies by trunk config, so we try several.

        Stasis arg[0] is the direction marker.  The canonical dialplan passes
        OpenSIPS' trusted original Request-URI user as arg[1] and the ingress
        context as arg[2].  Older dialplans fall back to dialplan.exten and
        connected.number.  Only masked values are logged.
        """
        channel = event.get("channel") or {}
        dialplan = channel.get("dialplan") or {}
        caller = channel.get("caller") or {}
        connected = channel.get("connected") or {}
        args = event.get("args") or []

        raw_arg0 = str(args[0]).strip() if args else ""
        arg_direction = raw_arg0.lower() if raw_arg0.lower() in {"inbound", "outbound"} else None
        if arg_direction is not None:
            arg_called_did = args[1] if len(args) > 1 else None
            arg_context = args[2] if len(args) > 2 else None
        else:
            # Backward-compatible shape used by older local dialplans:
            # Stasis(app,<did>[,<context>]). It is still admitted strictly.
            arg_called_did = raw_arg0 or None
            arg_context = args[1] if len(args) > 1 else None
        channel_name = str(channel.get("name") or "")
        ingress_endpoint = None
        if channel_name.startswith("PJSIP/"):
            endpoint_leg = channel_name[len("PJSIP/") :]
            ingress_endpoint = endpoint_leg.rsplit("-", 1)[0] or None

        called_did = (
            arg_called_did
            or (dialplan.get("exten") if isinstance(dialplan, dict) else None)
            or (connected.get("number") if isinstance(connected, dict) else None)
        )
        context = arg_context or (dialplan.get("context") if isinstance(dialplan, dict) else None)
        caller_number = caller.get("number") if isinstance(caller, dict) else None

        if not self._inbound_debug_dumped:
            self._inbound_debug_dumped = True
            logger.info(
                "inbound_stasis_shape direction=%s did=%s ani=%s "
                "context_present=%s endpoint_present=%s args_count=%d",
                arg_direction or "unknown",
                _masked_number(called_did),
                _masked_number(caller_number),
                bool(context),
                bool(ingress_endpoint),
                len(args),
            )

        return {
            "called_did": called_did,
            "context": context,
            "caller_number": caller_number,
            "direction": arg_direction,
            "ingress": "asterisk",
            "ingress_endpoint": ingress_endpoint,
            "linked_id": channel.get("linkedid"),
        }

    def get_inbound_call_meta(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Return the captured ingress metadata for an
        inbound channel (or None). Consumed by the bridge's _on_new_call to
        consume the already-admitted route. Non-destructive read."""
        return self._inbound_call_meta.get(channel_id)

    @staticmethod
    def _normalise_admission_decision(
        decision: Any,
        *,
        channel_id: str,
        meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        if decision is None:
            return {"allowed": False, "reason": "empty_admission_decision"}
        if isinstance(decision, dict):
            payload = dict(decision)
        else:
            to_dict = getattr(decision, "to_dict", None)
            payload = (
                dict(to_dict())
                if callable(to_dict)
                else {
                    name: getattr(decision, name)
                    for name in (
                        "allowed",
                        "accepted",
                        "admitted",
                        "reason",
                        "call_id",
                        "talklee_call_id",
                        "tenant_id",
                        "campaign_id",
                        "config_id",
                        "assignment_id",
                        "trunk_id",
                        "route_version",
                        "config_version",
                        "opening_mode",
                        "config_snapshot",
                        "concurrency_lease_id",
                        "usage_reservation_id",
                        "is_replay",
                    )
                    if hasattr(decision, name)
                }
            )

        if "allowed" not in payload:
            payload["allowed"] = bool(payload.get("accepted", payload.get("admitted", False)))
        payload.setdefault("reason", "admitted" if payload["allowed"] else "denied")
        payload.setdefault("provider", "asterisk")
        payload.setdefault("provider_call_id", channel_id)
        payload.setdefault("called_did", meta.get("called_did"))
        payload.setdefault("caller_ani", meta.get("caller_number"))
        payload.setdefault("ingress", meta.get("ingress") or "asterisk")
        return payload

    async def _admit_inbound(
        self,
        channel_id: str,
        meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        def denied(reason: str) -> Dict[str, Any]:
            return self._normalise_admission_decision(
                {"allowed": False, "reason": reason},
                channel_id=channel_id,
                meta=meta,
            )

        callback = self._on_inbound_admission
        answer_persist = self._on_inbound_answered_persist
        finalizer = self._on_inbound_admission_finalize
        if callback is None or answer_persist is None or finalizer is None:
            if callback is None:
                missing = "callback_unavailable"
            elif finalizer is None:
                missing = "finalizer_unavailable"
            else:
                missing = "answer_persist_unavailable"
            logger.error(
                "inbound_admission_denied channel=%s reason=%s",
                channel_id[:12],
                missing,
            )
            return denied(missing)
        try:
            raw = await asyncio.wait_for(
                callback(channel_id, dict(meta)),
                timeout=self._inbound_admission_timeout_s,
            )
            decision = self._normalise_admission_decision(
                raw,
                channel_id=channel_id,
                meta=meta,
            )
        except asyncio.TimeoutError:
            logger.error(
                "inbound_admission_denied channel=%s reason=timeout timeout_s=%.1f",
                channel_id[:12],
                self._inbound_admission_timeout_s,
            )
            return denied("admission_timeout")
        except Exception as exc:  # noqa: BLE001 — ingress must fail closed
            logger.error(
                "inbound_admission_denied channel=%s reason=callback_error err=%s",
                channel_id[:12],
                exc,
            )
            return denied("admission_callback_error")

        if not decision.get("allowed"):
            logger.warning(
                "inbound_admission_denied channel=%s did=%s reason=%s",
                channel_id[:12],
                _masked_number(meta.get("called_did")),
                str(decision.get("reason") or "denied")[:80],
            )
            # A policy denial may still own a durable calls row and/or a
            # concurrency lease. Cache that identity so the proof-owned
            # pre-answer finalizer can release it only after the PBX leg is gone.
            if decision.get("call_id") and decision.get("tenant_id"):
                self._inbound_admissions[channel_id] = decision
            return decision

        # A positive decision without the durable call identity is not an
        # admission.  Continuing would recreate the former best-effort row path.
        if not decision.get("call_id") or not decision.get("tenant_id"):
            logger.error(
                "inbound_admission_denied channel=%s reason=incomplete_decision",
                channel_id[:12],
            )
            decision["allowed"] = False
            decision["reason"] = "incomplete_admission_decision"
            return decision

        self._inbound_admissions[channel_id] = decision
        logger.info(
            "inbound_admitted channel=%s tenant=%s campaign=%s replay=%s",
            channel_id[:12],
            str(decision.get("tenant_id"))[:8],
            str(decision.get("campaign_id") or "-")[:8],
            bool(decision.get("is_replay")),
        )
        return decision

    async def _persist_pre_row_inbound_rejection(
        self,
        channel_id: str,
        meta: Dict[str, Any],
        admission: Dict[str, Any],
    ) -> bool:
        """Durably record a denial that has no billable ``calls`` row."""

        if admission.get("call_id"):
            # The admission transaction already persisted this reason on the
            # calls row; the operator endpoint unions that durable source.
            return True
        callback = self._on_inbound_rejection_persist
        if not callable(callback):
            logger.critical(
                "inbound_rejection_persist_unavailable channel=%s reason=%s",
                channel_id[:12],
                str(admission.get("reason") or "admission_denied")[:96],
            )
            return False

        payload = {
            "provider": str(admission.get("provider") or "asterisk"),
            "provider_call_id": str(admission.get("provider_call_id") or channel_id),
            "called_did": admission.get("called_did") or meta.get("called_did"),
            "caller_ani": admission.get("caller_ani") or meta.get("caller_number"),
            "ingress": admission.get("ingress") or meta.get("ingress") or "asterisk",
            "reason": str(admission.get("reason") or "admission_denied"),
        }
        try:
            await asyncio.wait_for(
                callback(channel_id, payload),
                timeout=self._inbound_rejection_persist_timeout_s,
            )
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - rejection must still terminate
            logger.critical(
                "inbound_rejection_persist_failed channel=%s reason=%s err=%s",
                channel_id[:12],
                payload["reason"][:96],
                exc,
            )
            return False

    def get_inbound_admission(self, channel_id: str) -> Optional[Dict[str, Any]]:
        decision = self._inbound_admissions.get(channel_id)
        return dict(decision) if decision is not None else None

    def _is_inbound_parent_channel(self, channel_id: str) -> bool:
        """Return whether ``channel_id`` owns inbound billing state."""

        if channel_id in self._inbound_admissions:
            return True
        session = self._active_sessions.get(channel_id)
        return isinstance(session, dict) and session.get("direction") == "inbound"

    def _record_terminal_at_monotonic(
        self,
        channel_id: str,
        timestamp: Optional[float] = None,
        *,
        timestamp_utc: Optional[str] = None,
    ) -> float:
        """Freeze the first authoritative parent-leg absence time."""

        if len(self._terminal_at_monotonic) >= 4000:
            for stale_id in list(self._terminal_at_monotonic)[:2000]:
                self._terminal_at_monotonic.pop(stale_id, None)
                self._terminal_at_utc.pop(stale_id, None)
        value = float(time.monotonic() if timestamp is None else timestamp)
        frozen = self._terminal_at_monotonic.setdefault(channel_id, value)
        self._terminal_at_utc.setdefault(
            channel_id,
            str(timestamp_utc or datetime.now(timezone.utc).isoformat()),
        )
        return frozen

    def pop_terminal_at_monotonic(self, channel_id: str) -> Optional[float]:
        """Return and retire the frozen absence clock for lifecycle billing."""

        self._terminal_at_utc.pop(channel_id, None)
        return self._terminal_at_monotonic.pop(channel_id, None)

    def pop_inbound_admission(self, channel_id: str) -> Optional[Dict[str, Any]]:
        handoff_task = self._inbound_handoff_tasks.get(channel_id)
        if handoff_task is None or handoff_task.done():
            self._inbound_handoff_accepted.discard(channel_id)
        decision = self._inbound_admissions.pop(channel_id, None)
        return dict(decision) if decision is not None else None

    @staticmethod
    def _admitted_inbound_action(decision: Dict[str, Any]) -> str:
        """Read the pre-answer action from a self-consistent pinned snapshot."""

        snapshot = decision.get("config_snapshot")
        inbound_config = snapshot.get("inbound_config") if isinstance(snapshot, dict) else None
        schedule = snapshot.get("schedule_decision") if isinstance(snapshot, dict) else None
        if not isinstance(inbound_config, dict) or not isinstance(schedule, dict):
            raise ValueError("missing pinned inbound schedule")
        action = inbound_config.get("selected_action")
        if action not in {"agent", "hangup", "voicemail", "transfer"}:
            raise ValueError("invalid pinned inbound action")
        if schedule.get("selected_action") != action:
            raise ValueError("inconsistent pinned inbound action")
        return str(action)

    @staticmethod
    def _answered_elapsed_seconds(
        decision: Dict[str, Any],
        *,
        terminal_at_monotonic: Optional[float] = None,
    ) -> int:
        """Measure confirmed Answer to frozen PBX absence.

        Answer intent is not billable evidence. Ambiguous Answer requests are
        finalized into the existing carrier-CDR hold with zero measured
        seconds rather than growing while cleanup retries. The result can never
        exceed the admission's pinned reservation.
        """

        marker = decision.get("_answered_at_monotonic")
        marker_is_valid = isinstance(marker, (int, float)) and not isinstance(marker, bool)
        terminal_is_valid = isinstance(terminal_at_monotonic, (int, float)) and not isinstance(
            terminal_at_monotonic,
            bool,
        )
        if marker_is_valid and not terminal_is_valid:
            raise RuntimeError("confirmed inbound Answer lacks terminal proof")
        if not marker_is_valid:
            return 0
        elapsed = max(
            1,
            math.ceil(max(0.0, float(terminal_at_monotonic) - float(marker))),
        )
        snapshot = decision.get("config_snapshot")
        route = snapshot.get("route") if isinstance(snapshot, dict) else None
        reservation = route.get("reservation_seconds") if isinstance(route, dict) else None
        if isinstance(reservation, int) and not isinstance(reservation, bool):
            return min(elapsed, max(0, reservation))
        return elapsed

    async def _persist_inbound_terminal_proof_for_channel(self, channel_id: str) -> bool:
        """Durably fence an answered inbound terminal boundary.

        A positive return means either this channel has no billable inbound
        Answer, or PostgreSQL now contains the exact PBX terminal timestamp and
        capped duration. Callers must retain cleanup ownership and retry while
        this returns false.
        """

        decision = self._inbound_admissions.get(channel_id)
        if decision is None:
            return True
        answered_marker = decision.get("_answered_at_monotonic")
        answered_confirmed = bool(
            (
                isinstance(answered_marker, (int, float))
                and not isinstance(answered_marker, bool)
            )
            or decision.get("_answered_at_utc")
        )
        if not answered_confirmed:
            return True
        if decision.get("_terminal_proof_persisted") is True:
            return True
        callback = self._on_inbound_terminal_proof_persist
        if not callable(callback):
            logger.critical(
                "inbound_terminal_proof_persist_unavailable channel=%s",
                channel_id[:12],
            )
            return False
        terminal_at_monotonic = self._terminal_at_monotonic.get(channel_id)
        terminal_at_utc = self._terminal_at_utc.get(channel_id)
        if terminal_at_monotonic is None or not terminal_at_utc:
            logger.critical(
                "inbound_terminal_proof_missing channel=%s",
                channel_id[:12],
            )
            return False
        try:
            duration_seconds = self._answered_elapsed_seconds(
                decision,
                terminal_at_monotonic=terminal_at_monotonic,
            )
            if not (
                isinstance(answered_marker, (int, float))
                and not isinstance(answered_marker, bool)
            ):
                # A wall-clock-only Answer can prove billing occurred, but it
                # cannot safely produce elapsed seconds across clock changes.
                duration_seconds = 0
                decision["_terminal_duration_ambiguous"] = True
            await callback(
                channel_id,
                dict(decision),
                terminated_at=terminal_at_utc,
                duration_seconds=duration_seconds,
            )
            decision["_terminal_proof_persisted"] = True
            decision["_terminal_duration_seconds"] = duration_seconds
            decision["_terminal_at_utc"] = terminal_at_utc
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - proof ownership must retry
            logger.critical(
                "inbound_terminal_proof_persist_failed channel=%s err=%s",
                channel_id[:12],
                exc,
            )
            return False

    async def _release_cached_inbound_admission(
        self,
        channel_id: str,
        *,
        reason: str,
    ) -> bool:
        decision = self._inbound_admissions.get(channel_id)
        callback = self._on_inbound_admission_finalize
        if decision is None:
            return True
        if callback is None:
            return False
        try:
            terminal_at = self._terminal_at_monotonic.get(channel_id)
            answered_duration = self._answered_elapsed_seconds(
                decision,
                terminal_at_monotonic=terminal_at,
            )
            answered_marker = decision.get("_answered_at_monotonic")
            monotonic_answer_confirmed = bool(
                isinstance(answered_marker, (int, float))
                and not isinstance(answered_marker, bool)
            )
            answer_confirmed = bool(
                monotonic_answer_confirmed or decision.get("_answered_at_utc")
            )
            answer_ambiguous = bool(
                not answer_confirmed
                and (
                    decision.get("_answer_intent_at_monotonic") is not None
                    or decision.get("_answer_intent_at_utc")
                )
            )
            duration_ambiguous = bool(
                answer_confirmed
                and (
                    not monotonic_answer_confirmed
                    or decision.get("_terminal_duration_ambiguous")
                )
            )
            terminal_reason = (
                "process_restart_answer_ambiguous"
                if answer_ambiguous or duration_ambiguous
                else reason
            )
            await callback(
                channel_id,
                decision,
                terminal_status="failed",
                duration_seconds=answered_duration,
                reason=terminal_reason,
                release_only=(not answer_confirmed and not answer_ambiguous),
            )
            if self._inbound_admissions.get(channel_id) is decision:
                self._inbound_admissions.pop(channel_id, None)
            released = self._inbound_admissions.get(channel_id) is not decision
            if released:
                self._terminal_at_monotonic.pop(channel_id, None)
                self._terminal_at_utc.pop(channel_id, None)
            return released
        except Exception as exc:  # noqa: BLE001 — cleanup remains best effort
            logger.error(
                "inbound_admission_release_failed channel=%s reason=%s err=%s",
                channel_id[:12],
                reason,
                exc,
            )
            return False

    async def _attempt_failed_inbound_setup_cleanup(
        self,
        channel_id: str,
        *,
        session_id: str = "",
        ext_channel_id: str = "",
        bridge_id: str = "",
    ) -> bool:
        """Make one idempotent cleanup pass and return authoritative proof."""

        confirmed = True
        if session_id:
            try:
                await self._gateway(
                    "POST",
                    "/v1/sessions/stop",
                    payload={"session_id": session_id, "reason": "start_failed"},
                    ok=(200, 204, 404),
                )
            except Exception as exc:
                confirmed = False
                logger.warning(
                    "inbound_setup_gateway_stop_unconfirmed channel=%s err=%s",
                    channel_id[:12],
                    exc,
                )
        if ext_channel_id:
            self._drop_channel_varset_cache(ext_channel_id)
            try:
                await self._ari(
                    "DELETE",
                    f"/channels/{ext_channel_id}",
                    ok=(200, 204, 404),
                )
            except Exception as exc:
                confirmed = False
                logger.warning(
                    "inbound_setup_external_delete_unconfirmed channel=%s ext=%s err=%s",
                    channel_id[:12],
                    ext_channel_id[:12],
                    exc,
                )
        if not await self.hangup_confirmed(channel_id):
            confirmed = False
        if bridge_id:
            try:
                await self._ari(
                    "DELETE",
                    f"/bridges/{bridge_id}",
                    ok=(200, 204, 404),
                )
            except Exception as exc:
                confirmed = False
                logger.warning(
                    "inbound_setup_bridge_delete_unconfirmed channel=%s bridge=%s err=%s",
                    channel_id[:12],
                    bridge_id[:12],
                    exc,
                )
        return confirmed

    def _schedule_failed_inbound_setup_cleanup(
        self,
        channel_id: str,
        *,
        reason: str,
        session_id: str = "",
        ext_channel_id: str = "",
        bridge_id: str = "",
        listen_port: Optional[int] = None,
    ) -> None:
        """Retry all post-answer cleanup before releasing quota/reservation."""

        existing = self._preanswer_hangup_tasks.get(channel_id)
        if existing is not None and not existing.done():
            return
        self._inbound_cleanup_pending.add(channel_id)

        async def _run() -> None:
            port_released = False
            try:
                while True:
                    try:
                        cleanup_confirmed = await self._attempt_failed_inbound_setup_cleanup(
                            channel_id,
                            session_id=session_id,
                            ext_channel_id=ext_channel_id,
                            bridge_id=bridge_id,
                        )
                        if cleanup_confirmed:
                            if listen_port is not None and not port_released:
                                await self._release_rtp_port(listen_port)
                                port_released = True
                            released = await asyncio.shield(
                                self._release_cached_inbound_admission(
                                    channel_id,
                                    reason=reason,
                                )
                            )
                            if released:
                                self._inbound_call_meta.pop(channel_id, None)
                                self._inbound_cleanup_pending.discard(channel_id)
                                return
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - retain ownership
                        logger.exception(
                            "inbound_setup_cleanup_pass_failed channel=%s err=%s",
                            channel_id[:12],
                            exc,
                        )
                    logger.critical(
                        "inbound_setup_cleanup_unconfirmed channel=%s reason=%s",
                        channel_id[:12],
                        reason,
                    )
                    await asyncio.sleep(self._inbound_cleanup_retry_s)
            finally:
                current = self._preanswer_hangup_tasks.get(channel_id)
                if current is asyncio.current_task():
                    self._preanswer_hangup_tasks.pop(channel_id, None)

        self._preanswer_hangup_tasks[channel_id] = asyncio.create_task(
            _run(),
            name=f"inbound-setup-cleanup:{channel_id}",
        )

    async def _cleanup_failed_inbound_setup_now_or_schedule(
        self,
        channel_id: str,
        *,
        reason: str,
        session_id: str = "",
        ext_channel_id: str = "",
        bridge_id: str = "",
        listen_port: Optional[int] = None,
    ) -> None:
        """Complete the normal fast path inline; background only real retries."""

        self._inbound_cleanup_pending.add(channel_id)
        cleanup_confirmed = await self._attempt_failed_inbound_setup_cleanup(
            channel_id,
            session_id=session_id,
            ext_channel_id=ext_channel_id,
            bridge_id=bridge_id,
        )
        if cleanup_confirmed:
            if listen_port is not None:
                try:
                    await self._release_rtp_port(listen_port)
                    listen_port = None
                except Exception as exc:  # noqa: BLE001 - retry in owner task
                    cleanup_confirmed = False
                    logger.error(
                        "inbound_setup_port_release_failed channel=%s port=%s err=%s",
                        channel_id[:12],
                        listen_port,
                        exc,
                    )
            if cleanup_confirmed:
                released = await self._release_cached_inbound_admission(
                    channel_id,
                    reason=reason,
                )
                if released:
                    self._inbound_call_meta.pop(channel_id, None)
                    self._inbound_cleanup_pending.discard(channel_id)
                    return
        self._schedule_failed_inbound_setup_cleanup(
            channel_id,
            reason=reason,
            session_id=session_id,
            ext_channel_id=ext_channel_id,
            bridge_id=bridge_id,
            listen_port=listen_port,
        )

    def reject_pending_inbound_handoff(
        self,
        channel_id: str,
        *,
        reason: str,
    ) -> bool:
        """Fence one locally-created inbound channel before lifecycle ownership.

        This deliberately recognizes only an admitted, active session whose
        adapter metadata says ``direction=inbound``.  It never scans Asterisk
        and never acts on another process's durable session records.  Claiming
        the local maps is synchronous, so terminal events and repeated fence
        attempts cannot acquire a second cleanup owner.
        """

        if channel_id in self._inbound_handoff_accepted:
            return False
        if channel_id in self._inbound_cleanup_pending:
            return True
        session_info = self._active_sessions.get(channel_id)
        if (
            not isinstance(session_info, dict)
            or session_info.get("direction") != "inbound"
            or channel_id not in self._inbound_admissions
        ):
            return False

        session_info = self._active_sessions.pop(channel_id)
        session_id = str(
            self._gateway_sessions.pop(channel_id, None) or session_info.get("session_id") or ""
        )
        ext_channel_id = str(self._ext_channels.pop(channel_id, None) or "")
        bridge_id = str(self._bridges.pop(channel_id, None) or session_info.get("bridge_id") or "")
        listen_port = session_info.get("listen_port")
        if not isinstance(listen_port, int):
            listen_port = None

        self._inbound_cleanup_pending.add(channel_id)
        self._schedule_failed_inbound_setup_cleanup(
            channel_id,
            reason=reason,
            session_id=session_id,
            ext_channel_id=ext_channel_id,
            bridge_id=bridge_id,
            listen_port=listen_port,
        )
        logger.critical(
            "inbound_handoff_fenced channel=%s reason=%s",
            channel_id[:12],
            reason,
        )
        return True

    def accept_inbound_handoff(self, channel_id: str) -> bool:
        """Atomically acknowledge fully initialized lifecycle ownership.

        Lifecycle calls this only after its final cancellable initialization
        await. Terminal handling therefore sees exactly one owner: adapter
        cleanup for a provisional session, or lifecycle teardown afterwards.
        """

        session_info = self._active_sessions.get(channel_id)
        if (
            not isinstance(session_info, dict)
            or session_info.get("direction") != "inbound"
            or channel_id not in self._inbound_admissions
            or channel_id in self._inbound_cleanup_pending
        ):
            return False
        self._inbound_handoff_accepted.add(channel_id)
        logger.info("inbound_handoff_accepted channel=%s", channel_id[:12])
        return True

    def _adapter_owns_inbound_handoff(self, channel_id: str) -> bool:
        """Return whether terminal handling must stay on adapter cleanup."""

        setup_task = self._inbound_setup_tasks.get(channel_id)
        handoff_task = self._inbound_handoff_tasks.get(channel_id)
        if channel_id in self._inbound_setup_inflight:
            return True
        if setup_task is not None and not setup_task.done():
            return True
        if (
            handoff_task is not None
            and not handoff_task.done()
            and channel_id not in self._inbound_handoff_accepted
        ):
            return True
        if channel_id in self._inbound_cleanup_pending:
            return True
        return channel_id in self._inbound_admissions and channel_id not in self._active_sessions

    def _schedule_inbound_handoff(
        self,
        channel_id: str,
        admission: Dict[str, Any],
    ) -> None:
        """Track the answer-to-lifecycle-registration window explicitly."""

        callback = self._on_new_call
        if callback is None:
            self.reject_pending_inbound_handoff(
                channel_id,
                reason="lifecycle_callback_unavailable",
            )
            return

        async def _run() -> None:
            try:
                await callback(channel_id, dict(admission))
                if channel_id not in self._inbound_handoff_accepted:
                    self.reject_pending_inbound_handoff(
                        channel_id,
                        reason="lifecycle_handoff_unacknowledged",
                    )
                    raise RuntimeError(
                        "inbound lifecycle callback returned without accepting ownership"
                    )
            except asyncio.CancelledError:
                self.reject_pending_inbound_handoff(
                    channel_id,
                    reason="lifecycle_handoff_cancelled",
                )
                raise
            except Exception:
                self.reject_pending_inbound_handoff(
                    channel_id,
                    reason="lifecycle_handoff_failed",
                )
                raise

        task = asyncio.create_task(
            _run(),
            name=f"inbound-lifecycle-handoff:{channel_id}",
        )
        self._inbound_handoff_tasks[channel_id] = task

        def _done(done: asyncio.Task) -> None:
            if self._inbound_handoff_tasks.get(channel_id) is done:
                self._inbound_handoff_tasks.pop(channel_id, None)
            if (
                channel_id not in self._active_sessions
                and channel_id not in self._inbound_admissions
            ):
                self._inbound_handoff_accepted.discard(channel_id)
            if not done.cancelled() and done.exception() is not None:
                logger.error(
                    "inbound_lifecycle_handoff_failed channel=%s err=%s",
                    channel_id[:12],
                    done.exception(),
                )

        task.add_done_callback(_done)

    def _schedule_unclaimed_hangup(
        self,
        channel_id: str,
        *,
        reason: str,
        reason_code: Optional[int] = None,
    ) -> None:
        """Durably own a rejected PBX leg until hard absence proof.

        These channels have no VoiceSession and may have no calls row. The
        state-backend cleanup obligation is therefore the only restart-safe
        identity a successor can use. Registration is attempted before the
        first DELETE; if Redis is temporarily unavailable the local task keeps
        retrying instead of creating an untracked crash window.
        """

        existing = self._unclaimed_hangup_tasks.get(channel_id)
        if existing is not None and not existing.done():
            return
        self._inbound_cleanup_pending.add(channel_id)

        async def _run() -> None:
            registered = False
            try:
                while not registered:
                    try:
                        from app.domain.services.telephony.state_backend import (
                            get_state_backend,
                        )

                        backend = get_state_backend()
                        register = getattr(
                            backend,
                            "register_cleanup_obligation",
                            None,
                        )
                        if callable(register):
                            await register(
                                channel_id,
                                state="termination_pending",
                            )
                        registered = True
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.critical(
                            "unclaimed_channel_ledger_register_failed "
                            "channel=%s reason=%s err=%s",
                            channel_id[:12],
                            reason,
                            exc,
                        )
                        await asyncio.sleep(self._inbound_cleanup_retry_s)

                while True:
                    # This is explicitly an unclaimed single leg. Expanding a
                    # target through the transfer registry could terminate its
                    # still-live parent when cleaning a provider-ID mismatch.
                    if await self.hangup_many_confirmed(
                        (channel_id,),
                        fence_root=False,
                        reason_code=reason_code,
                    ):
                        finalizer = self._on_inbound_admission_finalize
                        if callable(finalizer):
                            try:
                                # Admission may have acquired the cluster-wide
                                # slot before timing out without returning a
                                # durable calls-row identity. PBX proof must
                                # therefore be followed by strict slot release
                                # before the cleanup ledger is acknowledged.
                                await asyncio.shield(
                                    finalizer(
                                        channel_id,
                                        {},
                                        terminal_status="failed",
                                        duration_seconds=0,
                                        reason=reason,
                                        release_only=True,
                                    )
                                )
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                logger.critical(
                                    "unclaimed_channel_capacity_release_failed "
                                    "channel=%s reason=%s err=%s",
                                    channel_id[:12],
                                    reason,
                                    exc,
                                )
                                await asyncio.sleep(self._inbound_cleanup_retry_s)
                                continue
                        try:
                            backend = get_state_backend()
                            acknowledge = getattr(
                                backend,
                                "acknowledge_orphan_recovery",
                                None,
                            )
                            if callable(acknowledge):
                                await acknowledge(channel_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.critical(
                                "unclaimed_channel_ledger_ack_failed "
                                "channel=%s reason=%s err=%s",
                                channel_id[:12],
                                reason,
                                exc,
                            )
                            await asyncio.sleep(self._inbound_cleanup_retry_s)
                            continue
                        self._inbound_call_meta.pop(channel_id, None)
                        self._inbound_cleanup_pending.discard(channel_id)
                        return
                    logger.critical(
                        "unclaimed_channel_hangup_unconfirmed channel=%s reason=%s",
                        channel_id[:12],
                        reason,
                    )
                    await asyncio.sleep(self._inbound_cleanup_retry_s)
            finally:
                current = self._unclaimed_hangup_tasks.get(channel_id)
                if current is asyncio.current_task():
                    self._unclaimed_hangup_tasks.pop(channel_id, None)

        self._unclaimed_hangup_tasks[channel_id] = asyncio.create_task(
            _run(),
            name=f"asterisk-unclaimed-hangup:{channel_id}",
        )

    @staticmethod
    def _inbound_denial_reason_code(reason: str) -> Optional[int]:
        """Map a bounded admission result to its caller-facing Q.850 cause.

        Unknown policy/configuration denials are deliberately terminal (21),
        not congestion. Only the explicit transient allowlist may invite an
        upstream retry. The configured after-hours ``hangup`` action retains
        normal clearing to preserve its existing operator-selected behaviour.
        """

        normalized = str(reason or "").strip().lower()
        if normalized == "after_hours_closed":
            return None
        if normalized in _INBOUND_NOT_FOUND_REASONS:
            return 1
        if normalized in _INBOUND_BUSY_REASONS:
            return 17
        if normalized in _INBOUND_TRANSIENT_REASONS:
            return 42
        return 21

    def _schedule_preanswer_hangup_and_release(
        self,
        channel_id: str,
        *,
        reason: str,
    ) -> None:
        """Retry PBX deletion before releasing a pre-answer reservation."""

        existing = self._preanswer_hangup_tasks.get(channel_id)
        if existing is not None and not existing.done():
            return
        self._inbound_cleanup_pending.add(channel_id)

        async def _run() -> None:
            try:
                while channel_id in self._inbound_admissions:
                    if await self.hangup_confirmed(
                        channel_id,
                        reason_code=self._inbound_denial_reason_code(reason),
                    ):
                        released = await asyncio.shield(
                            self._release_cached_inbound_admission(
                                channel_id,
                                reason=reason,
                            )
                        )
                        if released:
                            self._inbound_call_meta.pop(channel_id, None)
                            self._inbound_cleanup_pending.discard(channel_id)
                            return
                    logger.critical(
                        "inbound_preanswer_hangup_unconfirmed channel=%s reason=%s",
                        channel_id[:12],
                        reason,
                    )
                    await asyncio.sleep(self._inbound_cleanup_retry_s)
            finally:
                current = self._preanswer_hangup_tasks.get(channel_id)
                if current is asyncio.current_task():
                    self._preanswer_hangup_tasks.pop(channel_id, None)

        self._preanswer_hangup_tasks[channel_id] = asyncio.create_task(
            _run(),
            name=f"inbound-preanswer-hangup:{channel_id}",
        )

    async def _on_stasis_start(self, channel_id: str, event: Dict[str, Any]) -> None:
        """Admit, answer, then attach media for a true inbound channel."""
        cleanup = self._preanswer_hangup_tasks.get(channel_id)
        if (
            self._stop_event.is_set()
            or channel_id in self._inbound_setup_inflight
            or channel_id in self._active_sessions
            or channel_id in self._inbound_admissions
            or channel_id in self._inbound_cleanup_pending
            or (cleanup is not None and not cleanup.done())
        ):
            logger.info("inbound_setup_duplicate_ignored channel=%s", channel_id[:12])
            return
        self._inbound_setup_inflight.add(channel_id)

        logger.info("AsteriskAdapter: inbound Stasis channel=%s", channel_id[:12])
        listen_port: Optional[int] = None
        session_id = ""
        bridge_id = ""
        ext_channel_id = ""
        setup_failure_reason = "media_setup_failed"

        try:
            # Admission is deliberately the first awaited side effect. A
            # missing/invalid metadata field, dependency error, timeout, or
            # negative decision leaves the channel unanswered and hangs it up.
            meta = self._extract_inbound_meta(event)
            self._inbound_call_meta[channel_id] = meta
            admission_started = time.monotonic()
            admission = await self._admit_inbound(channel_id, meta)
            logger.info(
                "inbound_admission_elapsed channel=%s allowed=%s reason=%s "
                "admission_elapsed_ms=%.1f",
                channel_id[:12],
                bool(admission.get("allowed")),
                str(admission.get("reason") or "-")[:80],
                (time.monotonic() - admission_started) * 1000.0,
            )
            if not admission.get("allowed"):
                reason = str(admission.get("reason") or "admission_denied")
                await self._persist_pre_row_inbound_rejection(
                    channel_id,
                    meta,
                    admission,
                )
                if channel_id in self._inbound_admissions:
                    self._schedule_preanswer_hangup_and_release(
                        channel_id,
                        reason=reason,
                    )
                else:
                    self._schedule_unclaimed_hangup(
                        channel_id,
                        reason=reason,
                        reason_code=self._inbound_denial_reason_code(reason),
                    )
                return

            try:
                selected_action = self._admitted_inbound_action(admission)
            except ValueError as exc:
                logger.error(
                    "inbound_admission_snapshot_invalid channel=%s err=%s",
                    channel_id[:12],
                    exc,
                )
                self._schedule_preanswer_hangup_and_release(
                    channel_id,
                    reason="invalid_schedule_snapshot",
                )
                return

            if selected_action == "hangup":
                # The configured action is a true pre-answer reject. The
                # durable admission row exists already, but no billable media
                # or external-media channel is created.
                self._schedule_preanswer_hangup_and_release(
                    channel_id,
                    reason="after_hours_closed",
                )
                logger.info(
                    "inbound_after_hours_reject_scheduled channel=%s",
                    channel_id[:12],
                )
                return

            # Persist a two-phase Answer intent before crossing the external
            # ARI side-effect boundary. If this process is killed after
            # Asterisk accepts Answer but before the confirmation hook below,
            # recovery sees ``answer_pending`` plus the durable call/provider
            # identities and holds settlement for carrier/CDR adjudication
            # rather than releasing a potentially billable call as zero.
            from app.domain.services.telephony.state_backend import (
                get_state_backend,
            )

            answer_intent_at_monotonic = time.monotonic()
            answer_intent_at_utc = datetime.now(timezone.utc).isoformat()
            state_backend = get_state_backend()
            register_answer_intent = getattr(
                state_backend,
                "register_answer_intent_cleanup_obligation",
                None,
            )
            if not callable(register_answer_intent):
                raise RuntimeError("telephony state backend lacks durable Answer intent")
            await register_answer_intent(
                channel_id,
                answer_requested_at=answer_intent_at_utc,
                tenant_id=str(admission.get("tenant_id")),
                campaign_id=(
                    str(admission.get("campaign_id")) if admission.get("campaign_id") else None
                ),
                durable_call_id=str(admission.get("call_id")),
                provider=str(admission.get("provider") or "asterisk"),
                provider_call_id=str(admission.get("provider_call_id") or channel_id),
            )
            admission["_answer_intent_at_monotonic"] = answer_intent_at_monotonic
            admission["_answer_intent_at_utc"] = answer_intent_at_utc
            cached_admission = self._inbound_admissions.get(channel_id)
            if cached_admission is not None:
                cached_admission["_answer_intent_at_monotonic"] = answer_intent_at_monotonic
                cached_admission["_answer_intent_at_utc"] = answer_intent_at_utc

            # Explicit ARI answer only after the admission service committed
            # the durable row + reservations and returned its pinned snapshot.
            try:
                await self._ari(
                    "POST",
                    f"/channels/{channel_id}/answer",
                    ok=(200, 204),
                )
            except _AriResponseError as exc:
                if exc.status in _DEFINITIVE_ANSWER_REJECTION_STATUSES:
                    # This response proves Answer was rejected. Keep the Redis
                    # intent for crash-safe PBX cleanup, but remove its local
                    # elapsed marker so confirmed absence releases the durable
                    # reservation as a true pre-answer failure.
                    for answer_view in (
                        admission,
                        self._inbound_admissions.get(channel_id),
                    ):
                        if isinstance(answer_view, dict):
                            answer_view.pop("_answer_intent_at_monotonic", None)
                            answer_view.pop("_answer_intent_at_utc", None)
                else:
                    setup_failure_reason = "process_restart_answer_ambiguous"
                raise
            except asyncio.CancelledError:
                # Cancellation while the HTTP request is in flight provides no
                # evidence about whether Asterisk committed Answer.
                setup_failure_reason = "process_restart_answer_ambiguous"
                raise
            except Exception:
                # Timeout, disconnect, and response-parse failures are likewise
                # ambiguous.  PBX deletion is still required, but billing must
                # remain held for carrier/CDR reconciliation rather than being
                # automatically finalized or released.
                setup_failure_reason = "process_restart_answer_ambiguous"
                raise

            # Provider Answer is already billable when the HTTP request
            # returns. Capture both clocks synchronously, then require the
            # domain's PostgreSQL + Redis durability hook before the first
            # bridge/media await. If the hook fails, the outer error path owns
            # confirmation-aware hangup and terminal finalization.
            answered_at_monotonic = time.monotonic()
            answered_at_utc = datetime.now(timezone.utc).isoformat()
            admission["_answered_at_monotonic"] = answered_at_monotonic
            admission["_answered_at_utc"] = answered_at_utc
            cached_admission = self._inbound_admissions.get(channel_id)
            if cached_admission is not None:
                cached_admission["_answered_at_monotonic"] = answered_at_monotonic
                cached_admission["_answered_at_utc"] = answered_at_utc

            answer_persist = self._on_inbound_answered_persist
            if not callable(answer_persist):
                raise RuntimeError("durable inbound Answer callback is unavailable")
            persist_task = asyncio.create_task(
                answer_persist(
                    channel_id,
                    dict(admission),
                    answered_at=answered_at_utc,
                ),
                name=f"inbound-answer-persist:{channel_id}",
            )
            try:
                persisted_timestamp = await asyncio.shield(persist_task)
            except asyncio.CancelledError:
                # Once Answer returns, request cancellation is not permission
                # to abandon the durable write. Let the finite hook finish,
                # then the outer cancellation path proves PBX cleanup.
                try:
                    await persist_task
                except Exception as exc:  # noqa: BLE001 - cleanup still runs
                    logger.critical(
                        "inbound_answer_persist_failed_during_cancellation " "channel=%s err=%s",
                        channel_id[:12],
                        exc,
                    )
                raise
            if persisted_timestamp:
                persisted_text = str(persisted_timestamp)
                admission["_answered_at_utc"] = persisted_text
                if cached_admission is not None:
                    cached_admission["_answered_at_utc"] = persisted_text

            listen_port = await self._alloc_rtp_port()
            session_id = f"asterisk-{uuid.uuid4().hex}"

            # 1. Pre-assign a deterministic ID before the network create. If
            # Asterisk commits the bridge but the response times out, cleanup
            # can still delete the exact resource instead of leaking an
            # unknowable server-generated ID.
            bridge_id = f"talky-inbound-bridge-{uuid.uuid4().hex[:20]}"
            bridge = await self._ari(
                "POST",
                "/bridges",
                params={"type": "mixing", "bridgeId": bridge_id},
            )
            returned_bridge_id = str((bridge or {}).get("id") or "").strip()
            if returned_bridge_id:
                bridge_id = returned_bridge_id

            # 2. Add caller channel to bridge
            await self._ari(
                "POST",
                f"/bridges/{bridge_id}/addChannel",
                params={"channel": channel_id},
                ok=(200, 204, 209),
            )

            # 3. Create ExternalMedia channel pointing at C++ Gateway RTP listener
            # As with bridges, channelId is chosen before POST so an ambiguous
            # timeout-after-create remains recoverable.
            ext_channel_id = f"talky-inbound-media-{uuid.uuid4().hex[:20]}"
            ext_data = await self._ari(
                "POST",
                "/channels/externalMedia",
                params={
                    "app": self._app_name,
                    "external_host": f"{self._gateway_rtp_ip}:{listen_port}",
                    "format": "ulaw",
                    "encapsulation": "rtp",
                    "transport": "udp",
                    "connection_type": "client",
                    "direction": "both",
                    "channelId": ext_channel_id,
                },
            )
            returned_ext_channel_id = str((ext_data or {}).get("id") or "").strip()
            if returned_ext_channel_id:
                ext_channel_id = returned_ext_channel_id

            # 4/5. addChannel + two UNICASTRTP_LOCAL_* GETs are independent of
            # each other (share only ext_channel_id), so run them concurrently.
            # Mirrors the same optimisation in _on_outbound_answered.
            add_coro = self._ari(
                "POST",
                f"/bridges/{bridge_id}/addChannel",
                params={"channel": ext_channel_id},
                ok=(200, 204, 209),
            )
            _, (remote_ip, remote_port) = await asyncio.gather(
                add_coro,
                self._resolve_unicastrtp_local(channel_id=ext_channel_id, channel=ext_data),
            )

            # 6. Start C++ Gateway session (AI mode: echo_enabled=False once TTS hooked in)
            await self._start_gateway_session(
                {
                    "session_id": session_id,
                    "listen_ip": self._gateway_rtp_ip,
                    "listen_port": listen_port,
                    "remote_ip": remote_ip,
                    "remote_port": remote_port,
                    "codec": "pcmu",
                    "ptime_ms": 20,
                    "echo_enabled": False,
                    # Increase timeouts for AI pipeline initialization
                    "startup_no_rtp_timeout_ms": 30000,  # 30 seconds (was 5s default)
                    "active_no_rtp_timeout_ms": 15000,  # 15 seconds (was 8s default)
                    # Maximum call lifetime (two hours by default).
                    "session_final_timeout_ms": _SESSION_FINAL_TIMEOUT_MS,
                    # Loopback has no jitter; one frame is 20ms (formerly 3).
                    "jitter_buffer_prefetch_frames": 1,
                    # 2 frames = 40ms = Deepgram Flux's optimal chunk size. Was 4 (80ms) when
                    # we re-batched downstream; now we hand off frames at Flux's native rate so
                    # there's no re-chunking jitter and per-call resamples drop by 50%.
                    # Sourced from telephony.config so the gap detector, which
                    # derives its threshold from the same constant, cannot drift
                    # out of step with it again (it did: this went 4 -> 2 while
                    # the detector kept assuming 80ms).
                    "audio_callback_batch_frames": AUDIO_CALLBACK_BATCH_FRAMES,
                    "enforce_rtp_source": True,
                    # VG-01 sequence-ordered STT tap (see _stt_reorder_config).
                    **self._stt_reorder_config(),
                    # Optional TTS queue cap (see _tts_queue_config).
                    **self._tts_queue_config(),
                    # Tell the gateway to POST audio chunks to our backend callback
                    "audio_callback_url": (
                        f"{os.getenv('BACKEND_INTERNAL_URL', 'http://127.0.0.1:8000')}"
                        f"/api/v1/sip/telephony/audio/{session_id}"
                    ),
                }
            )

            # Track the session
            self._active_sessions[channel_id] = {
                "session_id": session_id,
                "listen_port": listen_port,
                "bridge_id": bridge_id,
                "direction": "inbound",
                "answered_at_monotonic": answered_at_monotonic,
                "first_agent_audio_recorded": False,
            }
            self._ext_channels[channel_id] = ext_channel_id
            self._bridges[channel_id] = bridge_id
            self._gateway_sessions[channel_id] = session_id

            logger.info(
                f"AsteriskAdapter: session started channel={channel_id[:12]} "
                f"session={session_id} rtp_port={listen_port} remote={remote_ip}:{remote_port}"
            )

            # 7. Notify any registered callback for this call_id
            cb = self._call_arrived_callbacks.get(channel_id)
            if cb:
                asyncio.create_task(cb(channel_id))
            elif self._on_new_call:
                # The admission object is passed directly as well as cached so
                # lifecycle startup never performs a second route lookup and a
                # very fast hangup cannot race the hand-off.
                self._schedule_inbound_handoff(channel_id, admission)
            else:
                self.reject_pending_inbound_handoff(
                    channel_id,
                    reason="lifecycle_callback_unavailable",
                )

        except asyncio.CancelledError:
            self._schedule_failed_inbound_setup_cleanup(
                channel_id,
                reason=setup_failure_reason,
                session_id=session_id,
                ext_channel_id=ext_channel_id,
                bridge_id=bridge_id,
                listen_port=listen_port,
            )
            raise
        except Exception as exc:
            logger.error(f"AsteriskAdapter: session start failed: {exc}")
            await self._cleanup_failed_inbound_setup_now_or_schedule(
                channel_id,
                reason=setup_failure_reason,
                session_id=session_id,
                ext_channel_id=ext_channel_id,
                bridge_id=bridge_id,
                listen_port=listen_port,
            )
        finally:
            self._inbound_setup_inflight.discard(channel_id)

    async def _cleanup_pending_outbound(
        self,
        channel_id: str,
        *,
        absence_proven: bool = False,
    ) -> None:
        """Release resources for an outbound call that was never answered."""
        pending = self._pending_outbound.get(channel_id)
        if not pending:
            return
        while not absence_proven:
            if await self.hangup_confirmed(channel_id):
                absence_proven = True
                break
            logger.critical(
                "AsteriskAdapter: pending outbound termination unconfirmed "
                "channel=%s; lifecycle settlement deferred",
                channel_id[:12],
            )
            await asyncio.sleep(max(0.05, self._inbound_cleanup_retry_s))
        self._pending_outbound.pop(channel_id, None)
        await self._release_rtp_port(pending["listen_port"])
        bridge_id = pending.get("bridge_id", "")
        if bridge_id:
            try:
                await self._ari("DELETE", f"/bridges/{bridge_id}", ok=(200, 204, 404))
            except Exception:
                pass
        logger.info(
            f"AsteriskAdapter: unanswered outbound call cleaned up channel={channel_id[:12]}"
        )

        # Signal the bridge so it can release any ringing-phase VoiceSession
        # that was pre-created for this channel.  Without this hook, an
        # abandoned ring would leak the STT/TTS WebSocket connections that
        # _on_ringing opened during the ring window.
        if self._on_any_call_end is not None:
            try:
                await self._on_any_call_end(channel_id)
            except Exception as exc:
                logger.debug(f"on_any_call_end dispatch failed for {channel_id[:12]}: {exc}")

    def _emit_interrupt_audio_audit(self, call_id: str) -> None:
        """One line per call stating whether cancelled audio ever resumed.

        Emitted for EVERY call that had at least one barge-in, including — in
        fact especially — the calls where nothing went wrong. A verdict that is
        only written when something fails cannot be told apart from a verdict
        that was never computed, which is the exact reporting failure that let
        two dead STT streams look like quiet callers on 2026-08-13.
        """
        utt = self._tts_utterances.get(call_id)
        if not utt:
            return
        interrupts = int(utt.get("total_interrupts", 0) or 0)
        if not interrupts:
            return  # no barge-in on this call; nothing to attest to
        resumed = int(utt.get("total_resumed_chunks", 0) or 0)
        rejected = int(utt.get("total_stale_rejected", 0) or 0)
        log = logger.warning if resumed else logger.info
        log(
            "interrupt_audio_audit call=%s interrupts=%d resumed_chunks=%d "
            "stale_rejected=%d verdict=%s",
            call_id[:12],
            interrupts,
            resumed,
            rejected,
            "AUDIO_RESUMED" if resumed else "clean",
        )

    async def _on_stasis_end(
        self,
        channel_id: str,
        reason: str,
        *,
        absence_proven: bool = False,
    ) -> None:
        """Tear down C++ Gateway session and ARI bridge when a call ends."""
        setup_task = self._inbound_setup_tasks.get(channel_id)
        if (
            setup_task is not None
            and not setup_task.done()
            and setup_task is not asyncio.current_task()
        ):
            # A caller may hang up while answer/media setup is awaiting ARI or
            # the gateway. Cancel setup while its local deterministic IDs are
            # still available; its CancelledError path becomes the sole cleanup
            # owner before this event is allowed to finalize anything.
            setup_task.cancel()
            await asyncio.gather(setup_task, return_exceptions=True)

        handoff_task = self._inbound_handoff_tasks.get(channel_id)
        if (
            channel_id in self._inbound_handoff_accepted
            and handoff_task is not None
            and not handoff_task.done()
            and handoff_task is not asyncio.current_task()
        ):
            # After explicit acceptance lifecycle owns the VoiceSession, but
            # its finite initialization still mutates that session. Let it
            # finish before teardown so no provider/pipeline task can be
            # created after `_on_call_ended` has popped and ended the session.
            try:
                await asyncio.shield(handoff_task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # initialization teardown continues
                logger.warning(
                    "accepted_inbound_handoff_failed_before_teardown " "channel=%s err=%s",
                    channel_id[:12],
                    exc,
                )

        # The accepted initialization task is now finished (or was already
        # absent), so terminal teardown can retire its ownership marker. This
        # also covers initialization-failure paths whose durable finalizer ran
        # before the adapter received the resulting ARI terminal event.
        self._inbound_handoff_accepted.discard(channel_id)

        if channel_id in self._inbound_cleanup_pending:
            logger.info(
                "inbound_stasis_end_delegated_to_cleanup channel=%s reason=%s",
                channel_id[:12],
                reason,
            )
            return

        # ChannelHangupRequest is only intent and StasisEnd only says the
        # channel left this ARI app; neither proves the human/PSTN leg stopped
        # billing. A connected or failed transfer adds another owned leg. Do
        # not pop media/admission state or invoke lifecycle until every owned
        # leg is absent. ChannelDestroyed for the *same parent* is the one event
        # that can enter with ``absence_proven=True``.
        if channel_id in self._transfers_by_parent:
            while channel_id in self._transfers_by_parent:
                if await self._cleanup_transfer_for_parent(channel_id):
                    absence_proven = True
                    break
                await asyncio.sleep(max(0.05, self._inbound_cleanup_retry_s))
        while not absence_proven:
            if await self.hangup_confirmed(channel_id):
                absence_proven = True
                break
            logger.critical(
                "AsteriskAdapter: terminal event lacks parent absence proof "
                "channel=%s reason=%s; lifecycle settlement deferred",
                channel_id[:12],
                reason,
            )
            await asyncio.sleep(max(0.05, self._inbound_cleanup_retry_s))

        self._record_terminal_at_monotonic(channel_id)
        while not await self._persist_inbound_terminal_proof_for_channel(channel_id):
            logger.critical(
                "AsteriskAdapter: terminal proof is not durable; cleanup deferred "
                "channel=%s reason=%s",
                channel_id[:12],
                reason,
            )
            await asyncio.sleep(max(0.05, self._inbound_cleanup_retry_s))

        session_info = self._active_sessions.pop(channel_id, None)
        if (
            session_info is None
            and channel_id in self._inbound_admissions
            and channel_id not in self._inbound_cleanup_pending
        ):
            # Admission succeeded but lifecycle never took ownership (for
            # example, the caller hung up during answer/media setup).
            await self._release_cached_inbound_admission(
                channel_id,
                reason="pre_lifecycle_hangup",
            )
        elif channel_id not in self._inbound_cleanup_pending:
            # A normal active call is finalized by lifecycle with its measured
            # duration. Drop only the adapter's hand-off copy here.
            self._inbound_admissions.pop(channel_id, None)
        ext_channel_id = self._ext_channels.pop(channel_id, None)
        bridge_id = self._bridges.pop(channel_id, None)
        session_id = self._gateway_sessions.pop(channel_id, None)
        self._tts_error_counts.pop(channel_id, None)
        self._emit_interrupt_audio_audit(channel_id)
        self._tts_utterances.pop(channel_id, None)
        self._inbound_call_meta.pop(channel_id, None)

        if session_id:
            try:
                await self._gateway(
                    "POST",
                    "/v1/sessions/stop",
                    payload={"session_id": session_id, "reason": reason},
                )
            except Exception as exc:
                logger.debug(f"AsteriskAdapter: gateway stop error: {exc}")

        if ext_channel_id:
            self._drop_channel_varset_cache(ext_channel_id)
            try:
                await self._ari("DELETE", f"/channels/{ext_channel_id}", ok=(200, 204, 404))
            except Exception:
                pass

        if bridge_id:
            try:
                await self._ari("DELETE", f"/bridges/{bridge_id}", ok=(200, 204, 404))
            except Exception:
                pass

        # Ensure the parent (caller) leg is torn down. This teardown may have
        # been triggered by the EXTERNAL media leg dying (its id is a value in
        # _ext_channels, dispatched with the PARENT id) while the parent PJSIP
        # channel is still Up — deleting the bridge does NOT hang the parent up,
        # so without this the caller is left on dead air (and billed) until a
        # reaper sweeps it. In the normal case (the parent's own terminal event
        # drove this teardown) the channel is already gone and the DELETE is a
        # harmless 404, so this never hangs up a healthy call. Idempotent.
        try:
            await self._ari("DELETE", f"/channels/{channel_id}", ok=(200, 204, 404))
        except Exception as exc:
            logger.debug(f"AsteriskAdapter: parent hangup on teardown ({channel_id[:12]}): {exc}")

        if session_info and isinstance(session_info.get("listen_port"), int):
            await self._release_rtp_port(session_info["listen_port"])

        logger.info(f"AsteriskAdapter: session ended channel={channel_id[:12]} reason={reason}")

        cb = self._call_ended_callbacks.get(channel_id)
        if cb:
            await cb(channel_id)
        elif self._on_any_call_end:
            await self._on_any_call_end(channel_id)

    # ------------------------------------------------------------------
    # CallControlAdapter — call event callbacks
    # ------------------------------------------------------------------

    async def on_call_arrived(self, call_id: str, callback: Callable[..., Coroutine]) -> None:
        self._call_arrived_callbacks[call_id] = callback

    async def on_call_ended(self, call_id: str, callback: Callable[..., Coroutine]) -> None:
        self._call_ended_callbacks[call_id] = callback

    def set_new_call_callback(self, callback: Callable) -> None:
        """Global callback invoked for every new inbound call (call_id is passed as arg)."""
        self._on_new_call = callback

    def set_call_end_callback(self, callback: Callable) -> None:
        """Global callback invoked when any call ends."""
        self._on_any_call_end = callback

    def _dispatch_early_ringing(self, channel_id: str) -> None:
        """Fire the early-ringing callback once per outbound channel.

        Shared by ChannelStateChange(Ringing) and Dial(dialstatus=RINGING) —
        whichever the carrier actually delivers first wins; the dedupe set
        makes the other a no-op."""
        if (
            not channel_id
            or self._on_early_ringing is None
            or channel_id in self._early_ring_emitted
            or not (channel_id in self._originated_channels or channel_id.startswith("talky-out"))
        ):
            return
        self._early_ring_emitted.add(channel_id)
        logger.info(f"AsteriskAdapter: outbound channel early-ringing channel={channel_id[:12]}")
        asyncio.create_task(self._on_early_ringing(channel_id))

    def set_early_ringing_callback(self, callback: Callable) -> None:
        """Register the EARLY-ringing (ChannelStateChange Ringing) callback.

        Fired once per outbound channel the moment the carrier reports 180
        Ringing — used by the bridge to advance the live call status to
        "ringing" in real time. Status-only; never used for warmup."""
        self._on_early_ringing = callback

    def set_ringing_callback(self, callback: Callable) -> None:
        """
        Optional callback invoked once an outbound channel has been parked
        in its mixing bridge and is waiting for the callee to answer.
        Used by the telephony bridge for ringing-phase provider warmup.
        Signature: `async def callback(channel_id: str) -> None`.
        """
        self._on_ringing = callback

    def set_outbound_channel_alias_callback(self, callback: Callable) -> None:
        """
        Optional callback invoked when ARI reports a different outbound channel
        ID than the one passed to originate_call(channel_id=...).
        Signature: `def callback(original_call_id: str, actual_call_id: str)`.
        """
        self._on_outbound_channel_alias = callback

    def set_inbound_admission_callback(self, callback: Callable) -> None:
        """Register the pre-answer inbound admission callback.

        Signature: ``async callback(pbx_call_id: str, metadata: dict)``. The
        returned object must expose ``allowed``, a durable ``call_id``, and
        ``tenant_id`` (either attributes or ``to_dict()``).
        """
        self._on_inbound_admission = callback

    def set_inbound_rejection_persist_callback(self, callback: Callable) -> None:
        """Register durable persistence for denials with no ``calls`` row."""

        self._on_inbound_rejection_persist = callback

    def set_inbound_answered_persist_callback(self, callback: Callable) -> None:
        """Register the mandatory post-Answer durability fence.

        Signature: ``async callback(pbx_call_id, admission, answered_at=...)``.
        The callback must not return until both the calls row and cleanup
        ledger contain Answer truth. Missing/failing callbacks keep inbound
        media fail-closed.
        """
        self._on_inbound_answered_persist = callback

    def set_inbound_terminal_proof_persist_callback(self, callback: Callable) -> None:
        """Register the mandatory pre-cleanup terminal durability fence.

        Signature: ``async callback(pbx_call_id, admission,
        terminated_at=..., duration_seconds=...)``. Gateway, bridge and
        lifecycle cleanup remain deferred until it succeeds.
        """

        self._on_inbound_terminal_proof_persist = callback

    def set_inbound_admission_finalizer(self, callback: Callable) -> None:
        """Register cleanup/finalization for an already-admitted call."""
        self._on_inbound_admission_finalize = callback

    def set_transfer_connected_callback(self, callback: Callable) -> None:
        """Register the lifecycle hook fired after a target really answers."""
        self._on_transfer_connected = callback

    def set_transfer_answered_persist_callback(self, callback: Callable) -> None:
        """Register the mandatory durable Answer hook run before success."""
        self._on_transfer_answered_persist = callback

    def set_transfer_provider_identity_persist_callback(
        self,
        callback: Callable,
    ) -> None:
        """Register the mandatory durable hook for an ARI returned-ID rebind."""
        self._on_transfer_provider_identity_persist = callback

    def set_transfer_cleanup_confirmed_callback(self, callback: Callable) -> None:
        """Register durable child-leg settlement after target absence proof."""
        self._on_transfer_cleanup_confirmed = callback

    def register_call_event_handlers(
        self,
        on_new_call: Callable,
        on_call_ended: Callable,
        on_audio_received: Optional[Callable] = None,
    ) -> None:
        """Wire bridge-level callbacks into ARI event handlers."""
        self._on_new_call = on_new_call
        self._on_any_call_end = on_call_ended

    # ------------------------------------------------------------------
    # CallControlAdapter — audio I/O
    # ------------------------------------------------------------------

    async def start_audio_stream(self, call_id: str) -> None:
        """
        Audio streaming from caller starts automatically via the C++ Gateway
        audio_callback_url set during session creation.
        This method is a no-op for Asterisk (streaming begins at session start).
        """
        logger.debug(
            f"AsteriskAdapter.start_audio_stream: streaming already active for {call_id[:12]}"
        )

    async def send_tts_audio(self, call_id: str, pcmu_audio: bytes) -> None:
        """
        Send TTS audio to the caller via the C++ Gateway.

        Endpoint: POST /v1/sessions/tts/play
        Body: {"session_id": "...", "pcmu_base64": "...", "clear_existing": false}
        """
        session_id = self._gateway_sessions.get(call_id)
        if not session_id:
            # No live gateway session — the packet cannot reach the caller.
            # Rate-limit our own log (this fires transiently during teardown),
            # but RAISE so the caller does not count silence as played audio.
            count = self._tts_error_counts.get(call_id, 0) + 1
            self._tts_error_counts[call_id] = count
            if count == 1 or count % 50 == 0:
                logger.warning(
                    f"[AsteriskAdapter] send_tts_audio: no gateway session for "
                    f"call_id={call_id[:12]} (undelivered packets={count})"
                )
            raise TtsDeliveryError(f"no gateway session for call_id={call_id[:12]}")

        import base64
        import uuid as _uuid

        # Stamp the chunk with its utterance identity (VG-13). The id lives for
        # the whole utterance; interrupt_tts rotates it, after which the gateway
        # 409s any straggler ALREADY STAMPED with the old id. Gaps in chunk_seq
        # are fine — the gateway only requires monotonic increase per utterance.
        # A chunk arriving here after the rotation picks up the new id and is
        # accepted; that residual window is counted below, not assumed away.
        utt = self._tts_utterances.get(call_id)
        if utt is None:
            utt = {"utterance_id": _uuid.uuid4().hex[:16], "seq": 0}
            self._tts_utterances[call_id] = utt
        chunk_seq = utt["seq"]
        utt["seq"] = chunk_seq + 1

        try:
            pcmu_b64 = base64.b64encode(pcmu_audio).decode()

            await self._gateway(
                "POST",
                "/v1/sessions/tts/play",
                payload={
                    "session_id": session_id,
                    "pcmu_base64": pcmu_b64,
                    "clear_existing": False,
                    "utterance_id": utt["utterance_id"],
                    "chunk_seq": chunk_seq,
                },
            )
            # Reset error counter on first successful delivery.
            self._tts_error_counts.pop(call_id, None)

            # The gateway has accepted the packet, which is the earliest local
            # proof that agent audio is queued for the caller. Measure exactly
            # once from the confirmed provider Answer; failed gateway writes
            # deliberately never count as first audio.
            try:
                active_session = getattr(self, "_active_sessions", {}).get(call_id)
                if (
                    isinstance(active_session, dict)
                    and active_session.get("direction") == "inbound"
                    and active_session.get("first_agent_audio_recorded") is not True
                ):
                    answered_at = active_session.get("answered_at_monotonic")
                    if isinstance(answered_at, (int, float)):
                        now = time.monotonic()
                        elapsed_s = max(0.0, now - float(answered_at))
                        active_session["first_agent_audio_recorded"] = True
                        active_session["first_agent_audio_at_monotonic"] = now
                        from app.infrastructure.metrics.inbound_metrics import (
                            record_inbound_answer_to_first_audio,
                        )

                        record_inbound_answer_to_first_audio(elapsed_s)
                        logger.info(
                            "inbound_first_agent_audio channel=%s " "answer_to_first_audio_ms=%.1f",
                            call_id[:12],
                            elapsed_s * 1000.0,
                        )
            except Exception as exc:  # noqa: BLE001 - audio already succeeded
                logger.warning(
                    "inbound_first_agent_audio_observation_failed channel=%s err=%s",
                    call_id[:12],
                    exc,
                )

            # DID CANCELLED AUDIO RESUME? The gateway just ACCEPTED this chunk.
            # If we are still inside the window after an interrupt, this is the
            # cancelled generation getting through — the caller hears the agent
            # carry on after being told to stop. Recorded, never suppressed:
            # blocking here on an unproven hypothesis could mute a legitimate
            # turn, which is the worse of the two failures.
            retired_at = utt.get("retired_at")
            if retired_at is not None:
                age_s = time.monotonic() - retired_at
                if age_s <= _RESUME_WINDOW_S:
                    utt["resumed_chunks"] = utt.get("resumed_chunks", 0) + 1
                    utt["resumed_bytes"] = utt.get("resumed_bytes", 0) + len(pcmu_audio)
                    utt["total_resumed_chunks"] = utt.get("total_resumed_chunks", 0) + 1
                    # First few only — a genuine resumption is a burst, and the
                    # per-call audit line at teardown carries the full total.
                    if utt["resumed_chunks"] <= 3:
                        logger.warning(
                            "tts_resumed_after_interrupt call=%s ms_after=%.0f "
                            "chunk=%d bytes=%d — audio from the CANCELLED "
                            "utterance was accepted by the gateway after the "
                            "barge-in; the caller heard the agent continue",
                            call_id[:12],
                            age_s * 1000.0,
                            utt["resumed_chunks"],
                            len(pcmu_audio),
                        )
                else:
                    # Window closed with a legitimate next turn. Stop attributing.
                    utt.pop("retired_at", None)
        except Exception as exc:
            # A 409 is the idempotency gate doing its job: this chunk belongs to
            # an utterance that was already barged-in/replaced. Expected after an
            # interrupt — count it undelivered but don't log it as a failure.
            stale = "409" in str(exc)
            if stale:
                # POSITIVE evidence the idempotency gate worked. Counted so the
                # teardown audit can say "the gate rejected N stragglers" rather
                # than reporting silence, which would be indistinguishable from
                # the gate never having been exercised.
                utt["total_stale_rejected"] = utt.get("total_stale_rejected", 0) + 1
            count = self._tts_error_counts.get(call_id, 0) + 1
            self._tts_error_counts[call_id] = count
            if stale:
                logger.debug(
                    f"[AsteriskAdapter] stale TTS chunk rejected post-barge-in "
                    f"for {call_id[:12]} (seq={chunk_seq})"
                )
            elif count == 1:
                logger.error(f"[AsteriskAdapter] ❌ send_tts_audio failed: {exc}")
            elif count % 50 == 0:
                logger.warning(
                    f"[AsteriskAdapter] send_tts_audio still failing for {call_id[:12]} "
                    f"({count} errors total) — last error: {exc}"
                )
            # Re-raise so the media-gateway send loop treats this packet as
            # undelivered (does not advance chunks_sent / history). The caller
            # already guards every send_tts_audio call in try/except, so this
            # does not crash the audio path. TtsDeliveryError wraps the cause.
            raise TtsDeliveryError(str(exc)) from exc

    async def interrupt_tts(self, call_id: str) -> Dict[str, Any]:
        """
        Stop playing TTS audio via the C++ Gateway interrupt endpoint.

        Endpoint: POST /v1/sessions/tts/interrupt
        Body: {"session_id": "...", "reason": "barge_in"}

        A FAILED interrupt means the agent keeps talking over the caller, so
        failures are surfaced at WARNING and retried once (they were previously
        swallowed at debug level — invisible in production logs). The utterance
        id is rotated in every case so post-barge-in straggler chunks are
        rejected by the gateway's idempotency gate (VG-13) even when the
        interrupt POST itself failed.

        RETURNS THE GATEWAY'S ACKNOWLEDGEMENT (2026-08-08). The C++ endpoint has
        always replied with ``dropped_frames`` and ``interrupted_segments``
        (http_server.cpp:1697-1703) and Python threw the body away, so "did the
        agent actually stop, and how much audio did we bin?" was unanswerable
        from this side. Now returned so one caller can log and meter it:

            {"ok": bool, "dropped_frames": int, "interrupted_segments": int,
             "attempts": int, "error": str|None, "session_id": str|None}

        ``ok`` False means the agent may STILL BE SPEAKING — treat it as a real
        failure, never as best-effort.
        """
        import uuid as _uuid

        result: Dict[str, Any] = {
            "ok": False,
            "dropped_frames": 0,
            "interrupted_segments": 0,
            "attempts": 0,
            "error": None,
            "session_id": None,
        }

        session_id = self._gateway_sessions.get(call_id)
        if not session_id:
            # No gateway session: nothing is playing, so nothing to stop. This
            # is success-by-vacancy, not failure — distinguish it from a real
            # interrupt so the metric does not count teardown races as errors.
            result["ok"] = True
            result["error"] = "no_gateway_session"
            return result
        result["session_id"] = session_id
        try:
            payload = {"session_id": session_id, "reason": "barge_in"}
            try:
                result["attempts"] = 1
                ack = await self._gateway(
                    "POST", "/v1/sessions/tts/interrupt", payload=payload, ok=(200, 204, 404)
                )
                result["ok"] = True
            except Exception as exc:
                logger.warning(
                    f"[AsteriskAdapter] interrupt_tts failed for {call_id[:12]} "
                    f"(agent may keep speaking) — retrying once: {exc}"
                )
                try:
                    result["attempts"] = 2
                    ack = await self._gateway(
                        "POST", "/v1/sessions/tts/interrupt", payload=payload, ok=(200, 204, 404)
                    )
                    result["ok"] = True
                except Exception as exc2:
                    logger.error(
                        f"[AsteriskAdapter] ❌ interrupt_tts retry failed for "
                        f"{call_id[:12]} session={session_id} — stale audio may "
                        f"play out: {exc2}"
                    )
                    result["error"] = str(exc2)
                    ack = None
            if isinstance(ack, dict):
                result["dropped_frames"] = int(ack.get("dropped_frames") or 0)
                result["interrupted_segments"] = int(ack.get("interrupted_segments") or 0)
        finally:
            # Rotate AFTER the interrupt attempt: the gateway retired the id it
            # held as current; chunks still in flight with that id now 409,
            # while the next agent turn's chunks carry the fresh id.
            utt = self._tts_utterances.get(call_id)
            if utt is not None:
                utt["utterance_id"] = _uuid.uuid4().hex[:16]
                utt["seq"] = 0
                result["utterance_rotated"] = True
                # Open the attribution window: any chunk the gateway ACCEPTS
                # from here until _RESUME_WINDOW_S elapses belongs to the
                # utterance we just cancelled. Per-interrupt counters reset;
                # the total_* keys deliberately do not, so the teardown audit
                # covers the whole call rather than only its last barge-in.
                utt["retired_at"] = time.monotonic()
                utt["resumed_chunks"] = 0
                utt["resumed_bytes"] = 0
                utt["total_interrupts"] = utt.get("total_interrupts", 0) + 1

        return result

    # ------------------------------------------------------------------
    # CallControlAdapter — call control
    # ------------------------------------------------------------------

    async def originate_call(
        self,
        destination: str,
        caller_id: str,
        channel_id: Optional[str] = None,
        trunk_endpoint: Optional[str] = None,
    ) -> str:
        """
        Originate an outbound call via ARI that rings the destination phone.

        For outbound calls, ARI creates a channel to the target endpoint with
        app=talky_ai.  When the called party answers the channel enters Stasis,
        _on_stasis_start fires, and the ExternalMedia bridge + AI pipeline are
        attached — exactly the same flow as inbound calls.

        Two strategies depending on the destination:
          1. Internal/test extensions (e.g. 750) → Local channel through dialplan
          2. Real PBX extensions → Direct PJSIP dial (so audio goes through our
             mixing bridge, not a separate Dial()-created media path)
        """
        if destination == "750":
            # Test extension: route through dialplan
            endpoint = f"Local/{destination}@from-opensips"
            # Pre-generate channel ID and register it BEFORE the ARI POST.
            # This prevents a race condition where the StasisStart WS event
            # arrives before the HTTP response — at that point the channel
            # would NOT be in _originated_channels and would be mis-routed
            # to the inbound handler.
            pre_id = channel_id or f"talky-out-{uuid.uuid4()}"
            self._track_originated_channel(pre_id)
            try:
                data = await self._ari(
                    "POST",
                    "/channels",
                    params={
                        "endpoint": endpoint,
                        "callerId": caller_id,
                        "app": self._app_name,
                        "appArgs": "outbound",
                        "channelId": pre_id,
                    },
                )
            except Exception:
                self._discard_originated_channel(pre_id)
                raise
            channel_id = data.get("id", pre_id)
            # ARI should use our pre_id, but if it returns something else,
            # update the tracking set.
            if channel_id != pre_id:
                self._discard_originated_channel(pre_id)
                self._track_originated_channel(channel_id)
            logger.info(
                f"AsteriskAdapter: originated test call to {destination} channel={channel_id[:12]}"
            )
            return channel_id

        # -------------------------------------------------------------------
        # Real extensions: originate through a PJSIP trunk.
        #
        # Endpoint is configurable via TELEPHONY_PJSIP_OUTBOUND_ENDPOINT so
        # production can route through the upstream carrier (default:
        # blazedigitel-endpoint, registered in /etc/asterisk/pjsip.conf)
        # while local dev can still target lan-pbx by setting the env var.
        #
        # Per-tenant isolation: the caller may pass an explicit
        # ``trunk_endpoint`` (e.g. ``trunk-<trunkid>`` for a BYO/own trunk).
        # When None — the historical behaviour — we fall back to the global
        # env endpoint, so default-trunk tenants are byte-for-byte unchanged.
        # -------------------------------------------------------------------
        import os as _os

        trunk = trunk_endpoint or _os.getenv(
            "TELEPHONY_PJSIP_OUTBOUND_ENDPOINT", "blazedigitel-endpoint"
        )
        endpoint = f"PJSIP/{destination}@{trunk}"

        # Ring timeout (seconds): how long Asterisk lets the destination ring
        # before giving up. Without it ARI defaults to 30s, but making it
        # explicit + env-tunable lets us enforce a "natural" ring window — a
        # call that isn't answered within it is torn down by Asterisk with a
        # no-answer cause, which the outcome resolver maps to NO_ANSWER and the
        # disposition policy reschedules for +24h (never the same day).
        ring_timeout = int(_os.getenv("DIALER_RING_TIMEOUT_S", "30"))

        # Pre-generate channel ID and register BEFORE ARI POST to prevent
        # the StasisStart WS event from arriving before the HTTP response.
        pre_id = channel_id or f"talky-out-{uuid.uuid4()}"
        self._track_originated_channel(pre_id)

        try:
            data = await self._ari(
                "POST",
                "/channels",
                params={
                    "endpoint": endpoint,
                    "callerId": caller_id,
                    "app": self._app_name,
                    "appArgs": "outbound",
                    "channelId": pre_id,
                    "timeout": ring_timeout,
                },
            )
        except Exception:
            self._discard_originated_channel(pre_id)
            raise

        channel_id = data.get("id", pre_id)
        # ARI should use our pre_id, but if it returns something else,
        # update the tracking set.
        if channel_id != pre_id:
            self._discard_originated_channel(pre_id)
            self._track_originated_channel(channel_id)

        logger.info(
            "AsteriskAdapter: originated call to %s via %s channel=%s",
            destination,
            endpoint,
            channel_id[:12],
        )

        # Safety: remove the pre-generated ID from _originated_channels after
        # 30 seconds.  For PJSIP trunk calls, the actual StasisStart channel
        # has a different ID; the trunk-leg matcher in _handle_ari_event will
        # consume it.  This timer prevents stale entries from leaking if the
        # origination fails silently (no StasisStart at all).
        async def _expire_originated(cid: str) -> None:
            await asyncio.sleep(30)
            if cid in self._originated_channels:
                self._discard_originated_channel(cid)
                logger.debug(f"AsteriskAdapter: expired stale originated channel {cid[:12]}")

        asyncio.create_task(_expire_originated(channel_id))

        return channel_id

    async def hangup_many_confirmed(
        self,
        call_ids: Any,
        *,
        fence_root: bool = True,
        reason_code: Optional[int] = None,
    ) -> bool:
        """Fence transfer creation, then remove and prove explicit legs absent.

        The first ID is the logical root for public/domain calls. Marking it
        before the first await closes the authorize/terminate race; the shared
        setup lock then waits out any ARI create/dial already in flight before
        absence can be proved. Target-only maintenance calls opt out because
        they must never fence or expand a still-live parent.
        """

        owned_channel_ids = tuple(dict.fromkeys(str(value) for value in call_ids if value))
        if not owned_channel_ids:
            return False
        if fence_root:
            self._termination_fenced_call_ids.add(owned_channel_ids[0])
        async with self._transfer_setup_lock:
            confirmed = await self._hangup_many_confirmed_locked(
                owned_channel_ids,
                reason_code=reason_code,
            )
        if confirmed and self._is_inbound_parent_channel(owned_channel_ids[0]):
            self._record_terminal_at_monotonic(owned_channel_ids[0])
            confirmed = await self._persist_inbound_terminal_proof_for_channel(
                owned_channel_ids[0]
            )
        return confirmed

    async def _delete_channel_with_reason_fallback(
        self,
        channel_id: str,
        *,
        reason_code: Optional[int],
        timeout_s: float,
    ) -> Any:
        """Request one ARI hangup, retrying bare if cause delivery fails.

        Some deployed/older ARI builds can reject an otherwise documented
        ``reason_code`` query. A rejected reason must never turn into a live,
        unanswered, billable channel, so the compatibility retry happens in
        this same cleanup iteration. The shared confirmation deadline still
        bounds both requests and the subsequent absence proof.
        """

        if reason_code is not None and reason_code not in {1, 17, 21, 42}:
            raise ValueError("unsupported inbound hangup reason_code")

        started = asyncio.get_running_loop().time()
        request_timeout = max(0.001, float(timeout_s))
        request_kwargs: Dict[str, Any] = {
            "ok": (200, 204, 404),
            "return_status": True,
        }
        if reason_code is not None:
            # Reserve half of this iteration's remaining budget for the bare
            # compatibility request if the reasoned request stalls.
            request_timeout = max(0.001, request_timeout / 2.0)
            request_kwargs["params"] = {"reason_code": str(reason_code)}
        try:
            return await asyncio.wait_for(
                self._ari(
                    "DELETE",
                    f"/channels/{channel_id}",
                    **request_kwargs,
                ),
                timeout=request_timeout,
            )
        except Exception as exc:
            if reason_code is None:
                raise
            logger.warning(
                "inbound_reasoned_hangup_failed channel=%s reason_code=%d " "fallback=bare err=%s",
                channel_id[:12],
                reason_code,
                exc,
            )

        elapsed = asyncio.get_running_loop().time() - started
        remaining = max(0.001, float(timeout_s) - elapsed)
        return await asyncio.wait_for(
            self._ari(
                "DELETE",
                f"/channels/{channel_id}",
                ok=(200, 204, 404),
                return_status=True,
            ),
            timeout=remaining,
        )

    async def _hangup_many_confirmed_locked(
        self,
        call_ids: Any,
        *,
        reason_code: Optional[int] = None,
    ) -> bool:
        """Remove explicit human legs and prove their absence in one deadline.

        ARI accepting ``DELETE /channels/{id}`` is only a control-plane
        acknowledgement; the channel may still be present and billable.  A 404
        is authoritative for that leg.  For 200/204 responses we poll the ARI
        channel inventory until every parent/transfer target is absent.  Any
        inventory outage or deadline expiry returns ``False`` so callers leave
        the call row, lease, and billing reservation retryable.
        """

        owned_channel_ids = tuple(dict.fromkeys(str(value) for value in call_ids if value))
        if not owned_channel_ids:
            return False

        call_id = owned_channel_ids[0]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._hangup_confirm_timeout_s
        # Start fail-closed: every snapshotted leg needs proof.  A DELETE 404
        # can remove one from this set immediately; all other legs remain until
        # the inventory poll proves them absent.
        needs_absence_proof: set[str] = set(owned_channel_ids)
        needs_absence_proof.difference_update(self._destroyed_channel_ids)
        for channel_id in owned_channel_ids:
            if channel_id not in needs_absence_proof:
                continue
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                response = await self._delete_channel_with_reason_fallback(
                    channel_id,
                    reason_code=reason_code,
                    timeout_s=remaining,
                )
                status = response[0] if isinstance(response, tuple) else None
                if status == 404:
                    needs_absence_proof.discard(channel_id)
            except Exception as exc:
                # A request can time out after Asterisk already removed the
                # channel.  Do not settle yet, but let the inventory proof below
                # establish the safe result if ARI remains reachable.
                logger.warning(
                    "AsteriskAdapter.hangup_request_unconfirmed channel=%s err=%s",
                    channel_id[:12],
                    exc,
                )

        if not needs_absence_proof:
            self._destroyed_channel_ids.difference_update(owned_channel_ids)
            return True

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                active_ids = await asyncio.wait_for(
                    self.list_active_channel_ids(),
                    timeout=remaining,
                )
            except (asyncio.TimeoutError, TimeoutError):
                active_ids = None
            if active_ids is not None:
                needs_absence_proof.intersection_update(active_ids)
                if not needs_absence_proof:
                    self._destroyed_channel_ids.difference_update(owned_channel_ids)
                    return True
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(self._hangup_confirm_poll_s, remaining))

        logger.warning(
            "AsteriskAdapter.hangup_confirmation_timeout call=%s remaining_legs=%s",
            call_id[:12],
            ",".join(sorted(value[:12] for value in needs_absence_proof)),
        )
        return False

    async def hangup_confirmed(
        self,
        call_id: str,
        *,
        reason_code: Optional[int] = None,
    ) -> bool:
        """Remove every currently-owned human leg with hard PBX proof."""

        channel_ids = [call_id]
        transfer = self._transfers_by_parent.get(call_id)
        if transfer is not None:
            channel_ids.append(transfer.target_id)
        target_transfer = self._transfers_by_target.get(call_id)
        if target_transfer is not None:
            channel_ids.extend([target_transfer.parent_id, target_transfer.target_id])
        return await self.hangup_many_confirmed(
            channel_ids,
            reason_code=reason_code,
        )

    async def hangup(self, call_id: str) -> None:
        """Delete (hang up) a channel via ARI, preserving legacy fail-soft API."""
        await self.hangup_confirmed(call_id)

    async def transfer(
        self,
        call_id: str,
        destination: str,
        mode: str = "blind",
        *,
        provider_leg_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run a supervised blind transfer in the parent's mixing bridge.

        ARI redirect is intentionally not used: it only confirms that Asterisk
        accepted a redirect request, not that the destination answered, and it
        removes the caller from Stasis before Talky can settle the transfer leg.
        This method returns ``completed`` only after an ``Up``/``ANSWER`` event
        for the created target channel.
        """
        mode = str(mode or "blind").strip().lower()
        parent = self._active_sessions.get(call_id)
        direction = str((parent or {}).get("direction") or "outbound").lower()
        is_inbound = direction == "inbound" or call_id in self._inbound_admissions

        # Preserve the pre-existing outbound contract byte-for-byte. Existing
        # callers use internal extensions and all three public mode labels;
        # applying the new inbound PSTN linked-leg machinery here would be an
        # unrelated production regression.
        if not is_inbound:
            try:
                await self._ari(
                    "POST",
                    f"/channels/{call_id}/redirect",
                    params={"endpoint": f"PJSIP/{destination}"},
                    ok=(200, 204),
                )
                return {
                    "status": "success",
                    "call_id": call_id,
                    "destination": destination,
                    "mode": mode,
                }
            except Exception as exc:
                logger.error("AsteriskAdapter.transfer outbound: %s", exc)
                return {
                    "status": "failed",
                    "call_id": call_id,
                    "destination": destination,
                    "mode": mode,
                    "error": str(exc),
                }

        if mode != "blind":
            return {
                "status": "failed",
                "call_id": call_id,
                "destination": destination,
                "mode": mode,
                "error": "unsupported_transfer_mode",
                "message": "Asterisk supports supervised blind transfer only",
            }

        if parent is None:
            return {
                "status": "failed",
                "call_id": call_id,
                "destination": destination,
                "mode": mode,
                "error": "call_not_active",
            }
        if call_id in self._termination_fenced_call_ids:
            return {
                "status": "failed",
                "call_id": call_id,
                "destination": destination,
                "mode": mode,
                "error": "call_terminating",
            }
        if not re.fullmatch(r"\+[1-9][0-9]{6,14}", str(destination or "")):
            return {
                "status": "failed",
                "call_id": call_id,
                "destination": destination,
                "mode": mode,
                "error": "invalid_transfer_destination",
            }
        bridge_id = str(parent.get("bridge_id") or self._bridges.get(call_id) or "")
        if not bridge_id:
            return {
                "status": "failed",
                "call_id": call_id,
                "destination": destination,
                "mode": mode,
                "error": "call_has_no_bridge",
            }
        if call_id in self._transfers_by_parent:
            return {
                "status": "failed",
                "call_id": call_id,
                "destination": destination,
                "mode": mode,
                "error": "transfer_already_in_progress",
            }

        if direction == "inbound":
            admission = self._inbound_admissions.get(call_id) or {}
            trunk_id = str(admission.get("trunk_id") or "").strip()
            snapshot = admission.get("config_snapshot") or {}
            route = snapshot.get("route") if isinstance(snapshot, dict) else {}
            trunk_name = str(
                (route or {}).get("sip_trunk_name") if isinstance(route, dict) else ""
            ).strip()
            if not trunk_id or not trunk_name:
                return {
                    "status": "failed",
                    "call_id": call_id,
                    "destination": destination,
                    "mode": mode,
                    "error": "inbound_trunk_identity_unavailable",
                }
            from app.domain.services.telephony.trunk_resolver import (
                env_default_endpoint,
                platform_default_trunk_name,
            )

            if trunk_name.lower() == platform_default_trunk_name().lower():
                trunk_endpoint = env_default_endpoint().strip()
            else:
                trunk_endpoint = f"trunk-{trunk_id}"
            if not trunk_endpoint:
                return {
                    "status": "failed",
                    "call_id": call_id,
                    "destination": destination,
                    "mode": mode,
                    "error": "inbound_trunk_unavailable",
                }
        else:  # Defensive: is_inbound above should make this unreachable.
            return {
                "status": "failed",
                "call_id": call_id,
                "destination": destination,
                "mode": mode,
                "error": "inbound_direction_mismatch",
            }

        target_id = str(provider_leg_id or "").strip().lower()
        target_suffix = target_id.removeprefix("talky-xfer-")
        if (
            not target_id.startswith("talky-xfer-")
            or len(target_suffix) != 20
            or any(char not in "0123456789abcdef" for char in target_suffix)
        ):
            return {
                "status": "failed",
                "call_id": call_id,
                "destination": destination,
                "mode": mode,
                "error": "provider_leg_id_required",
                "message": "A pre-persisted Asterisk transfer leg ID is required",
            }
        loop = asyncio.get_running_loop()
        leg = _AsteriskTransferLeg(
            parent_id=call_id,
            target_id=target_id,
            bridge_id=bridge_id,
            destination=str(destination),
            endpoint=f"PJSIP/{destination}@{trunk_endpoint}",
            mode=mode,
            future=loop.create_future(),
            persisted_target_id=target_id,
            requested_target_id=target_id,
        )
        provider_side_effect_started = False
        try:
            async with self._transfer_setup_lock:
                if call_id in self._termination_fenced_call_ids:
                    raise RuntimeError("call_terminating")
                if call_id in self._transfers_by_parent:
                    raise RuntimeError("transfer_already_in_progress")
                existing_target = self._transfers_by_target.get(target_id)
                if existing_target is not None and existing_target is not leg:
                    raise RuntimeError("provider_leg_id_in_use")
                self._transfers_by_parent[call_id] = leg
                self._transfers_by_target[target_id] = leg
                self._record_transfer_attempt_once(leg)
                self._sync_transfer_inflight_metric()

                # From this point a transport exception may have happened
                # after Asterisk accepted channel creation. Such uncertainty
                # must retain the proof-aware target cleanup path. Rejections
                # above are purely local and have no PBX leg to clean.
                provider_side_effect_started = True
                created = await self._ari(
                    "POST",
                    "/channels/create",
                    params={
                        "endpoint": leg.endpoint,
                        "app": self._app_name,
                        "appArgs": f"transfer_target,{call_id}",
                        "channelId": target_id,
                        "originator": call_id,
                        "formats": "ulaw",
                    },
                    ok=(200, 201),
                )
                actual_target_id = str((created or {}).get("id") or target_id)
                if actual_target_id != leg.target_id:
                    planned_target_id = leg.target_id
                    collision = self._transfer_provider_identity_conflict(
                        leg,
                        actual_target_id,
                    )
                    if collision:
                        # Never delete an ID already owned by another local
                        # call. Cleanup remains scoped to the safe planned ID.
                        raise RuntimeError(f"transfer_provider_identity_collision:{collision}")

                    # Classify racing ARI events by the returned ID, but keep
                    # ``persisted_target_id`` on the planned value until the
                    # exact tenant/call/child transaction commits below.
                    self._transfers_by_target.pop(planned_target_id, None)
                    leg.target_id = actual_target_id
                    self._transfers_by_target[actual_target_id] = leg
                    identity_callback = self._on_transfer_provider_identity_persist
                    if identity_callback is None:
                        raise RuntimeError("transfer_provider_leg_id_mismatch")
                    admission = self._inbound_admissions.get(call_id) or {}
                    try:
                        identity_result = identity_callback(
                            call_id,
                            planned_target_id,
                            actual_target_id,
                            str(admission.get("tenant_id") or ""),
                            str(admission.get("call_id") or ""),
                        )
                        if asyncio.iscoroutine(identity_result):
                            identity_result = await identity_result
                    except BaseException as identity_exc:
                        # A proved DB collision/alias may identify somebody
                        # else's real channel. Roll local ownership back so
                        # cleanup cannot hang it up. Availability/commit
                        # uncertainty keeps the actual ID owned for deletion.
                        identity_code = str(getattr(identity_exc, "code", "") or identity_exc)
                        if "collision" in identity_code or "alias" in identity_code:
                            self._transfers_by_target.pop(actual_target_id, None)
                            leg.target_id = planned_target_id
                            self._transfers_by_target[planned_target_id] = leg
                        if isinstance(identity_exc, asyncio.CancelledError):
                            raise
                        raise RuntimeError(
                            identity_code or "transfer_provider_identity_persistence_unavailable"
                        ) from identity_exc
                    persisted_identity = str(identity_result or "").strip()
                    if persisted_identity != actual_target_id:
                        raise RuntimeError("transfer_provider_identity_persistence_mismatch")
                    leg.persisted_target_id = actual_target_id
                    leg.provider_identity_rebound = True
                    if leg.answer_pending_identity_persist:
                        leg.answer_pending_identity_persist = False
                        self._mark_transfer_answered(leg)

                if call_id in self._termination_fenced_call_ids:
                    raise RuntimeError("call_terminating")
                if leg.pending_failure is not None or leg.future.done():
                    raise RuntimeError("transfer_target_terminated_during_identity_persistence")

                await self._ari(
                    "POST",
                    f"/channels/{leg.target_id}/dial",
                    params={"timeout": int(self._transfer_answer_timeout_s)},
                    ok=(200, 204),
                )
            logger.info(
                "AsteriskAdapter: supervised transfer dialing parent=%s target=%s endpoint=%s",
                call_id[:12],
                leg.target_id[:12],
                trunk_endpoint,
            )
            try:
                return await asyncio.wait_for(
                    asyncio.shield(leg.future),
                    timeout=self._transfer_answer_timeout_s + 2.0,
                )
            except asyncio.TimeoutError:
                self._resolve_transfer_failure(leg, "no_answer")
                try:
                    return dict(
                        await asyncio.wait_for(
                            asyncio.shield(leg.future),
                            timeout=self._hangup_confirm_timeout_s + 0.25,
                        )
                    )
                except asyncio.TimeoutError:
                    pending_result = {
                        "status": "cleanup_pending",
                        "call_id": call_id,
                        "target_call_id": leg.target_id,
                        "provider_leg_id": leg.target_id,
                        "destination": destination,
                        "mode": mode,
                        "error": "target_termination_unconfirmed",
                    }
                    pending_result.update(self._transfer_provider_identity_payload(leg))
                    return pending_result
        except asyncio.CancelledError:
            self._resolve_transfer_failure(leg, "transfer_cancelled")
            raise
        except Exception as exc:
            logger.error(
                "AsteriskAdapter.transfer: parent=%s target=%s err=%s",
                call_id[:12],
                leg.target_id[:12],
                exc,
            )
            if not provider_side_effect_started:
                self._drop_transfer_indexes(leg)
                error = str(exc).strip() or "provider_error"
                if error not in {
                    "call_terminating",
                    "transfer_already_in_progress",
                    "provider_leg_id_in_use",
                }:
                    error = "provider_error"
                return {
                    "status": "failed",
                    "call_id": call_id,
                    "target_call_id": leg.persisted_target_id or leg.target_id,
                    "provider_leg_id": leg.persisted_target_id or leg.target_id,
                    "destination": destination,
                    "mode": mode,
                    "error": error,
                    "message": str(exc),
                }
            error_text = str(exc).strip()
            if error_text == "transfer_provider_leg_id_mismatch":
                public_error = "provider_leg_id_mismatch"
            elif error_text.startswith("transfer_provider_identity_collision"):
                public_error = "provider_leg_id_collision"
            elif error_text.startswith("transfer_provider_identity_"):
                public_error = "provider_leg_identity_persistence_failed"
            else:
                public_error = "provider_error"
            self._resolve_transfer_failure(
                leg,
                public_error,
                detail=error_text,
            )
            try:
                return dict(
                    await asyncio.wait_for(
                        asyncio.shield(leg.future),
                        timeout=self._hangup_confirm_timeout_s + 0.25,
                    )
                )
            except asyncio.TimeoutError:
                pending_result = {
                    "status": "cleanup_pending",
                    "call_id": call_id,
                    "target_call_id": leg.target_id,
                    "provider_leg_id": leg.target_id,
                    "destination": destination,
                    "mode": mode,
                    "error": "target_termination_unconfirmed",
                }
                pending_result.update(self._transfer_provider_identity_payload(leg))
                return pending_result
