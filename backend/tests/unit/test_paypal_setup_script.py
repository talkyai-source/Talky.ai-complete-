"""scripts/paypal_setup.py against a fake client and a temporary settings file."""
from __future__ import annotations

import importlib.util
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from app.domain.services.paypal_client import PayPalError
from app.domain.services.paypal_mode import PAYPAL_WEBHOOK_EVENTS

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location("paypal_setup_under_test", _SCRIPTS / "paypal_setup.py")
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)

URL = "https://api.example.com" + ps.WEBHOOK_PATH
CLIENT_ID = "sandbox-" + "id" * 5
CLIENT_SECRET = "sandbox-" + "sec" * 5
OTHER_VALUE = "other-" + "val" * 5
_ALL = (
    "PAYPAL_ENV", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET",
    "PAYPAL_SANDBOX_CLIENT_ID", "PAYPAL_SANDBOX_CLIENT_SECRET",
)


class FakeClient:
    def __init__(self, hooks=None, fail_on=None):
        self.hooks = hooks or []
        self.fail_on = fail_on
        self.calls: list[tuple] = []

    def _maybe_fail(self, name):
        self.calls.append((name,))
        if self.fail_on == name:
            raise PayPalError(status=400, name="BAD", issue="NOPE", debug_id="dbg1")

    async def list_webhooks(self):
        self._maybe_fail("list")
        return self.hooks

    async def create_webhook(self, *, url, event_names):
        self._maybe_fail("create")
        self.calls[-1] = ("create", url, tuple(event_names))
        return {"id": "WH-NEW1"}

    async def update_webhook_events(self, webhook_id, *, event_names):
        self._maybe_fail("update")
        self.calls[-1] = ("update", webhook_id, tuple(event_names))
        return {"id": webhook_id}


@pytest.fixture
def env(monkeypatch):
    for name in _ALL:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setenv("PAYPAL_SANDBOX_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("PAYPAL_SANDBOX_CLIENT_SECRET", CLIENT_SECRET)


@pytest.fixture
def settings_file(tmp_path):
    path = tmp_path / "app.env"
    path.write_text(f"FIRST=1\nOTHER_SETTING={OTHER_VALUE}\nPAYPAL_ENV=sandbox\n", encoding="utf-8")
    return path


def args_for(path, **kw):
    base = dict(env_file=path, webhook_url=URL, enable=None, apply=False)
    base.update(kw)
    return Namespace(**base)


def hook(events, url=URL, wid="WH-OLD1"):
    return {"id": wid, "url": url, "event_types": [{"name": n} for n in events]}


async def test_dry_run_nothing_registered(env, settings_file, capsys):
    before = settings_file.read_text()
    client = FakeClient()
    assert await ps.run(args_for(settings_file), client=client) == 0
    out = capsys.readouterr().out
    assert "would create webhook" in out
    assert "would set PAYPAL_WEBHOOK_ID" in out
    assert "dry run complete; nothing changed" in out
    assert [c[0] for c in client.calls] == ["list"]
    assert settings_file.read_text() == before
    assert len(list(settings_file.parent.iterdir())) == 1  # no backup in a dry run


async def test_apply_creates_and_writes_id(env, settings_file, capsys):
    client = FakeClient()
    assert await ps.run(args_for(settings_file, apply=True), client=client) == 0
    out = capsys.readouterr().out
    assert ("create", URL, PAYPAL_WEBHOOK_EVENTS) in client.calls
    assert "added PAYPAL_WEBHOOK_ID" in out
    assert "environment: sandbox, webhook id: WH-NEW1" in out
    assert out.rstrip().endswith("done")
    lines = settings_file.read_text().splitlines()
    assert "PAYPAL_WEBHOOK_ID=WH-NEW1" in lines
    assert lines[:3] == ["FIRST=1", f"OTHER_SETTING={OTHER_VALUE}", "PAYPAL_ENV=sandbox"]


async def test_existing_same_events_left_alone_id_written(env, settings_file, capsys):
    client = FakeClient([hook(PAYPAL_WEBHOOK_EVENTS), hook([], url="https://other.example/x", wid="WH-X")])
    assert await ps.run(args_for(settings_file, apply=True), client=client) == 0
    assert "webhook already registered" in capsys.readouterr().out
    assert [c[0] for c in client.calls] == ["list"]
    assert "PAYPAL_WEBHOOK_ID=WH-OLD1" in settings_file.read_text().splitlines()


async def test_existing_different_events_updated_only_with_apply(env, settings_file, capsys):
    stale = hook(["PAYMENT.CAPTURE.COMPLETED", "SOMETHING.ELSE"])
    dry = FakeClient([stale])
    assert await ps.run(args_for(settings_file), client=dry) == 0
    out = capsys.readouterr().out
    assert f"would update webhook events (missing: {len(PAYPAL_WEBHOOK_EVENTS) - 1}, extra: 1)" in out
    assert [c[0] for c in dry.calls] == ["list"]
    live = FakeClient([stale])
    assert await ps.run(args_for(settings_file, apply=True), client=live) == 0
    assert ("update", "WH-OLD1", PAYPAL_WEBHOOK_EVENTS) in live.calls
    assert "PAYPAL_WEBHOOK_ID=WH-OLD1" in settings_file.read_text().splitlines()


async def test_star_subscription_counts_as_complete(env, settings_file, capsys):
    client = FakeClient([hook(["*"])])
    assert await ps.run(args_for(settings_file, apply=True), client=client) == 0
    assert "webhook already registered" in capsys.readouterr().out
    assert [c[0] for c in client.calls] == ["list"]


async def test_enable_team_writes_switch(env, settings_file, capsys):
    client = FakeClient([hook(PAYPAL_WEBHOOK_EVENTS)])
    assert await ps.run(args_for(settings_file, apply=True, enable="team"), client=client) == 0
    assert "added PAYPAL_TOPUPS_ENABLED" in capsys.readouterr().out
    assert "PAYPAL_TOPUPS_ENABLED=team" in settings_file.read_text().splitlines()


async def test_dry_run_mentions_switch(env, settings_file, capsys):
    await ps.run(args_for(settings_file, enable="on"), client=FakeClient())
    assert "would set PAYPAL_TOPUPS_ENABLED=on" in capsys.readouterr().out


@pytest.mark.parametrize("bad", [
    "http://api.example.com" + ps.WEBHOOK_PATH,
    "https://api.example.com/api/v1/billing/webhooks",
    "",
])
async def test_bad_webhook_url_exits_before_any_call(env, settings_file, capsys, bad):
    client = FakeClient()
    assert await ps.run(args_for(settings_file, webhook_url=bad, apply=True), client=client) == 1
    assert client.calls == []
    assert "--webhook-url" in capsys.readouterr().out


async def test_missing_settings_name_only_names(monkeypatch, settings_file, capsys):
    for name in _ALL:
        monkeypatch.delenv(name, raising=False)
    client = FakeClient()
    assert await ps.run(args_for(settings_file, apply=True), client=client) == 1
    assert "PAYPAL_ENV" in capsys.readouterr().out
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setenv("PAYPAL_SANDBOX_CLIENT_ID", CLIENT_ID)
    assert await ps.run(args_for(settings_file, apply=True), client=client) == 1
    out = capsys.readouterr().out
    assert "PAYPAL_SANDBOX_CLIENT_SECRET" in out
    assert CLIENT_ID not in out
    assert client.calls == []


@pytest.mark.parametrize("step", ["list", "create"])
async def test_paypal_error_exits_1_and_writes_nothing(env, settings_file, capsys, step):
    before = settings_file.read_text()
    client = FakeClient(fail_on=step)
    assert await ps.run(args_for(settings_file, apply=True, enable="on"), client=client) == 1
    assert "PayPal error: status=400 name=BAD issue=NOPE debug_id=dbg1" in capsys.readouterr().out
    assert settings_file.read_text() == before


async def test_update_error_writes_nothing(env, settings_file, capsys):
    before = settings_file.read_text()
    client = FakeClient([hook(["X.Y"])], fail_on="update")
    assert await ps.run(args_for(settings_file, apply=True), client=client) == 1
    assert settings_file.read_text() == before


async def test_output_never_contains_credentials_or_values(env, settings_file, capsys):
    await ps.run(args_for(settings_file), client=FakeClient())
    await ps.run(args_for(settings_file, apply=True, enable="team"), client=FakeClient())
    out = capsys.readouterr().out
    for value in (CLIENT_ID, CLIENT_SECRET, OTHER_VALUE):
        assert value not in out
