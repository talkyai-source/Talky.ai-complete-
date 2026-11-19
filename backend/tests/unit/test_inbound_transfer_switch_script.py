"""scripts/set_inbound_transfer_switch.py: the platform switch for transfers on inbound calls.

The script must change ONLY the transfer switch, go through the audited service
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
        "set_inbound_transfer_switch", BACKEND / "scripts/set_inbound_transfer_switch.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Service:
    def __init__(self, **state):
        self.state = {"inbound_enabled": True, "recording_enabled": True, "transfer_enabled": False,
                      "settlement_enabled": True, "version": 7, **state}
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


def args(state=None, apply=False, actor=ADMIN, reason="Owner decision: transfer on inbound calls"):
    return argparse.Namespace(state=state, apply=apply, actor_user_id=actor, reason=reason)


async def test_without_a_state_it_only_shows(script, capsys):
    service = Service()
    assert await script.run(args(), service=service) == 0
    assert service.writes == []
    out = capsys.readouterr().out
    assert "transfer=False" in out and "version=7" in out


async def test_without_apply_nothing_is_written(script, capsys):
    service = Service()
    assert await script.run(args("on"), service=service) == 0
    assert service.writes == []
    assert "nothing written" in capsys.readouterr().out


async def test_switching_on_changes_only_the_transfer_switch(script, capsys):
    service = Service(recording_enabled=False, settlement_enabled=True)
    assert await script.run(args("on", apply=True), service=service) == 0
    (write,) = service.writes
    assert write["payload"] == {
        "inbound_enabled": True, "recording_enabled": False, "transfer_enabled": True,
        "settlement_enabled": True, "reason": "Owner decision: transfer on inbound calls",
        "expected_version": 7,
    }
    assert write["actor_id"] == ADMIN and write["actor_role"] == "platform_admin"
    assert write["idempotency_key"] == "inbound-transfer-on-v7"
    out = capsys.readouterr().out
    assert "transfer for inbound calls is now on" in out
    assert ADMIN not in out, "no account id is printed"


async def test_switching_off_is_the_same_path(script):
    service = Service(transfer_enabled=True)
    assert await script.run(args("off", apply=True), service=service) == 0
    assert service.writes[0]["payload"]["transfer_enabled"] is False
    assert service.writes[0]["idempotency_key"] == "inbound-transfer-off-v7"


async def test_already_in_the_wanted_state_writes_nothing(script, capsys):
    service = Service(transfer_enabled=True)
    assert await script.run(args("on", apply=True), service=service) == 0
    assert service.writes == []
    assert "already on" in capsys.readouterr().out


async def test_a_refusal_is_reported_by_its_code_and_fails(script, capsys):
    service = Service()

    class Refused(Exception):
        code = "transfer_runtime_unavailable"

    service.error = Refused("Inbound transfer cannot be enabled until ... internal detail")
    assert await script.run(args("on", apply=True), service=service) == 1
    out = capsys.readouterr().out
    assert "not switched: transfer_runtime_unavailable" in out
    assert "internal detail" not in out, "the error text is not printed, only its code"


async def test_the_notes_about_the_other_switches(script, capsys):
    service = Service(settlement_enabled=False, inbound_enabled=False)
    assert await script.run(args("on"), service=service) == 0
    out = capsys.readouterr().out
    assert "settlement switch is off" in out and "master inbound switch is off" in out


async def test_no_administrator_account_means_nothing_is_written(script, capsys, monkeypatch):
    service = Service()

    async def nobody(pool):
        return None

    monkeypatch.setattr(script, "_pick_platform_admin", nobody)
    assert await script.run(args("on", apply=True, actor=None), service=service, pool=object()) == 1
    assert service.writes == []
    assert "no active, verified platform administrator" in capsys.readouterr().out


async def test_the_administrator_is_picked_when_none_is_given(script, monkeypatch):
    service = Service()

    async def oldest(pool):
        return ADMIN

    monkeypatch.setattr(script, "_pick_platform_admin", oldest)
    assert await script.run(args("on", apply=True, actor=None), service=service, pool=object()) == 0
    assert service.writes[0]["actor_id"] == ADMIN


def test_the_service_accepts_enabling_now_that_the_runtime_is_declared_available():
    """The enable used to be refused in code. It is open by the owner's decision
    of 19 November 2026; the live switch is what this script changes."""
    from app.domain.services.telephony import inbound_transfer

    assert inbound_transfer.CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE is True
    assert inbound_transfer.inbound_transfer_runtime_available() is True
    assert inbound_transfer.inbound_transfer_scope_available(tenant_id=None, config_id=None) is True
