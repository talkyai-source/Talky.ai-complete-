"""Agent-first (assistant-speaks-first) call-flow handler.

Used when the campaign owner selects ``first_speaker = "agent"`` (the default,
and what most campaigns pick). On answer:

* **Fast path** — pre-synthesized greeting chunks (built during the ringing
  phase by lifecycle's ``_on_ringing``) are pumped directly into the media
  gateway. First audio reaches the callee within ~5ms.
* **Slow path** — if pre-synth is unavailable, falls back to realtime TTS via
  ``voice_session.pipeline.synthesize_and_send_audio``.

Barge-in: if the callee speaks during playback, only the spoken portion of the
greeting is persisted to ``conversation_history`` so the LLM does not echo the
unspoken tail on the next turn.

Recording disclosure (2026-07-28): when the tenant's recording policy requires
an announcement, the notice is spoken as the FIRST audio on the call — before
the greeting, and therefore before the callee has said anything that could be
retained. What actually happened is reported to the disclosure ledger in
``recording_policy_service``; ``RecordingService.save_and_link`` refuses to
retain audio for a call whose required notice was not delivered.

2026-08-11: the 2026-07-31 fix that RETRIED the notice from the top after a
barge-in was itself reverted. Two traced production calls showed
``recording_disclosure_interrupted_retrying attempt=1/2`` followed by the
caller hanging up ~2s later — a callee who has already heard "This call may
be recorded..." start once and then hears it start over reads it as a
malfunctioning robot, not a re-delivered notice, and hangs up. Restarting
from the beginning is strictly worse than the "abandon after one interrupt"
behaviour it replaced: it not only fails to get the notice heard, it also
loses the caller. See ``_speak_recording_disclosure`` for the single-attempt,
fail-closed replacement.
"""
from __future__ import annotations

import asyncio
import logging

from app.domain.services.telephony.config import (
    _build_outbound_greeting,
    _build_call_greeting,
)

logger = logging.getLogger(__name__)

# Recording-disclosure delivery attempts (2026-08-11: fixed at 1 — see the
# module docstring for the production evidence). A prior version of this
# code retried up to twice, restarting the notice FROM THE TOP each time.
# That sounds correct on paper ("say it again once they stop talking") but
# is not what a human hears: they already heard the notice begin, so a
# restart plays like a broken recording looping, not a second attempt to
# inform them. Both traced production interruptions were followed by the
# caller hanging up within ~2 seconds of the restart.
#
# There is no cheap way to RESUME instead of restart —
# ``synthesize_and_send_audio`` reports only a bool ("was this call
# interrupted"), not a text/audio offset to resume from, and synthesizing
# a resume clause on the fly would require knowing exactly which words the
# TTS engine had already emitted at the moment barge-in fired, which the
# pipeline does not track. Of the two lawful options — stop retrying, or
# resume without restarting — "stop retrying" is the one buildable today
# without invasive changes to the TTS playback path, and it is also the
# one directly supported by what production showed: talking over the
# caller a second time is worse than losing the recording once. The gate
# stays fail-closed either way: an interrupted notice is never counted as
# delivered, so the recording is suppressed exactly as it would be if we
# had never tried a second time.
_DISCLOSURE_MAX_ATTEMPTS = 1

# 2026-07-08: opener-content guard. Prod surfaced 2 calls where the spoken
# opener was a bare "Hello?" — the pre-synth greeting text was empty/
# whitespace or degraded to a filler word, so the callee heard what sounded
# like a wrong-number call instead of a real introduction. At the time EVERY
# outbound opener was supposed to be a full identity+reason monologue, so a
# lone filler word was unambiguously a rendering bug.
#
# 2026-08-11 (opener redesign): that is no longer true. The DEFAULT outbound
# opener is now a DELIBERATE short pickup greeting — "Hi there.", "Hello?" —
# see telephony_session_config.build_telephony_greeting. That is exactly the
# text the old guard existed to reject, so left unfixed it would silently
# discard every correctly-built greeting and fall through to the "safe
# minimal branded opener" below on every single call, undoing the redesign
# entirely.
#
# The underlying bug this guard defends against has NOT changed — an empty
# or whitespace pre-synth text still means the TTS pipeline had nothing to
# say — but what counts as "genuine content" has: a short greeting is now
# correct, not degraded, so no single word (or short phrase) is
# disqualifying on its own. What is still disqualifying is having NOTHING
# speakable at all: empty, whitespace-only, or leftover punctuation from a
# template substitution that failed (e.g. "...", "?" with no words either
# side of it).
def _has_real_opener_content(text) -> bool:
    """True when `text` has at least one speakable word — not empty,
    whitespace, or bare punctuation left over from a broken template."""
    if not text or not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped:
        return False
    return any(ch.isalpha() for ch in stripped)


# Word-count threshold distinguishing a BARE pickup greeting ("Hi there.",
# "Hello?") from text that already carries an introduction (identity +
# reason, up to the 12-word budget the LLM-authored opener and the persona's
# relocated STAGE 1 / OPENING turn both use — see llm_opener.py and
# telephony_session_config.py's OPENER REDESIGN note). A word-count
# heuristic, not a fixed allowlist, so a new pickup variant added to
# build_telephony_greeting is recognised as bare without a code change here.
_BARE_GREETING_MAX_WORDS = 5


def _looks_like_bare_pickup_greeting(text) -> bool:
    """True when the text actually spoken as the opener was just a pickup
    greeting with no introduction in it.

    Used to decide whether ``session._has_introduced`` may be set True after
    the greeting plays (see _send_outbound_greeting below): a bare greeting
    has not introduced the agent, so the flag must stay False and the
    per-turn LIVE STATE block (prompts/live_state.py) will correctly tell the
    model, on the turn right after the callee replies, that it still needs to
    give its opening. Text that is NOT bare — the LLM-authored opener
    (flag-gated, already validated to contain identity), a legacy longer
    template, or the "safe minimal branded opener" fallback further below —
    already contains the introduction, so the flag may be set True as before.
    """
    words = [w for w in (text or "").split() if any(c.isalnum() for c in w)]
    return 0 < len(words) <= _BARE_GREETING_MAX_WORDS


def _disclosure_call_ids(voice_session) -> list:
    """Every id this call is known by, for the disclosure ledger.

    ``voice_session.call_id`` is the voice-session UUID; ``_dialer_call_id``
    is ``calls.id``, stamped by ``bind_telephony_call`` at answer time (which
    runs before the greeting task is spawned). The recording teardown path
    saves under whichever of the two it managed to resolve, so both are
    registered.

    ``call_session.provider_call_id`` is deliberately NOT used: on the
    telephony path it is the literal ``"{session_type}-session"``, identical
    across concurrent calls, so keying on it would let one call's disclosure
    satisfy another call's consent check.
    """
    session = getattr(voice_session, "call_session", None)
    candidates = [
        getattr(voice_session, "call_id", None),
        getattr(voice_session, "_dialer_call_id", None),
        getattr(session, "call_id", None) if session is not None else None,
    ]
    seen, ids = set(), []
    for value in candidates:
        if not value:
            continue
        text = str(value)
        if text not in seen:
            seen.add(text)
            ids.append(text)
    return ids


def _disclosure_tenant_id(voice_session):
    """Authoritative tenant for the recording policy lookup.

    Same priority order as the recording teardown path's
    ``_session_tenant_uuid`` (telephony/recording.py), so the policy that
    gates the upload is the policy we announced under.
    """
    config = getattr(voice_session, "config", None)
    session = getattr(voice_session, "call_session", None)
    for value in (
        getattr(voice_session, "_dialer_tenant_id", None),
        getattr(config, "tenant_id", None) if config is not None else None,
        getattr(session, "tenant_id", None) if session is not None else None,
    ):
        if value:
            return str(value)
    return None


async def _speak_recording_disclosure(voice_session) -> None:
    """Speak the recording notice, if the tenant's policy requires one.

    Must run BEFORE any greeting audio: a disclosure delivered after the
    callee has already spoken is worthless — their words were captured
    before they were told. Being the first audio on the call also means
    the notice itself sits at the head of the stored WAV, so the
    recording carries its own proof of consent.

    Always records an outcome in the disclosure ledger, and NEVER raises:
    a failure here must not drop the call, it must drop the RECORDING.
    The call itself is perfectly lawful without a recording; the
    recording is not lawful without the notice. Every failure path
    therefore lands on DISCLOSURE_FAILED, which
    ``RecordingService.save_and_link`` treats as "do not retain".
    """
    from app.domain.models.conversation import Message, MessageRole
    from app.domain.services.recording_policy_service import (
        DISCLOSURE_FAILED,
        DISCLOSURE_NOT_REQUIRED,
        DISCLOSURE_SPOKEN,
        RecordingPolicyService,
        record_disclosure_state,
        spoken_disclosure_text,
    )

    call_ids = _disclosure_call_ids(voice_session)
    call_id = voice_session.call_id
    session = voice_session.call_session

    try:
        from app.core.container import get_container

        container = get_container()
        db_pool = (
            getattr(container, "db_pool", None)
            if getattr(container, "is_initialized", False)
            else None
        )
        if db_pool is None:
            # No pool = no way to know what this tenant's policy is. We
            # cannot prove an announcement was unnecessary, so we must
            # assume it was required and was not given.
            record_disclosure_state(DISCLOSURE_FAILED, *call_ids)
            logger.error(
                "recording_disclosure_unavailable call_id=%s — no DB pool to "
                "resolve the recording policy; recording will be suppressed",
                call_id[:12],
            )
            return

        tenant_id = _disclosure_tenant_id(voice_session)
        if not tenant_id:
            record_disclosure_state(DISCLOSURE_FAILED, *call_ids)
            logger.error(
                "recording_disclosure_no_tenant call_id=%s — cannot resolve "
                "the recording policy; recording will be suppressed",
                call_id[:12],
            )
            return

        # destination_country_code is deliberately left None. It is the
        # ONLY input that can turn the announcement off for a two-party
        # tenant, and we cannot derive it safely from the dialled number:
        # the policy's two-party list is written in US STATE terms
        # ("US-CA"), which no E.164 prefix resolves to. Guessing "US"
        # against a "US-CA" list returns "no announcement needed" and
        # would record a Californian in silence. Passing None means
        # "unknown" -> announce, and it matches what save_and_link passes
        # when it re-runs the same decision, so the two ends never
        # disagree about whether a notice was owed.
        decision = await RecordingPolicyService(db_pool).decide(
            tenant_id=tenant_id,
        )
        text = spoken_disclosure_text(decision)
        if not text:
            record_disclosure_state(DISCLOSURE_NOT_REQUIRED, *call_ids)
            logger.info(
                "recording_disclosure_not_required call_id=%s reason=%s",
                call_id[:12], getattr(decision, "reason", "-"),
            )
            return

        logger.info(
            "recording_disclosure_speaking call_id=%s reason=%s text=%r",
            call_id[:12], getattr(decision, "reason", "-"), text[:80],
        )
        # SINGLE ATTEMPT, NO RESTART (2026-08-11 — see module docstring for
        # the production traces that drove this). A callee who speaks during
        # the notice has already heard it begin; playing it again FROM THE
        # TOP talks over them a second time and reads as a malfunctioning
        # robot rather than a polite re-delivery. Both traced production
        # calls hung up ~2s after exactly that restart. So: one attempt,
        # and if it is interrupted we stop — we do not get a second chance
        # at this call, but we also do not make things worse.
        #
        # Still FAILS CLOSED: an interrupted notice is never counted as
        # delivered, so `record_disclosure_state` below lands on
        # DISCLOSURE_FAILED and `save_and_link` discards the audio exactly
        # as it would if we had never attempted a second time. This change
        # can only ever REMOVE a retry that talked over the caller; it
        # cannot cause a recording to be kept that previously would not
        # have been.
        interrupted = await voice_session.pipeline.synthesize_and_send_audio(
            session, text, websocket=None
        )
        if interrupted:
            record_disclosure_state(DISCLOSURE_FAILED, *call_ids)
            logger.warning(
                "recording_disclosure_interrupted call_id=%s — notice cut "
                "short; not retried (a restart would talk over the caller "
                "a second time); recording will be suppressed",
                call_id[:12],
            )
            return

        record_disclosure_state(DISCLOSURE_SPOKEN, *call_ids)
        # Put it in history so the LLM knows the notice was already given
        # and does not repeat it (or contradict it) on the first turn.
        try:
            session.conversation_history.append(
                Message(role=MessageRole.ASSISTANT, content=text)
            )
        except Exception:
            pass
        logger.info("recording_disclosure_spoken call_id=%s", call_id[:12])
    except Exception as exc:
        record_disclosure_state(DISCLOSURE_FAILED, *call_ids)
        logger.error(
            "recording_disclosure_failed call_id=%s err=%s — recording will "
            "be suppressed for this call",
            call_id[:12], exc, exc_info=True,
        )


async def _send_outbound_greeting(voice_session) -> None:
    """
    Speak the AI's opening line immediately after an outbound call is answered.

    Fast path (pre-synthesized): The greeting audio was already synthesized
    during the ringing phase by _on_ringing.  The buffered PCM chunks are
    pumped directly into the media gateway — first audio reaches the callee
    within ~5ms of this function starting (same instant-start pattern as
    Ask AI's pre-fetched greeting).

    Slow path (fallback): If ringing-phase pre-synthesis failed or was skipped,
    falls back to real-time TTS via synthesize_and_send_audio.
    """
    from app.domain.models.conversation import Message, MessageRole
    import time as _time

    call_id = voice_session.call_id
    session = voice_session.call_session

    # Mark LLM as active so handle_turn_end in the pipeline skips any early
    # caller speech ("Hello?") that arrives before the greeting plays.
    if session.llm_active:
        logger.debug(f"Greeting skipped — LLM already active for {call_id[:12]}")
        return
    session.llm_active = True

    # F-10 extended to the agent-first greeting path. The outbound greeting we
    # are about to play echoes back off the callee's pickup ("Hello?") as a
    # StartOfTurn a beat after audio starts — prod 2026-07-20 showed this
    # truncating the greeting ~1s in (elapsed_ms≈1107/906, interrupted=True).
    # Arm the SAME content-aware echo-immunity window the instant-opener path
    # uses (session._instant_opener_in_flight during playback + a bounded
    # trailing grace after). Both barge-in arming sites already consult
    # is_opener_echo as their FIRST check (audio_ingest._on_barge_in_direct and
    # voice_pipeline_service.handle_barge_in), so setting these flags makes the
    # echo be recognized by CONTENT (is_bare_greeting) and ignored, with ZERO
    # change to those functions. A real interrupt ("stop"/"wait"/any non-bare
    # content) is not a bare greeting, so is_opener_echo returns False, the gate
    # falls through, and the barge-in still fires immediately — no time-only
    # suppression. NB: we deliberately do NOT touch _instant_opener_done here —
    # that once-per-call marker belongs to the caller-first try_instant_opener
    # path and must stay independent so reuse of the shared echo-window flags
    # cannot misfire its guard.
    from app.domain.services.voice_pipeline.instant_opener import (
        _INSTANT_OPENER_ECHO_GRACE_S,
    )
    # Arm the same echo window the caller-first path arms, INCLUDING the onset
    # bound and the greeting text (see instant_opener.is_opener_echo). This
    # path matters most for the bound: the window here spans the recording
    # disclosure AND the opener — several seconds — during which the old
    # greeting-word-only gate swallowed every "Hello?" the callee offered.
    session._instant_opener_started_at = _time.monotonic()
    session._instant_opener_spoken_text = getattr(
        voice_session, "_presynth_greeting_text", None
    )
    session._instant_opener_echo_at = None
    session._instant_opener_in_flight = True

    try:
        # Clear any barge_in_event that fired when the callee answered ("Hello?")
        # so the greeting is not immediately suppressed before a single chunk plays.
        voice_session.pipeline.clear_barge_in_event(session)

        # ── Recording disclosure — FIRST audio on the call ──────────────
        # Ordering is the whole point: this runs before the pre-synth fast
        # path and before the slow-path TTS, so the notice reaches the
        # callee before the opener invites them to say anything. It sits
        # inside the echo-immunity window armed above, so the "Hello?"
        # that answering produces cannot cut it short. A failure here only
        # suppresses the recording (see _speak_recording_disclosure) — the
        # greeting below still plays and the call continues normally.
        await _speak_recording_disclosure(voice_session)

        # ── Fast path: pre-synthesized greeting from ringing phase ──────
        presynth_chunks = getattr(voice_session, "_presynth_greeting_audio", None)
        presynth_text = getattr(voice_session, "_presynth_greeting_text", None)

        # Defaults visible to the history-append block below regardless of
        # which playback path runs.
        was_interrupted = False
        chunks_sent = 0

        # 2026-07-08: opener-content guard — never speak a bare "Hello?" (or
        # empty text) as the whole opener. If the pre-synth text is degraded,
        # discard it (and its matching audio chunks, since they were
        # synthesized FROM that bad text) and fall through to the slow path,
        # which rebuilds real greeting content via _build_call_greeting.
        # Wrapped defensively so any failure here just falls through to the
        # existing pre-synth behaviour rather than breaking the greeting.
        try:
            if presynth_chunks and presynth_text and not _has_real_opener_content(
                presynth_text
            ):
                logger.warning(
                    "outbound_greeting_presynth_rejected call_id=%s text=%r "
                    "— falling back to real-time greeting build",
                    call_id[:12], presynth_text,
                )
                presynth_chunks = None
                presynth_text = None
        except Exception as _guard_exc:
            logger.debug(
                "opener-content guard failed for %s: %s", call_id[:12], _guard_exc
            )

        if presynth_chunks and presynth_text:
            _t0 = _time.monotonic()
            greeting = presynth_text
            logger.info(
                "outbound_greeting_presynth call_id=%s chunks=%d text=%r",
                call_id[:12], len(presynth_chunks), greeting[:60],
            )

            session.tts_active = True
            barge_in_event = getattr(session, "barge_in_event", None)

            for chunk in presynth_chunks:
                # Check barge-in before each chunk
                if barge_in_event and barge_in_event.is_set():
                    was_interrupted = True
                    barge_in_event.clear()
                    try:
                        await voice_session.media_gateway.clear_output_buffer(call_id)
                    except Exception:
                        pass
                    logger.info("presynth_greeting_barge_in call_id=%s", call_id[:12])
                    break

                await voice_session.media_gateway.send_audio(call_id, chunk)
                chunks_sent += 1

                # Check barge-in after send
                if barge_in_event and barge_in_event.is_set():
                    was_interrupted = True
                    barge_in_event.clear()
                    try:
                        await voice_session.media_gateway.clear_output_buffer(call_id)
                    except Exception:
                        pass
                    logger.info("presynth_greeting_barge_in_post_send call_id=%s", call_id[:12])
                    break

            # Flush remaining audio in the gateway buffer
            if not was_interrupted:
                flush = getattr(voice_session.media_gateway, "flush_tts_buffer", None)
                if not flush:
                    flush = getattr(voice_session.media_gateway, "flush_audio_buffer", None)
                if flush:
                    try:
                        await flush(call_id)
                    except Exception:
                        pass

            session.tts_active = False
            _elapsed_ms = (_time.monotonic() - _t0) * 1000.0
            logger.info(
                "outbound_greeting_presynth_done call_id=%s elapsed_ms=%.0f interrupted=%s",
                call_id[:12], _elapsed_ms, was_interrupted,
            )

            # Free memory
            voice_session._presynth_greeting_audio = None
            voice_session._presynth_greeting_text = None

        else:
            # ── Slow path: real-time TTS (ringing pre-synth unavailable) ─
            await asyncio.sleep(0.05)
            # Pick the greeting that matches the call direction so a
            # user-first call falling through to slow-path doesn't
            # accidentally read the outbound consent-first opener.
            # Falls back to "agent" framing when first_speaker is unset
            # (legacy code paths or non-telephony sessions).
            first_speaker = (
                getattr(voice_session, "_first_speaker", None) or "agent"
            )
            greeting = _build_call_greeting(session, first_speaker=first_speaker)

            # 2026-07-08: last-resort opener-content guard. If even the
            # real-time greeting builder produced empty/filler-only text
            # (e.g. a persona template misconfiguration), never let the
            # spoken opener be a bare "Hello?" — build a minimal branded
            # line from whatever agent/company name is already on the
            # session. Wrapped so any failure here falls through to the
            # original (possibly degraded) greeting rather than crashing.
            try:
                if not _has_real_opener_content(greeting):
                    from app.domain.services.telephony.config import (
                        _resolve_greeting_context,
                    )
                    agent_name, company = _resolve_greeting_context(session)
                    greeting = (
                        f"Hello, this is {agent_name} calling from {company}."
                    )
                    logger.warning(
                        "outbound_greeting_realtime_rejected call_id=%s "
                        "— using safe minimal branded opener",
                        call_id[:12],
                    )
            except Exception as _guard_exc:
                logger.debug(
                    "final opener-content guard failed for %s: %s",
                    call_id[:12], _guard_exc,
                )

            logger.info(
                "outbound_greeting_realtime call_id=%s first_speaker=%s text=%r",
                call_id[:12], first_speaker, greeting[:60],
            )
            await voice_session.pipeline.synthesize_and_send_audio(
                session, greeting, websocket=None
            )

        # Persist the greeting so the LLM sees it as conversation history on the
        # first real turn.  Without this the next turn sees an empty history and
        # the LLM re-reads "OPENING THE CALL" instructions, generating a duplicate.
        #
        # If the callee barged in mid-greeting, only persist the portion that
        # actually played. Recording the full text would make the LLM think the
        # callee already heard the whole greeting when in reality they cut us
        # off partway through — and the LLM would then respond as if the rest
        # was heard too, producing a confused follow-up that sounds like a
        # second greeting. (Mostly moot for the bare "Hi there." pickup
        # greeting itself, which has nothing left to cut off mid-thought, but
        # this path is shared with the LLM-authored and legacy longer openers,
        # which do.)
        try:
            _spoken_text = greeting
            if was_interrupted and presynth_chunks and chunks_sent > 0:
                _frac = chunks_sent / len(presynth_chunks)
                _words = greeting.split()
                _keep = max(1, int(len(_words) * _frac))
                _spoken_text = " ".join(_words[:_keep])
                if _keep < len(_words):
                    _spoken_text = _spoken_text.rstrip(".,!?") + "…"
        except Exception:
            _spoken_text = greeting
        session.conversation_history.append(
            Message(role=MessageRole.ASSISTANT, content=_spoken_text)
        )
        # Flip the same flag turn_runner sets after the first LLM-generated
        # reply (see turn_runner.py) so live_state.py's per-turn LIVE STATE
        # block reflects reality — but ONLY when what was actually spoken
        # already contains the introduction.
        #
        # 2026-08-11 (opener redesign): this used to be an unconditional
        # `True` because every greeting WAS the introduction. Now the default
        # greeting is a bare pickup ("Hi there.") with no identity in it — see
        # telephony_session_config.build_telephony_greeting — so setting this
        # True unconditionally would tell LIVE STATE the agent already
        # introduced itself when the callee has heard nothing but "Hi there.",
        # and the model would never give its name or reason for calling on any
        # later turn either (has_introduced=True forever silences that
        # instruction). `_looks_like_bare_pickup_greeting` reads what was
        # ACTUALLY spoken (`_spoken_text`, already trimmed for a barge-in
        # above) rather than the greeting source, so a partial "Hi" cut off
        # mid-word is still correctly treated as bare. Non-bare text — the
        # LLM-authored opener (flag-gated, pre-validated to contain identity),
        # a legacy longer template, or the safe minimal branded fallback in
        # the slow path above — still flips the flag True exactly as before.
        session._has_introduced = not _looks_like_bare_pickup_greeting(_spoken_text)
    except Exception as exc:
        logger.warning(f"Outbound greeting failed for {call_id[:12]}: {exc}")
    finally:
        session.llm_active = False
        # Close the echo-immunity window: clear the in-flight flag and open the
        # bounded trailing grace so a StartOfTurn that lands just after playback
        # returns is still recognized as the same greeting echo (mirrors
        # instant_opener.try_instant_opener's finally exactly).
        session._instant_opener_in_flight = False
        session._instant_opener_grace_until = (
            _time.monotonic() + _INSTANT_OPENER_ECHO_GRACE_S
        )


async def prepare_pre_originate_greeting(
    pre_warm_session,
    effective_first_speaker: str,
) -> None:
    """Pre-synthesize the greeting during the ringing phase.

    Runs only when the agent will actually play audio on answer:
    * ``first_speaker == "agent"`` always pre-synths.
    * ``first_speaker == "user"`` pre-synths when EITHER the silence
      safety net is enabled (default after T1.3) OR instant-pickup mode
      is enabled (opt-in via ``TELEPHONY_USER_FIRST_GREET_ON_PICKUP``).

    On success, attaches ``_presynth_greeting_audio`` (list of PCM chunks)
    and ``_presynth_greeting_text`` to ``pre_warm_session`` so the
    on-answer handler in ``lifecycle._on_new_call`` can pump audio into
    the gateway with no TTS round-trip.

    Raises ``RuntimeError`` when pre-synth runs but produces zero audio
    chunks, so the caller can refuse to ring a cold pipeline.
    """
    # Both first-speaker modes always play a greeting:
    # * agent-first speaks immediately on pickup
    # * caller-first waits 2s then speaks the SAME greeting
    # So pre-synth is unconditional — the gate that used to skip it for
    # "silent caller-first" mode was removed when the silence handler
    # was retired.
    #
    # The greeting text is direction-aware: caller-first uses the inbound
    # receiver phrasing ("Hello, this is {agent} from {company} — how can
    # I help?") so the AI sounds like someone picking up the phone after
    # the callee said hello, rather than reading a cold-call opener.
    greeting_text = _build_call_greeting(
        pre_warm_session.call_session,
        first_speaker=effective_first_speaker,
    )
    chunks: list[bytes] = []
    # Carry the orphan byte across chunks. Int16 (s16le) providers like
    # ElevenLabs HTTP-stream PCM that can split a 2-byte sample across two
    # chunks (odd-length chunk). DROPPING that trailing byte (raw[:-1])
    # byte-shifts every following sample → white-noise buzz on the whole
    # greeting. Instead, hold the orphan and prepend it to the next chunk so
    # sample alignment is preserved. This is the same fix applied to the
    # conversation path in voice_pipeline/tts_playback.py — the pre-synth
    # greeting is a SEPARATE path that pumps chunks straight to the media
    # gateway, so it needed the fix independently.
    pending = b""
    tts_config = pre_warm_session.config
    async for audio_chunk in pre_warm_session.tts_provider.stream_synthesize(
        text=greeting_text,
        voice_id=tts_config.voice_id if tts_config else "default",
        sample_rate=(
            tts_config.tts_sample_rate if tts_config else 8000
        ),
        call_id=pre_warm_session.call_id,
    ):
        raw = (
            audio_chunk.data
            if hasattr(audio_chunk, "data")
            else audio_chunk
        )
        if not raw:
            continue
        if not isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw)
        raw = pending + bytes(raw)
        pending = b""
        if len(raw) % 2 != 0:
            pending = raw[-1:]
            raw = raw[:-1]
        if raw:
            chunks.append(raw)
    # A single trailing orphan byte at stream end has no partner sample —
    # it's a genuinely incomplete final sample (not a misalignment source),
    # so it is dropped rather than carried.

    if not chunks:
        raise RuntimeError(
            "pre_originate_greeting_empty: TTS returned 0 audio chunks"
        )

    pre_warm_session._presynth_greeting_audio = chunks
    pre_warm_session._presynth_greeting_text = greeting_text
    logger.info(
        "pre_originate_greeting_ready first_speaker=%s chunks=%d bytes=%d text=%r",
        effective_first_speaker,
        len(chunks), sum(len(c) for c in chunks), greeting_text[:60],
    )


async def warm_tts_inference_path(pre_warm_session) -> None:
    """Force the TTS voice model to load on the provider's inference worker.

    ``connect_for_call`` opens the WebSocket but most cloud TTS providers
    only load the voice model when they receive the first synthesize
    request. Without this, turn 0 pays full model-load latency
    (~2s on Google/ElevenLabs) — which is what user-first mode saw before
    this warmup existed (no greeting synth = no model load during ringing =
    cold first turn).

    We synthesize a real phrase, drain the chunks, throw the audio away.
    A single space proved too short on Google TTS — the provider appears
    to fast-path trivial inputs without fully priming the streaming voice
    pipeline. A real sentence ensures the model-load + streaming buffer
    pool are exercised end-to-end before turn 0 fires.
    """
    tts_config = pre_warm_session.config
    async for _chunk in pre_warm_session.tts_provider.stream_synthesize(
        text="Warming up the voice pipeline now.",
        voice_id=tts_config.voice_id if tts_config else "default",
        sample_rate=tts_config.tts_sample_rate if tts_config else 8000,
        call_id=pre_warm_session.call_id,
    ):
        pass  # discard
    logger.info(
        "tts_inference_warmed call_id=%s",
        pre_warm_session.call_id[:12],
    )


async def warm_llm_stream(pre_warm_session) -> None:
    """Drain a tiny LLM streaming generation to prime the inference path.

    ``llm_provider.warm_up()`` opens the HTTP/WS connection but does not
    run a real streaming generation. Most providers' first streaming
    request after warm_up still pays a one-time setup cost (KV-cache
    scheduling, inference-worker assignment). In agent-first mode the
    pre-synthesized greeting indirectly exercises this on the LLM side
    via the system-prompt cache lookup; user-first mode has no such
    cover and turn 0 pays the cold cost in full.

    Fire a minimal "hi" through ``stream_chat`` with ``max_tokens=2`` and
    drain whatever comes back. Discard the result. Both modes run this
    so pickup always lands on a fully-warmed LLM streaming path.
    """
    from app.domain.models.conversation import Message, MessageRole

    msgs = [Message(role=MessageRole.USER, content="hi")]
    try:
        async for _token in pre_warm_session.llm_provider.stream_chat(
            msgs,
            system_prompt="Reply with a single word only.",
            max_tokens=2,
        ):
            pass  # discard
        logger.info(
            "llm_stream_warmed call_id=%s",
            pre_warm_session.call_id[:12],
        )
    except Exception as exc:
        # Re-raise so the strict warmup gate refuses to ring rather than
        # letting the call originate with a half-warm LLM streaming path.
        raise RuntimeError(
            f"llm_stream_warmup_failed: {exc!r}"
        ) from exc
