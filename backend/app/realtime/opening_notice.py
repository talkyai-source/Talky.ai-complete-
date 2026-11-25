"""The first sentence of a Realtime phone call: checked before it plays, proven after.

On the normal (cascaded) path the first sentence is built in code and its delivery is
proven by the phone gateway's receipt. On the Realtime path the model speaks, but every
reply is held until it is complete together with the model's own transcript, before the
first byte is played. So the first reply can be REQUIRED to be exactly the sentence the
normal path would have said:

* before it plays: its transcript must equal the required text word for word
  (normalised); if not it is withheld and repaired once;
* after it plays: the same receipt rule as ``agent_first._speak_recording_disclosure``
  decides whether the notice counts as delivered.

Compliance rule: when in doubt the notice is recorded as NOT confirmed (ledger
``failed``), never as delivered. Delivered here means "the gateway transmitted that
utterance AND the model's own transcript matched word for word"; logged with
``words=model_transcript``.

All wording comes from existing builders; nothing is invented here. Switch
``REALTIME_OPENING_NOTICE_ENABLED`` (on unless 0/false/no/off), read once per call.
Only ``telephony/lifecycle.py`` wires it, so the Test-agent screen's bridge has no gate.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

ENV_SWITCH = "REALTIME_OPENING_NOTICE_ENABLED"
_OFF_VALUES = {"0", "false", "no", "off"}
_REPAIR_REASON = "This call must open with the required line."
# Letters and digits of EVERY script count as words (an account's own notice may be
# written in Urdu, Arabic, Greek ...): a comparison that dropped them would call a
# notice delivered that was never said.
_WORDS = re.compile(r"[^\W_]+(?:'[^\W_]+)*")
# One reply may carry about 30 seconds of speech (realtime/playout_buffer.py). A required
# sentence longer than this many words could never be played as one reply.
MAX_REQUIRED_WORDS = 60
_AI_DOTTED = re.compile(r"\ba\.i\.?(?!\w)")


def opening_notice_enabled() -> bool:
    # The literal name is what the settings-template generator looks for.
    return os.getenv("REALTIME_OPENING_NOTICE_ENABLED", "").strip().lower() not in _OFF_VALUES


@dataclass
class OpeningGate:
    voice_session: Any
    call_ids: list
    required_text: str
    notice_owed: bool
    notice: str
    outbound: bool
    continue_after: bool
    repairs_left: int = 1
    state: str = "pending"  # pending, playing, delivered, unconfirmed
    reason: str = ""
    call_id: str = ""
    first_speaker: str = "-"
    policy_reason: str = "-"
    _extra: dict = field(default_factory=dict)


def normalize_words(text: Optional[str]) -> list:
    t = unicodedata.normalize("NFKC", text or "").casefold().replace("’", "'").replace("&", " and ")
    t = _AI_DOTTED.sub("ai", t)
    return _WORDS.findall(t)


def words_match(heard: Optional[str], required: Optional[str]) -> bool:
    a, b = normalize_words(heard), normalize_words(required)
    return bool(a) and bool(b) and a == b


def receipt_evidence(receipt: Any, utterance_id: Any) -> Optional[str]:
    """The receipt's evidence string, only under the conditions agent_first uses.

    Same rule as ``agent_first._speak_recording_disclosure``: ids match, and either
    ``completed`` with evidence ``transport_played``/``transmitted``, or ``transmitted``
    with evidence ``transmitted``. Anything else gives None.
    """
    if not (isinstance(receipt, dict) and receipt.get("utterance_id")
            and receipt.get("utterance_id") == utterance_id):
        return None
    status, evidence = receipt.get("status"), receipt.get("evidence")
    if (status == "completed" and evidence in {"transport_played", "transmitted"}) or (
            status == "transmitted" and evidence == "transmitted"):
        return evidence
    return None


def _cid(gate: OpeningGate) -> str:
    return str(gate.call_id)[:12]


def _log_text(text: Optional[str]) -> str:
    from app.domain.services.telephony.config import opener_for_log
    return opener_for_log(text)[:120]


def _set_gate_recording(gate: OpeningGate, enabled: bool) -> None:
    gateway = getattr(gate.voice_session, "media_gateway", None)
    fn = getattr(gateway, "set_recording_enabled", None)
    if callable(fn):
        fn(gate.call_id, enabled)


async def decide_opening(voice_session, *, greet_on_start: bool) -> OpeningGate:
    """Work out the required first sentence and the notice state for this call.

    For an outbound call this repeats the policy steps of
    ``agent_first._speak_recording_disclosure`` in the same order. For an incoming call
    it writes nothing to the ledger: ``prepare_inbound_recording`` already decided and
    an incoming Realtime call stays not recorded.
    """
    from app.domain.services.recording_policy_service import (
        DISCLOSURE_FAILED,
        DISCLOSURE_NOT_REQUIRED,
        RecordingPolicyService,
        callee_country,
        record_callee_country,
        record_disclosure_state,
        spoken_disclosure_text,
    )
    from app.domain.services.telephony.modes.agent_first import (
        _disclosure_call_ids,
        _disclosure_tenant_id,
    )
    from app.domain.services.telephony.recording import _is_true_inbound_session
    from app.realtime.runtime import realtime_opening_line

    config = voice_session.config
    session = voice_session.call_session
    call_id = voice_session.call_id
    call_ids = _disclosure_call_ids(voice_session)
    voice_session._recording_disclosure_attempted = True
    direction = getattr(getattr(config, "direction", None), "value", getattr(config, "direction", None))
    first_speaker = "agent" if greet_on_start else "caller"

    def gate(required, notice, *, owed=False, outbound=True, cont=False, reason="-"):
        return OpeningGate(
            voice_session=voice_session, call_ids=call_ids, required_text=required or "",
            notice_owed=owed, notice=notice, outbound=outbound, continue_after=cont,
            state="pending" if required else "off", call_id=str(call_id),
            first_speaker=first_speaker, policy_reason=reason)
    gate_direction = str(direction or "-")

    if _is_true_inbound_session(voice_session):
        from app.domain.services.voice_pipeline.inbound_answer_greeting import (
            build_inbound_answer_greeting,
            build_inbound_identity_line,
            ensure_ai_disclosure,
        )
        if greet_on_start:
            required = ensure_ai_disclosure(
                getattr(config, "realtime_opening_greeting", None)
                or build_inbound_answer_greeting(session), session)
            g = gate(required, "not_recorded", outbound=False)
        else:
            g = gate(build_inbound_identity_line(session), "not_recorded", outbound=False, cont=True)
        g._extra["direction"] = gate_direction
        return g

    try:
        from app.core.container import get_container

        container = get_container()
        db_pool = (getattr(container, "db_pool", None)
                   if getattr(container, "is_initialized", False) else None)
        if db_pool is None:
            record_disclosure_state(DISCLOSURE_FAILED, *call_ids)
            logger.error("recording_disclosure_unavailable call_id=%s — no DB pool to resolve "
                         "the recording policy; the recording is kept and marked notice not "
                         "confirmed (realtime)", str(call_id)[:12])
            g = gate(realtime_opening_line(config), "policy_unavailable")
            g._extra["direction"] = gate_direction
            return g
        tenant_id = _disclosure_tenant_id(voice_session)
        if not tenant_id:
            record_disclosure_state(DISCLOSURE_FAILED, *call_ids)
            logger.error("recording_disclosure_no_tenant call_id=%s — cannot resolve the "
                         "recording policy; the recording is kept and marked notice not "
                         "confirmed (realtime)", str(call_id)[:12])
            g = gate(realtime_opening_line(config), "policy_unavailable")
            g._extra["direction"] = gate_direction
            return g

        callee = callee_country(voice_session)
        record_callee_country(callee, *call_ids)
        decision = await RecordingPolicyService(db_pool).decide(
            tenant_id=tenant_id, callee_country_code=callee)
        reason = str(getattr(decision, "reason", "-"))
        if not decision.should_record:
            voice_session._recording_refused_by_policy = True
            gateway = getattr(voice_session, "media_gateway", None)
            set_gate = getattr(gateway, "set_recording_enabled", None)
            if callable(set_gate):
                set_gate(call_id, False)
            logger.info("recording_disabled_by_policy call_id=%s reason=%s", str(call_id)[:12], reason)
        pinned = getattr(voice_session, "_recording_disclosure_text_override", None)
        if pinned is not None:
            text = str(pinned).strip()
            if not text:
                raise RuntimeError("pinned recording disclosure is empty")
        else:
            text = spoken_disclosure_text(decision)
        from app.domain.services.notice_placement import decide_notice_placement
        placement = decide_notice_placement(
            decision, text, opener_available=True, pinned=pinned is not None,
            call_id=call_id, path="realtime",
        )
        if not text:
            voice_session._recording_policy_decided = True
            record_disclosure_state(DISCLOSURE_NOT_REQUIRED, *call_ids)
            logger.info("recording_disclosure_not_required call_id=%s reason=%s", str(call_id)[:12], reason)
            g = gate(realtime_opening_line(config), "not_required", reason=reason)
        elif placement.in_opening:
            g = gate(realtime_opening_line(config, recorded_line=True), "platform_in_opener",
                     owed=True, reason=reason)
        elif len(normalize_words(f"{text} {realtime_opening_line(config)}")) > MAX_REQUIRED_WORDS:
            # Too long to be one reply: asking for it would end in silence and a hang-up.
            # The opening is still required; the notice is recorded as NOT confirmed.
            record_disclosure_state(DISCLOSURE_FAILED, *call_ids)
            logger.warning("realtime_opening_notice_text_too_long call_id=%s words=%d limit=%d — the "
                           "recording is kept and marked notice not confirmed", str(call_id)[:12],
                           len(normalize_words(text)), MAX_REQUIRED_WORDS)
            g = gate(realtime_opening_line(config), "tenant_text_too_long", reason=reason)
        else:
            g = gate(f"{text} {realtime_opening_line(config)}", "tenant_text_first",
                     owed=True, reason=reason)
    except Exception as exc:  # noqa: BLE001 — fail closed: marked not confirmed
        record_disclosure_state(DISCLOSURE_FAILED, *call_ids)
        logger.error("recording_disclosure_failed call_id=%s err=%s — the recording is kept "
                     "and marked notice not confirmed (realtime)", str(call_id)[:12], exc, exc_info=True)
        g = gate(realtime_opening_line(config), "policy_error")
    g._extra["direction"] = gate_direction
    return g


def settle_unconfirmed(gate: OpeningGate, reason: str, text: Optional[str] = None) -> None:
    from app.domain.services.recording_policy_service import DISCLOSURE_FAILED, record_disclosure_state
    from app.domain.services.voice_pipeline.inbound_answer_greeting import says_it_is_ai

    if gate.notice_owed:
        record_disclosure_state(DISCLOSURE_FAILED, *gate.call_ids)
    gate.state = "unconfirmed"
    gate.reason = reason
    logger.warning("realtime_opening_notice_unconfirmed call_id=%s reason=%s ai_line=%s",
                   _cid(gate), reason, says_it_is_ai(text if text is not None else gate.required_text)
                   if text is not None else "unknown")


async def admit_opening(bridge, event, utterance) -> bool:
    """True when the reply may go on to play; False when it was withheld (repair sent)."""
    gate = bridge._opening_gate
    if gate is None or gate.state != "pending":
        return True
    if getattr(bridge._action_session, "_caller_opted_out", False):
        settle_unconfirmed(gate, "opt_out", event.text)
        return True
    if words_match(event.text, gate.required_text):
        if (gate.outbound and gate.notice_owed
                and not getattr(gate.voice_session, "_recording_refused_by_policy", False)):
            # The call is ending (voicemail hang-up proven or pending, teardown begun,
            # session no longer served): the reset below would empty the whole call and
            # the notice cannot be heard. Keep the audio and settle the notice as NOT
            # confirmed, so no later receipt can mark it delivered over earlier speech.
            from app.domain.services.telephony.modes.agent_first import _notice_skip_reason
            skip = await _notice_skip_reason(gate.voice_session, gate.call_id)
            if skip:
                gate.voice_session._recording_policy_decided = True
                settle_unconfirmed(gate, f"call_ending_{skip}", event.text)
                logger.info("recording_notice_skipped_call_ending call_id=%s reason=%s path=realtime",
                            _cid(gate), skip)
                return False
        gate.state = "playing"
        utterance["opening_gate"] = True
        if gate.outbound and gate.notice_owed:
            # The kept recording starts at the notice. Never re-open a gate that
            # the account's own policy closed ("disabled").
            if not getattr(gate.voice_session, "_recording_refused_by_policy", False):
                _set_gate_recording(gate, False)
                _set_gate_recording(gate, True)
            gate.voice_session._recording_policy_decided = True
        # The words equal the required sentence here; it is logged the way the normal
        # path logs its own opening (through the opener's log helper), never the model's text.
        logger.info("realtime_opening_notice_speaking call_id=%s utterance=%s text=%r",
                    _cid(gate), utterance["id"], _log_text(gate.required_text))
        return True
    if gate.repairs_left > 0:
        gate.repairs_left -= 1
        # The model's own words are never written to the log (they may carry personal
        # details): only the counts and where the two sentences first differ.
        want, got = normalize_words(gate.required_text), normalize_words(event.text)
        differs_at = next((i for i, (a, b) in enumerate(zip(want, got)) if a != b), min(len(want), len(got)))
        logger.warning("realtime_opening_notice_withheld call_id=%s attempt=%d reason=text_mismatch "
                       "required_words=%d heard_words=%d first_difference_at_word=%d",
                       _cid(gate), 1 - gate.repairs_left, len(want), len(got), differs_at + 1)
        await bridge._rt.repair_unspoken_response(
            (event.raw or {}).get("response") or {},
            required_text=gate.required_text, required_reason=_REPAIR_REASON)
        return False
    settle_unconfirmed(gate, "text_mismatch", event.text)
    return True


async def settle_opening(bridge, utterance, text) -> None:
    """After playback: count the opening as delivered only on the normal path's receipt rule."""
    gate = bridge._opening_gate
    if gate is None or gate.state != "playing":
        return
    from app.domain.services.recording_policy_service import (
        DISCLOSURE_SPOKEN,
        DISCLOSURE_TRANSMITTED,
        record_disclosure_state,
    )
    from app.domain.services.voice_pipeline.inbound_answer_greeting import says_it_is_ai
    try:
        receipt = utterance.get("receipt")
        evidence = receipt_evidence(receipt, utterance.get("id"))
        status = (receipt or {}).get("status") if isinstance(receipt, dict) else None
        if (evidence is None or utterance.get("status") == "interrupted"
                or not utterance.get("submission_started")):
            settle_unconfirmed(gate, f"receipt_{status or 'none'}", text)
            return
        if gate.notice_owed:
            record_disclosure_state(
                DISCLOSURE_SPOKEN if evidence == "transport_played" else DISCLOSURE_TRANSMITTED,
                *gate.call_ids)
        gate.state = "delivered"
        logger.info("realtime_opening_notice_delivered call_id=%s utterance=%s evidence=%s "
                    "words=model_transcript ai_line=%s", _cid(gate), utterance.get("id"),
                    evidence, says_it_is_ai(text))
        if gate.continue_after:
            await bridge._rt.request_response()
    except Exception:  # noqa: BLE001 — never mark delivered on an error
        logger.exception("realtime_opening_notice_settle_failed call_id=%s", _cid(gate))
        if gate.state == "playing":
            settle_unconfirmed(gate, "settle_error", text)


def settle_interrupted(bridge, utterance) -> None:
    """From the playback ``finally``: audio already submitted is unconfirmed, never restarted;
    nothing submitted means the next reply must still be the opening."""
    gate = bridge._opening_gate
    if gate is None or gate.state != "playing":
        return
    if utterance.get("submission_started"):
        settle_unconfirmed(gate, "interrupted", utterance.get("text"))
    else:
        gate.state = "pending"


async def run_realtime_bridge(voice_session) -> None:
    bridge = voice_session.realtime_bridge
    config = getattr(voice_session, "config", None)
    direction = getattr(getattr(config, "direction", None), "value", "-")
    cid = str(voice_session.call_id)[:12]
    if not opening_notice_enabled():
        logger.info("realtime_opening_notice_wiring call_id=%s enabled=false direction=%s "
                    "first_speaker=%s notice=- policy_reason=- required_words=0",
                    cid, direction, "agent" if bridge._greet_on_start else "caller")
        await bridge.run()
        return
    try:
        gate = await decide_opening(voice_session, greet_on_start=bool(bridge._greet_on_start))
    except Exception:  # noqa: BLE001 — the call must never be left without an agent
        # Nothing is marked delivered: with no gate the bridge runs as it did before
        # this module existed, and an outbound recording stays "notice not confirmed".
        logger.exception("realtime_opening_notice_decide_failed call_id=%s — running without the gate", cid)
        await bridge.run()
        return
    bridge.attach_opening_gate(gate)
    logger.info("realtime_opening_notice_wiring call_id=%s enabled=true direction=%s "
                "first_speaker=%s notice=%s policy_reason=%s required_words=%d",
                cid, gate._extra.get("direction", direction), gate.first_speaker, gate.notice,
                gate.policy_reason, len(normalize_words(gate.required_text)))
    try:
        await bridge.run()
    finally:
        if gate.state in {"pending", "playing"}:
            settle_unconfirmed(gate, "call_ended_before_opening")
