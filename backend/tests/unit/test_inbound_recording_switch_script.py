"""scripts/set_inbound_recording_switch.py: the platform switch for recording inbound calls.

The script must change ONLY the recording switch, go through the audited service
path, write nothing without --apply, and print no account detail.
"""
import argparse
import importlib.util
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
ADMIN = "11111111-1111-4111-8111-00000000ad01"


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location(
        "set_inbound_recording_switch", BACKEND / "scripts/set_inbound_recording_switch.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Service:
    def __init__(self, **state):
        self.state = {"inbound_enabled": True, "recording_enabled": False, "transfer_enabled": True,
                      "settlement_enabled": True, "version": 5, **state}
        self.writes = []
        self.error = None

    async def get_platform_controls(self):
        return dict(self.state)

    async def set_platform_controls(self, *, actor_id, actor_role, payload, idempotency_key):
        self.writes.append({"actor_id": actor_id, "actor_role": actor_role,
                            "payload": dict(payload), "idempotency_key": idempotency_key})
        if self.error is not None:
            raise self.error
        self.state.update(
            inbound_enabled=payload["inbound_enabled"], recording_enabled=payload["recording_enabled"],
            transfer_enabled=payload["transfer_enabled"], settlement_enabled=payload["settlement_enabled"],
            version=self.state["version"] + 1,
        )
        return dict(self.state)


def args(state=None, apply=False, actor=ADMIN, reason="Owner decision: recording on inbound calls"):
    return argparse.Namespace(state=state, apply=apply, actor_user_id=actor, reason=reason)


async def test_without_a_state_it_only_shows(script, capsys):
    service = Service()
    assert await script.run(args(), service=service) == 0
    assert service.writes == []
    out = capsys.readouterr().out
    assert "recording=False" in out and "version=5" in out


async def test_without_apply_nothing_is_written(script, capsys):
    service = Service()
    assert await script.run(args("on"), service=service) == 0
    assert service.writes == []
    assert "nothing written" in capsys.readouterr().out


async def test_switching_on_changes_only_the_recording_switch(script, capsys):
    service = Service()
    assert await script.run(args("on", apply=True), service=service) == 0
    assert len(service.writes) == 1
    write = service.writes[0]
    assert write["actor_id"] == ADMIN and write["actor_role"] == "platform_admin"
    assert write["payload"] == {
        "inbound_enabled": True, "recording_enabled": True, "transfer_enabled": True,
        "settlement_enabled": True, "reason": "Owner decision: recording on inbound calls",
        "expected_version": 5,
    }
    assert write["idempotency_key"] == "inbound-recording-on-v5"
    out = capsys.readouterr().out
    assert "recording of inbound calls is now on" in out
    assert ADMIN not in out  # no account detail is printed


async def test_the_other_three_switches_go_back_exactly_as_read(script):
    service = Service(inbound_enabled=False, transfer_enabled=False, settlement_enabled=False)
    assert await script.run(args("on", apply=True), service=service) == 0
    payload = service.writes[0]["payload"]
    assert (payload["inbound_enabled"], payload["transfer_enabled"], payload["settlement_enabled"]) == (False, False, False)
    assert payload["recording_enabled"] is True


async def test_switching_off_is_the_same_path(script):
    service = Service(recording_enabled=True)
    assert await script.run(args("off", apply=True), service=service) == 0
    assert service.writes[0]["payload"]["recording_enabled"] is False
    assert service.writes[0]["idempotency_key"] == "inbound-recording-off-v5"


async def test_already_in_the_wanted_state_writes_nothing(script, capsys):
    service = Service(recording_enabled=True)
    assert await script.run(args("on", apply=True), service=service) == 0
    assert service.writes == []
    assert "already on" in capsys.readouterr().out


async def test_a_refusal_is_reported_by_its_code_and_fails(script, capsys):
    service = Service()

    class Refused(Exception):
        code = "version_conflict"

    service.error = Refused("Version conflict: expected 5, current 6 for a named admin")
    assert await script.run(args("on", apply=True), service=service) == 1
    out = capsys.readouterr().out
    assert "not switched: version_conflict" in out
    assert "named admin" not in out  # the error text is never printed


async def test_the_note_when_the_master_switch_is_off(script, capsys):
    service = Service(inbound_enabled=False)
    assert await script.run(args("on"), service=service) == 0
    assert "master inbound switch is off" in capsys.readouterr().out


async def test_no_administrator_account_means_nothing_is_written(script, capsys, monkeypatch):
    async def nobody(pool):
        return None

    monkeypatch.setattr(script, "_pick_platform_admin", nobody)
    service = Service()
    assert await script.run(args("on", apply=True, actor=None), service=service, pool=object()) == 1
    assert service.writes == []
    assert "nothing written" in capsys.readouterr().out


async def test_the_administrator_is_picked_when_none_is_given(script, monkeypatch):
    async def picked(pool):
        return ADMIN

    monkeypatch.setattr(script, "_pick_platform_admin", picked)
    service = Service()
    assert await script.run(args("on", apply=True, actor=None), service=service, pool=object()) == 0
    assert service.writes[0]["actor_id"] == ADMIN
