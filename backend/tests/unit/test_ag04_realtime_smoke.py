"""The direct-provider smoke utility must consume the current adapter contract."""
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.realtime.openai import RealtimeEvent
from scripts import realtime_smoke


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_first", [False, True])
async def test_smoke_captures_complete_response_candidate(monkeypatch, tmp_path, capsys, tool_first):
    async def events():
        if tool_first:
            yield RealtimeEvent(kind="function_call", function_call=SimpleNamespace(
                name="lookup_knowledge", arguments={"query": "synthetic"}, call_id="tool-1"))
            yield RealtimeEvent(kind="response_done")
        yield RealtimeEvent(kind="response_candidate", text="Synthetic introduction.", audio=b"\xff" * 160)
        yield RealtimeEvent(kind="response_done")

    session = SimpleNamespace(connect=AsyncMock(return_value=True), send_text=AsyncMock(),
                              send_function_result=AsyncMock(), close=AsyncMock(), events=events,
                              stats=SimpleNamespace(to_dict=lambda: {"synthetic": True}))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-credential")
    monkeypatch.setattr(sys, "argv", ["realtime_smoke.py"])
    monkeypatch.setattr(realtime_smoke, "OpenAIRealtimeSession", lambda **_kw: session)
    monkeypatch.setattr(realtime_smoke, "RAW_OUT_PATH", str(tmp_path / "out.raw"))
    monkeypatch.setattr(realtime_smoke, "WAV_OUT_PATH", str(tmp_path / "out.wav"))
    assert await realtime_smoke.run() == 0
    assert (tmp_path / "out.raw").read_bytes() == b"\xff" * 160
    assert "Synthetic introduction." in capsys.readouterr().out
    assert session.send_function_result.await_count == int(tool_first)
    session.close.assert_awaited_once()
