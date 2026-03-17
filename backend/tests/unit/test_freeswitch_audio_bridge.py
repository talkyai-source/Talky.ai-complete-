import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import WebSocketDisconnect

from app.infrastructure.telephony.freeswitch_audio_bridge import FreeSwitchAudioBridge


class FreeSwitchAudioBridgeTests(unittest.IsolatedAsyncioTestCase):
    def test_loads_websocket_timing_config(self) -> None:
        with patch(
            "app.infrastructure.telephony.freeswitch_audio_bridge.ConfigManager"
        ) as mock_config_manager:
            mock_config_manager.return_value.get_websocket_config.return_value = {
                "connection_timeout_seconds": 123,
                "heartbeat_interval_seconds": 45,
                "heartbeat_timeout_seconds": 9,
            }
            bridge = FreeSwitchAudioBridge()

        self.assertEqual(bridge._connection_timeout_seconds, 123.0)
        self.assertEqual(bridge._heartbeat_interval_seconds, 45.0)
        self.assertEqual(bridge._heartbeat_timeout_seconds, 9.0)

    async def test_handle_websocket_forwards_audio_and_fires_callbacks(self) -> None:
        bridge = FreeSwitchAudioBridge()
        on_audio = AsyncMock()
        on_start = AsyncMock()
        on_end = AsyncMock()
        bridge.set_audio_callback(on_audio)
        bridge.set_session_start_callback(on_start)
        bridge.set_session_end_callback(on_end)

        websocket = AsyncMock()
        websocket.receive_bytes = AsyncMock(
            side_effect=[b"audio-frame", WebSocketDisconnect(code=1000)]
        )

        await bridge.handle_websocket(websocket, "call-12345678")
        await asyncio.sleep(0)

        websocket.accept.assert_awaited_once()
        on_audio.assert_awaited_once_with("call-12345678", b"audio-frame")
        on_start.assert_awaited_once_with("call-12345678")
        on_end.assert_awaited_once_with("call-12345678")
        self.assertFalse(bridge.is_session_active("call-12345678"))

    async def test_handle_websocket_closes_idle_session_on_timeout(self) -> None:
        bridge = FreeSwitchAudioBridge()
        bridge._connection_timeout_seconds = 0.01

        websocket = AsyncMock()
        websocket.receive_bytes = AsyncMock(side_effect=asyncio.TimeoutError)

        await bridge.handle_websocket(websocket, "call-timeout")

        websocket.accept.assert_awaited_once()
        websocket.close.assert_awaited_once_with(
            code=1001,
            reason="audio session idle timeout",
        )
        self.assertFalse(bridge.is_session_active("call-timeout"))


if __name__ == "__main__":
    unittest.main()