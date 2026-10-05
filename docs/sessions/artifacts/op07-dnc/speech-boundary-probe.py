"""Actual native event/guard/playback path; failed DNC port, synthetic audio only."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from app.realtime.openai import RealtimeEvent
from tests.unit.test_realtime_end_call_ownership import _events, _fixture


async def main():
    bridge, provider, gateway, ended, session = _fixture()
    session.call_id = "synthetic-close"
    output = "I've removed your number from our calling list. Goodbye."
    failure = AsyncMock(return_value=False)
    with patch("app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", failure):
        try:
            await _events(bridge, provider,
                RealtimeEvent(kind="caller_transcript", text="Do not call me again. Goodbye.", is_final=True),
                RealtimeEvent(kind="response_candidate", text=output, audio=b"\xff" * 320))
            if bridge._termination_task:
                await asyncio.wait_for(bridge._termination_task, 1)
            print(json.dumps({
                "scope": "Actual native event pump, existing shared response guards and playback; synthetic provider candidate/audio/transport/DNC failure only",
                "input": "Do not call me again. Goodbye.",
                "provider_candidate": output,
                "dnc_attempts": failure.await_count,
                "dnc_acknowledged": False,
                "submitted_audio_chunks": gateway.send_audio.await_count,
                "submitted_audio_bytes": sum(len(call.args[1]) for call in gateway.send_audio.await_args_list),
                "hangup_callback_count": ended.await_count,
                "repair_requested": bool(getattr(bridge, "_repair_attempted", False)),
                "confirmation_language_fenced": gateway.send_audio.await_count == 0,
                "limits": "No provider request or real audio/phone/DB call. Synthetic gateway completion is not human-hearing proof. This probes wiring, not model frequency or semantic accuracy."
            }, indent=2))
        finally:
            await bridge.stop()


asyncio.run(main())
