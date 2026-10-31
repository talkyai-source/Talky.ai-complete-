"""Maintenance uses real quiescence signals and a reviewed plan digest."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.test_media_resource_reconciliation import PBX, PARENT, OLD


def load_script():
    spec = importlib.util.spec_from_file_location("media_maintenance", Path(__file__).parents[2] / "scripts/reconcile_orphaned_media.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response:
    status = 200

    def __init__(self, action):
        self.action = action

    async def __aenter__(self):
        self.body = await self.action()
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self.body


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["check", "changed_digest", "busy_gateway", "human_arrives", "apply"])
async def test_maintenance_guards(monkeypatch, capsys, case):
    script = load_script()
    pbx = PBX()
    plan = await script.discover_orphan_media(pbx.ari, "talky_ai", excluded=set())
    gateway_checks = 0

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def request(self, method, url, **kwargs):
            return Response(lambda: pbx.ari(method, url.split("/ari", 1)[1], **kwargs))

        def get(self, *args, **kwargs):
            async def body():
                nonlocal gateway_checks
                gateway_checks += 1
                if case == "human_arrives" and gateway_checks == 1:
                    pbx.channels.append({"id": PARENT, "name": "PJSIP/new-call", "creationtime": OLD})
                return {"sessions": [{"session_id": "active"}] if case == "busy_gateway" else []}
            return Response(body)

    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "test-only-password")
    monkeypatch.setenv("VOICE_GATEWAY_AUTH_TOKEN", "test-only-token")
    monkeypatch.setattr(script.aiohttp, "ClientSession", lambda **kwargs: Client())
    args = SimpleNamespace(apply=case != "check", expected_digest="changed" if case == "changed_digest" else script.plan_digest(plan))
    if case in {"changed_digest", "busy_gateway", "human_arrives"}:
        with pytest.raises((RuntimeError, ValueError)):
            await script.run(args)
    else:
        await script.run(args)
    if case == "apply":
        assert pbx.deleted == ["/bridges/legacy-bridge", "/channels/media"]
        assert gateway_checks >= 3  # before the run and before each DELETE
    else:
        assert not pbx.deleted
    if case == "check":
        assert gateway_checks == 0 and '"mode": "check"' in capsys.readouterr().out
