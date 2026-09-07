"""Lost ARI responses must not erase the identity of a created resource."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_bridge_creation_has_cleanup_identity_before_response(cancelled):
    adapter = AsteriskAdapter()
    adapter._alloc_rtp_port = AsyncMock(return_value=31000)
    adapter._release_rtp_port = AsyncMock()
    requests = []

    async def ari(method, path, **kwargs):
        requests.append((method, path, kwargs))
        if method == "POST" and path == "/bridges":
            if cancelled:
                raise asyncio.CancelledError()
            raise TimeoutError("Asterisk created the bridge but its response was lost")
        return {}

    adapter._ari = ari
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await adapter._on_outbound_stasis_start("parent")
    else:
        await adapter._on_outbound_stasis_start("parent")
    bridge_id = requests[0][2]["params"].get("bridgeId")
    assert bridge_id and bridge_id.startswith("talky-outbound-bridge-")
    assert any(method == "DELETE" and path == f"/bridges/{bridge_id}" for method, path, _ in requests)
    adapter._release_rtp_port.assert_awaited_once_with(31000)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_external_media_creation_has_cleanup_identity_before_response(cancelled):
    adapter = AsteriskAdapter()
    adapter._pending_outbound["parent"] = {
        "bridge_id": "bridge", "listen_port": 31000, "session_id": "session",
    }
    adapter._persist_outbound_answer_obligation = AsyncMock()
    adapter._cleanup_failed_outbound_setup = AsyncMock()
    created = []

    async def ari(method, path, **kwargs):
        assert path == "/channels/externalMedia"
        created.append(kwargs["params"].get("channelId"))
        if cancelled:
            raise asyncio.CancelledError()
        raise TimeoutError("Asterisk created media but its response was lost")

    adapter._ari = ari
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await adapter._on_outbound_answered("parent")
    else:
        await adapter._on_outbound_answered("parent")
    assert created[0] and created[0].startswith("talky-outbound-media-")
    assert adapter._cleanup_failed_outbound_setup.await_args.kwargs["ext_channel_id"] == created[0]
    assert adapter._outbound_answer_setup_resources["parent"]["ext_channel_id"] == created[0]
