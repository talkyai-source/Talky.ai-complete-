"""Call a campaign's live agent with a synthetic caller, in real time, and report.

Plays the browser's side of the Test Agent exactly: microphone frames go into
the real browser media gateway every 20 ms, the agent's audio is "played" at
real speed with the same playback_complete receipts a browser sends, and a
barge-in clears what was queued. The agent runs unchanged: Deepgram Flux
turn-taking, the transcript handler and turn ender, the tenant's model and
tools, the campaign's knowledge and voice. Caller lines are synthesised with
Deepgram Aura and spoken on cue, after the agent finishes or while it is still
talking.

Nothing is written: the database connections are read-only
(default_transaction_read_only), the app container is never started, so every
container-guarded write is skipped, and contact saves go to an in-memory
recorder. Outside calls: Deepgram (speech recognition and the caller's voice)
and the tenant's model and voice providers.

Why it exists (2026-10-09): a caller who said "Okay. Thank you. Bye." while
the agent was talking was silently dropped (14 of 133 Test Agent caller turns
since 1 October). Text replays (replay_call.py) cannot reach that path; this
does, with real speech timing.

usage, in backend/ on the server (reads .env for the database and keys):
  sudo venv/bin/python scripts/synthetic_caller.py --tenant <id> --campaign <id> \
      [--say "at:1.0:Hello?"] [--say "after:0.8:What do you offer?"] \
      [--say "during:1.5:Okay. Thank you. Bye."]

Cues: at:<s> from the start; after:<s> once the agent has answered the
previous line and finished playing; during:<s> into the agent's next reply,
while it is still playing.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import re
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.getcwd() if Path("app").is_dir() else str(Path(__file__).resolve().parents[1]))

from scripts.replay_call import RecorderPool, contact_summary, load_env  # noqa: E402

DEFAULT_SCRIPT = ["at:1.0:Hello?", "after:0.8:Hi. What do you offer, in a sentence?",
                  "during:1.5:Okay. Thank you. Bye."]
_EVENTS = re.compile(
    r"Flux (StartOfTurn|EndOfTurn|TurnResumed)|barge_in_detected|backchannel|turn_end\b|turn_queued|"
    r"turn_skipped|llm_response|voice_action_result|record_contact_result|knowledge_lookup|"
    r"agent_end_call|llm_end_session_action|end_call_stripped|Empty transcript|interrupt_step=task_cancelled|"
    r"barge_in_ignored|pre_tts_hold|Error processing turn|false_barge_in")


class Timeline(logging.Handler):
    """Keeps the pipeline's own log lines that describe turn-taking; with a
    log file, every app line goes there too."""

    def __init__(self, started: float, log_path: str | None = None):
        super().__init__(level=logging.DEBUG)
        self.started, self.rows = started, []
        self.log = open(log_path, "w", encoding="utf-8") if log_path else None

    def emit(self, record):
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad record must not stop the call
            return
        at = time.monotonic() - self.started
        if self.log is not None and record.name.startswith("app"):
            self.log.write(f"{at:7.2f} {record.levelname[:4]} [{record.name.split('.')[-1]}] {message}" + "\n")
        if _EVENTS.search(message):
            self.rows.append((at, message))


class FakeBrowser:
    """The Test Agent page: plays agent audio in real time and says when it ended."""

    def __init__(self, sample_rate: int, started: float):
        self.rate, self.started = sample_rate, started
        self.gateway = self.call_id = None
        self.play_until = 0.0
        self.utterances: list[tuple[float, str]] = []
        self.turns_completed = 0  # the pipeline's turn_complete messages
        self.closed = None
        self._timers: list[asyncio.Task] = []

    def playing(self) -> bool:
        return time.monotonic() < self.play_until

    async def send_bytes(self, data: bytes):
        now = time.monotonic()
        self.play_until = max(now, self.play_until) + len(data) / (self.rate * 2)

    async def send_json(self, payload: dict):
        kind = payload.get("type")
        if kind == "turn_complete":
            self.turns_completed += 1
        elif kind == "playback_start":
            self.utterances.append((time.monotonic() - self.started, str(payload.get("utterance_id"))))
        elif kind == "tts_audio_complete":
            self._timers.append(asyncio.create_task(self._complete(payload.get("utterance_id"))))
        elif kind == "barge_in":
            self.play_until = time.monotonic()  # the page drops what it queued

    async def _complete(self, utterance_id):
        await asyncio.sleep(max(0.0, self.play_until - time.monotonic()))
        if self.gateway is not None:
            self.gateway.mark_playback_complete(self.call_id, utterance_id)

    async def send_text(self, text: str):
        return None

    async def close(self, code: int = 1000, reason: str = ""):
        self.closed = (code, reason, round(time.monotonic() - self.started, 2))

    async def receive(self):
        await asyncio.Event().wait()


class Microphone:
    """20 ms frames of quiet room noise, with the caller's speech mixed in on cue."""

    def __init__(self, gateway, call_id: str, rate: int):
        self.gateway, self.call_id, self.rate = gateway, call_id, rate
        self.pending = bytearray()
        self.stopped = False
        rng = random.Random(7)
        self.noise = b"".join(int(rng.uniform(-40, 40)).to_bytes(2, "little", signed=True) for _ in range(rate))

    def say(self, pcm: bytes) -> float:
        self.pending.extend(pcm)
        return len(pcm) / (self.rate * 2)

    def speaking(self) -> bool:
        return bool(self.pending)

    async def run(self):
        frame = int(self.rate * 0.02) * 2
        offset, due = 0, time.monotonic()
        while not self.stopped and self.gateway.is_session_active(self.call_id):
            if self.pending:
                chunk = bytes(self.pending[:frame]).ljust(frame, b"\0")
                del self.pending[:frame]
            else:
                chunk = self.noise[offset:offset + frame]
                offset = (offset + frame) % (len(self.noise) - frame)
            await self.gateway.on_audio_received(self.call_id, chunk)
            due += 0.02
            await asyncio.sleep(max(0.0, due - time.monotonic()))


async def synthesise(text: str, rate: int, key: str) -> bytes:
    import httpx

    async with httpx.AsyncClient(timeout=30) as client:
        for model in ("aura-2-thalia-en", "aura-asteria-en"):
            response = await client.post(
                f"https://api.deepgram.com/v1/speak?model={model}&encoding=linear16&sample_rate={rate}&container=none",
                headers={"Authorization": f"Token {key}"}, json={"text": text})
            if response.status_code == 200 and response.content:
                return response.content
    raise RuntimeError(f"caller voice synthesis failed ({response.status_code})")


def parse_script(lines: list[str]) -> list[tuple[str, float, str]]:
    steps = []
    for line in lines:
        cue, delay, text = line.split(":", 2)
        if cue not in ("at", "after", "during"):
            raise SystemExit(f"unknown cue {cue!r} in {line!r}")
        steps.append((cue, float(delay), text.strip()))
    return steps


def dropped_turns(rows) -> list[float]:
    """EndOfTurn -> barge_in_detected with no turn before the next turn boundary."""
    events = [(t, m) for t, m in rows if re.search(r"Flux (StartOfTurn|EndOfTurn)|barge_in_detected|"
                                                     r"turn_end\b|turn_queued|turn_skipped|backchannel_suppressed", m)]
    lost = []
    for index, (t, message) in enumerate(events):
        if "Flux EndOfTurn" not in message or index + 1 >= len(events) or "barge_in_detected" not in events[index + 1][1]:
            continue
        answered = False
        for _, later in events[index + 2:]:
            if "Flux StartOfTurn" in later or "Flux EndOfTurn" in later:
                break
            if re.search(r"turn_end\b|turn_queued|turn_skipped|backchannel_suppressed", later):
                answered = True
                break
        if not answered:
            lost.append(round(t, 2))
    return lost


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--say", action="append", default=None, help="cue:seconds:text, repeatable")
    parser.add_argument("--first-speaker", default="user", choices=["user", "agent"])
    parser.add_argument("--wait", type=float, default=25.0, help="seconds to wait for each cue")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--log", default=None, help="write every app log line here")
    args = parser.parse_args()
    steps = parse_script(args.say or DEFAULT_SCRIPT)

    load_env(Path(args.env))
    import asyncpg
    from app.api.v1.endpoints.ai_options._shared import _fetch_tenant_config
    from app.api.v1.endpoints.campaign_test_ws import _fetch_campaign_row
    from app.domain.services.telephony.prewarm import callee_first_eot_timeout
    from app.domain.services.telephony_session_config import build_telephony_session_config
    from app.domain.services.tenant_ai_config_resolver import get_tenant_ai_config_resolver
    from app.domain.services.voice_orchestrator import Direction, VoiceOrchestrator, opening_mode_from_first_speaker
    from app.domain.services.voice_tuning import get_voice_tuning_resolver
    from app.services.scripts.knowledge.session_inject import apply_campaign_knowledge

    started = time.monotonic()
    timeline = Timeline(started, args.log)
    logging.basicConfig(level=logging.WARNING)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in root.handlers:
        handler.setLevel(logging.WARNING)
    root.addHandler(timeline)

    # Read-only at the database: a write attempted anywhere below fails there.
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3,
                                     server_settings={"default_transaction_read_only": "on"})

    async def ai_lookup(tid):
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                return await _fetch_tenant_config(conn, tid)

    get_tenant_ai_config_resolver().set_db_lookup(ai_lookup)
    ai_cfg = await get_tenant_ai_config_resolver().for_tenant_async(args.tenant, require_available=True)
    voice_tuning = await get_voice_tuning_resolver().for_tenant_async(args.tenant)
    campaign_row = await _fetch_campaign_row(pool, args.tenant, args.campaign)
    if campaign_row is None:
        raise SystemExit("campaign not found for that tenant")
    # Exactly as campaign_test_ws builds a Test Agent session.
    config = build_telephony_session_config(
        gateway_type="browser", campaign=campaign_row, direction=Direction.OUTBOUND,
        opening_mode=opening_mode_from_first_speaker(args.first_speaker),
        ai_config_override=ai_cfg, voice_tuning_override=voice_tuning, allow_browser_barge_in=True)
    if args.first_speaker == "user":
        config.stt_eot_timeout_ms = callee_first_eot_timeout(getattr(config, "stt_eot_timeout_ms", None))
    await apply_campaign_knowledge(config, campaign_row, pool=pool)
    # The voice-ownership check reads through db_client.pool; no table() means
    # no call-event logging.
    orchestrator = VoiceOrchestrator(db_client=SimpleNamespace(pool=pool))
    voice = await orchestrator.create_voice_session(config)
    call = voice.call_session
    voice._first_speaker = call._first_speaker = args.first_speaker
    call._enable_silence_monitor = True
    call._mute_during_tts = bool(config.mute_during_tts)
    call._voice_action_pool = RecorderPool()
    call._voice_action_context_loaded, call._voice_action_capabilities = True, {}
    call._dialer_call_id, call._dialer_tenant_id = str(uuid.uuid4()), args.tenant
    call._dialer_campaign_id, call._lead_capture_is_test = args.campaign, True
    if args.first_speaker == "user":
        from app.domain.services.telephony.modes.caller_first import select_inbound_base_prompt
        select_inbound_base_prompt(voice)

    gateway = voice.media_gateway
    rate = getattr(gateway, "_input_sample_rate", config.stt_sample_rate)
    browser = FakeBrowser(getattr(gateway, "_sample_rate", rate), started)
    browser.gateway, browser.call_id = gateway, voice.call_id
    print(f"synthetic call {voice.call_id[:8]} campaign={args.campaign[:8]} llm={config.llm_provider_type}/"
          f"{config.llm_model} tts={config.tts_provider_type} eot_timeout_ms={config.stt_eot_timeout_ms} "
          f"mute_during_tts={config.mute_during_tts} first_speaker={args.first_speaker}")
    voices = {text: await synthesise(text, rate, os.environ["DEEPGRAM_API_KEY"]) for _, _, text in steps}

    await orchestrator.start_pipeline(voice, browser)
    mic = Microphone(gateway, voice.call_id, rate)
    mic_task = asyncio.create_task(mic.run())
    said: list[tuple[float, float, str]] = []

    def agent_idle() -> bool:
        task = voice.pipeline._pending_llm_tasks.get(voice.call_id)
        return ((task is None or task.done()) and not getattr(call, "llm_active", False)
                and not browser.playing() and not mic.speaking())

    async def settled(limit: float, quiet: float = 0.7) -> None:
        """Until the agent has finished its turn and stayed quiet for a moment."""
        deadline, since = time.monotonic() + limit, None
        while time.monotonic() < deadline and gateway.is_session_active(voice.call_id):
            if agent_idle():
                since = since or time.monotonic()
                if time.monotonic() - since >= quiet:
                    return
            else:
                since = None
            await asyncio.sleep(0.05)

    async def until(predicate, limit: float) -> bool:
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            if predicate() or not gateway.is_session_active(voice.call_id):
                return predicate()
            await asyncio.sleep(0.02)
        return False

    for cue, delay, text in steps:
        if not gateway.is_session_active(voice.call_id):
            break
        replies_before = len(browser.utterances)
        if cue == "at":
            await asyncio.sleep(max(0.0, delay - (time.monotonic() - started)))
        elif cue == "after":
            # A reply is several sentences, each its own playback, with tool
            # rounds between them: wait until the pipeline's turn is over.
            await until(lambda: len(browser.utterances) > replies_before or not said, args.wait)
            await settled(args.wait)
            await asyncio.sleep(delay)
        else:  # during: into the agent's next reply, while it plays
            await until(lambda: len(browser.utterances) > replies_before and browser.playing(), args.wait)
            await asyncio.sleep(delay)
            if not browser.playing():
                print(f"  note: the reply had finished before {delay}s; '{text}' is spoken after it")
        at = time.monotonic() - started
        said.append((at, at + mic.say(voices[text]), text))

    await until(lambda: False, args.wait)  # let the last line play out and the agent answer
    mic.stopped = True
    await asyncio.gather(mic_task, return_exceptions=True)

    history = [(m.role.value, m.content) for m in call.conversation_history]
    rows = timeline.rows
    print("\n-- caller lines")
    for start, end, text in said:
        print(f"  {start:6.2f}-{end:5.2f}s  {text}")
    print("-- pipeline")
    for t, message in rows:
        print(f"  {t:6.2f}s  {message[:200]}")
    print("-- conversation as the model saw it")
    for role, content in history:
        print(f"  {role:9} {content[:220]}")
    lost = dropped_turns(rows)
    replies = [c for r, c in history if r == "assistant"]
    last = (replies[-1] if replies else "").lower()
    goodbyes = len(re.findall(r"\b(?:good ?bye|bye)\b", last))
    ended = browser.closed or not gateway.is_session_active(voice.call_id)
    print("-- verdict")
    print(f"  caller turns dropped after a barge-in: {len(lost)} {lost if lost else ''}")
    print(f"  caller lines: {len(said)}, caller turns in history: {sum(1 for r, _ in history if r == 'user')}")
    print(f"  goodbyes in the last reply: {goodbyes}; call ended by the agent: {bool(ended)} {browser.closed or ''}")
    print(f"  contact writes recorded (not persisted): {len(call._voice_action_pool.writes)}")
    print(f"  contacts: {contact_summary(call.captured_slots)}")
    print(f"  agent utterances played: {[(round(t, 2), u[:8]) for t, u in browser.utterances]}")

    if gateway.is_session_active(voice.call_id):
        await gateway.on_call_ended(voice.call_id, "synthetic_caller_done")
    if voice.pipeline_task is not None:
        voice.pipeline_task.cancel()
        await asyncio.gather(voice.pipeline_task, return_exceptions=True)
    for provider in (voice.stt_provider, voice.tts_provider, voice.llm_provider):
        cleanup = getattr(provider, "cleanup", None)
        if callable(cleanup):
            try:
                await cleanup()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
    await pool.close()
    return 0 if not lost else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
