"""scripts/set_inbound_campaign_recording.py: the recording box of one tenant's incoming campaigns.

The script must go through the service's update_campaign (never its own SQL), change
only the recording fields, write nothing without --apply or on any refusal, and
refuse an actor who is not a member of that tenant.
"""
import argparse
import importlib.util
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
TENANT = "aaaaaaaa-1111-4111-8111-000000000001"
OTHER_TENANT = "aaaaaaaa-2222-4222-8222-000000000002"
C1 = "c1c1c1c1-1111-4111-8111-000000000001"
C2 = "c2c2c2c2-2222-4222-8222-000000000002"
ADMIN = "11111111-1111-4111-8111-00000000ad01"
FOREIGN = "11111111-1111-4111-8111-00000000ad02"
DEFAULT_NOTICE = "This call may be recorded for quality and training purposes."


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location(
        "set_inbound_campaign_recording", BACKEND / "scripts/set_inbound_campaign_recording.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def campaign(cid=C1, name="Main line", status="paused", recording=False, notice=None, version=7,
             blockers=()):
    return {"id": cid, "name": name, "status": status, "recording_enabled": recording,
            "consent_message": notice, "version": version,
            "readiness": {"ready": not blockers, "blockers": [{"code": b} for b in blockers]}}


class Service:
    def __init__(self, campaigns=None, platform_recording=True):
        self.campaigns = {c["id"]: c for c in (campaigns if campaigns is not None else [campaign()])}
        self.controls = {"recording_enabled": platform_recording}
        self.updates = []
        self.lifecycle = []
        self.error = None
        self.activate_errors = []  # one entry popped per activation attempt; None = succeeds
        self.calls = []  # every service call after listing, in order

    async def get_platform_controls(self):
        return dict(self.controls)

    async def list_campaigns(self, *, tenant_id, include_archived=False):
        assert tenant_id == TENANT
        return [dict(c) for c in self.campaigns.values()]

    async def get_campaign(self, *, tenant_id, config_id):
        self.calls.append("get")
        return dict(self.campaigns[config_id])

    async def update_campaign(self, *, tenant_id, config_id, actor_id, actor_role, payload, idempotency_key):
        self.calls.append("update")
        self.updates.append({"tenant_id": tenant_id, "config_id": config_id, "actor_id": actor_id,
                             "actor_role": actor_role, "payload": dict(payload), "key": idempotency_key})
        if self.error is not None:
            raise self.error
        c = self.campaigns[config_id]
        c["recording_enabled"] = payload["recording_enabled"]
        if "consent_message" in payload:
            c["consent_message"] = payload["consent_message"]
        c["version"] += 1
        return dict(c)

    async def set_lifecycle(self, *, tenant_id, config_id, target_status, actor_id, actor_role,
                            idempotency_key, expected_version, reason=None):
        self.calls.append(target_status)
        self.lifecycle.append((config_id, target_status, expected_version))
        c = self.campaigns[config_id]
        if target_status == "active" and self.activate_errors:
            err = self.activate_errors.pop(0)
            if err is not None:
                raise err
        c["status"] = target_status
        c["version"] += 1
        return dict(c)


class FakeDb:
    def __init__(self, tenants=None, mode="one_party"):
        self.tenants = tenants if tenants is not None else [{"id": TENANT, "name": "Acme"}]
        self.mode = mode
        self.members = {ADMIN: "tenant_admin"}  # FOREIGN belongs to another tenant

    async def find_tenants(self, prefix):
        return [t for t in self.tenants if t["id"].startswith(prefix.lower())]

    async def recording_mode(self, tenant_id):
        return self.mode

    async def pick_actor(self, tenant_id):
        return ADMIN

    async def actor_role(self, tenant_id, user_id):
        return self.members.get(user_id)


def args(state=None, apply=False, tenant="aaaaaaaa-1111", campaign=None, actor=None,
         notice=DEFAULT_NOTICE, pause=False, reason="Owner decision: record incoming calls"):
    return argparse.Namespace(tenant=tenant, campaign=campaign, state=state, apply=apply,
                              actor_user_id=actor, notice=notice, pause_and_resume=pause, reason=reason)


async def test_show_only_writes_nothing(script, capsys):
    service = Service()
    assert await script.run(args(), service=service, db=FakeDb()) == 0
    assert service.updates == [] and service.lifecycle == []
    out = capsys.readouterr().out
    assert "recording=off" in out and "notice_saved=no" in out
    assert "platform recording switch: on" in out and "account recording setting: one_party" in out


async def test_no_saved_account_setting_is_said_plainly(script, capsys):
    assert await script.run(args(), service=Service(), db=FakeDb(mode=None)) == 0
    assert "no saved setting" in capsys.readouterr().out


async def test_a_prefix_matching_two_tenants_writes_nothing(script, capsys):
    db = FakeDb(tenants=[{"id": TENANT, "name": "A"}, {"id": "aaaaaaaa-1111-4111-8111-000000000009", "name": "B"}])
    service = Service()
    assert await script.run(args("on", apply=True), service=service, db=db) == 1
    assert service.updates == []
    assert "more than one account" in capsys.readouterr().out


async def test_a_prefix_matching_no_tenant_writes_nothing(script, capsys):
    service = Service()
    assert await script.run(args("on", apply=True, tenant="bbbbbbbb"), service=service, db=FakeDb()) == 1
    assert service.updates == []
    assert "no account" in capsys.readouterr().out


async def test_without_apply_nothing_is_written(script, capsys):
    service = Service()
    assert await script.run(args("on"), service=service, db=FakeDb()) == 0
    assert service.updates == []
    assert "nothing written" in capsys.readouterr().out


async def test_switching_on_updates_once_with_only_the_recording_fields(script, capsys):
    service = Service()
    assert await script.run(args("on", apply=True), service=service, db=FakeDb()) == 0
    assert len(service.updates) == 1
    u = service.updates[0]
    assert (u["tenant_id"], u["config_id"], u["actor_id"], u["actor_role"]) == (TENANT, C1, ADMIN, "tenant_admin")
    assert u["payload"] == {"expected_version": 7, "recording_enabled": True,
                            "reason": "Owner decision: record incoming calls",
                            "consent_message": DEFAULT_NOTICE}
    assert service.lifecycle == []
    out = capsys.readouterr().out
    assert 'campaign c1c1c1c1 "Main line": recording is now on' in out
    assert ADMIN not in out


async def test_a_campaign_that_already_has_a_notice_keeps_it(script):
    service = Service([campaign(notice="Calls are recorded.")])
    assert await script.run(args("on", apply=True), service=service, db=FakeDb()) == 0
    assert "consent_message" not in service.updates[0]["payload"]


async def test_already_on_is_left_alone(script, capsys):
    service = Service([campaign(recording=True, notice="x")])
    assert await script.run(args("on", apply=True), service=service, db=FakeDb()) == 0
    assert service.updates == []
    assert 'campaign c1c1c1c1 "Main line": already on; nothing to do' in capsys.readouterr().out


async def test_switching_off_sends_only_the_box(script):
    service = Service([campaign(recording=True, notice="x")])
    assert await script.run(args("off", apply=True), service=service, db=FakeDb(mode="disabled")) == 0
    assert service.updates[0]["payload"] == {"expected_version": 7, "recording_enabled": False,
                                            "reason": "Owner decision: record incoming calls"}


async def test_a_disabled_account_setting_is_refused(script, capsys):
    service = Service()
    assert await script.run(args("on", apply=True), service=service, db=FakeDb(mode="disabled")) == 1
    assert service.updates == []
    assert "disabled" in capsys.readouterr().out


async def test_platform_switch_off_is_refused(script, capsys):
    service = Service(platform_recording=False)
    assert await script.run(args("on", apply=True), service=service, db=FakeDb()) == 1
    assert service.updates == []
    assert "platform recording switch is off" in capsys.readouterr().out


async def test_a_version_conflict_is_reported_and_fails(script, capsys):
    class Conflict(Exception):
        code = "version_conflict"

    service = Service()
    service.error = Conflict("Version conflict: expected 7, current 8")
    assert await script.run(args("on", apply=True), service=service, db=FakeDb()) == 1
    assert len(service.updates) == 1  # not retried
    out = capsys.readouterr().out
    assert "not changed: version_conflict" in out and "expected 7" not in out


async def test_campaign_option_limits_the_change_to_that_campaign(script):
    service = Service([campaign(C1), campaign(C2, name="Second")])
    assert await script.run(args("on", apply=True, campaign="c2c2c2c2"), service=service, db=FakeDb()) == 0
    assert [u["config_id"] for u in service.updates] == [C2]


async def test_default_is_every_incoming_campaign_of_the_tenant(script):
    service = Service([campaign(C1), campaign(C2, name="Second")])
    assert await script.run(args("on", apply=True), service=service, db=FakeDb()) == 0
    assert sorted(u["config_id"] for u in service.updates) == [C1, C2]


async def test_campaign_prefix_matching_nothing_writes_nothing(script, capsys):
    service = Service()
    assert await script.run(args("on", apply=True, campaign="deadbeef"), service=service, db=FakeDb()) == 1
    assert service.updates == []


async def test_an_actor_from_another_tenant_is_refused(script, capsys):
    service = Service()
    assert await script.run(args("on", apply=True, actor=FOREIGN), service=service, db=FakeDb()) == 1
    assert service.updates == []
    assert "not an active member" in capsys.readouterr().out


async def test_an_opt_out_notice_already_saved_is_refused_with_the_products_message(script, capsys):
    service = Service([campaign(notice="Recorded. Press 9 to opt out.")])
    assert await script.run(args("on", apply=True), service=service, db=FakeDb()) == 1
    assert service.updates == []
    assert "cannot offer callers a way to opt out" in capsys.readouterr().out


async def test_a_live_campaign_is_not_touched_without_pause_and_resume(script, capsys):
    service = Service([campaign(status="active")])
    assert await script.run(args("on", apply=True), service=service, db=FakeDb()) == 1
    assert service.updates == [] and service.lifecycle == []
    assert "pause_before_edit" in capsys.readouterr().out


async def test_pause_and_resume_pauses_updates_with_new_version_and_activates(script):
    service = Service([campaign(status="active", version=7)])
    assert await script.run(args("on", apply=True, pause=True), service=service, db=FakeDb()) == 0
    assert service.lifecycle == [(C1, "paused", 7), (C1, "active", 9)]
    assert service.updates[0]["payload"]["expected_version"] == 8
    assert service.campaigns[C1]["status"] == "active"


class Refused(Exception):
    def __init__(self, code):
        super().__init__("text that must never be printed")
        self.code = code


async def test_live_campaign_with_passing_readiness_pauses_updates_activates_with_nothing_between(script, capsys):
    service = Service([campaign(status="active")])
    assert await script.run(args("on", apply=True, pause=True), service=service, db=FakeDb()) == 0
    assert service.calls[:3] == ["paused", "update", "active"]  # no read between pause and activate
    out = capsys.readouterr().out
    assert "paused at " in out and "active again at " in out and "gap " in out and "UTC" in out


async def test_live_campaign_whose_readiness_fails_is_not_paused_or_written(script, capsys):
    service = Service([campaign(status="active", blockers=("trunk_ready", "did_verified"))])
    assert await script.run(args("on", apply=True, pause=True), service=service, db=FakeDb()) == 1
    assert service.calls == [] and service.updates == []
    assert ("not changed: the campaign would not pass its own readiness check as it stands: "
            "trunk_ready, did_verified") in capsys.readouterr().out


async def test_live_campaign_without_readiness_report_is_not_paused(script):
    c = campaign(status="active")
    del c["readiness"]
    service = Service([c])
    assert await script.run(args("on", apply=True, pause=True), service=service, db=FakeDb()) == 1
    assert service.calls == []


async def test_saved_account_setting_required_by_the_product_blocks_the_pause(script, monkeypatch, capsys):
    monkeypatch.setattr(script, "notice_proof_required", lambda: True)
    service = Service([campaign(status="active")])
    assert await script.run(args("on", apply=True, pause=True), service=service, db=FakeDb(mode=None)) == 1
    assert service.calls == []
    assert "saved account recording setting" in capsys.readouterr().out


async def test_activation_refused_restores_previous_values_and_activates_again(script, capsys):
    service = Service([campaign(status="active", recording=False, notice=None)])
    service.activate_errors = [Refused("not_ready"), None]
    assert await script.run(args("on", apply=True, pause=True), service=service, db=FakeDb()) == 1
    assert service.calls == ["paused", "update", "active", "update", "active"]
    assert service.updates[0]["payload"]["recording_enabled"] is True
    restore = service.updates[1]["payload"]
    assert restore["recording_enabled"] is False and restore["consent_message"] is None
    assert service.campaigns[C1]["status"] == "active" and service.campaigns[C1]["recording_enabled"] is False
    out = capsys.readouterr().out
    assert ("not changed: activation was refused (not_ready); the previous setting was restored "
            "and the campaign is active again") in out
    assert "text that must never be printed" not in out


async def test_second_activation_refused_says_still_paused_in_capitals(script, capsys):
    service = Service([campaign(status="active")])
    service.activate_errors = [Refused("not_ready"), Refused("trunk_ready")]
    assert await script.run(args("on", apply=True, pause=True), service=service, db=FakeDb()) == 1
    assert service.campaigns[C1]["status"] == "paused"
    out = capsys.readouterr().out
    assert "CAMPAIGN STILL PAUSED: trunk_ready" in out and "NOT active again" in out


async def test_update_failing_after_the_pause_activates_again(script, capsys):
    service = Service([campaign(status="active")])
    service.error = Refused("unsupported_recording_notice")
    assert await script.run(args("on", apply=True, pause=True), service=service, db=FakeDb()) == 1
    assert service.calls[0] == "paused" and "active" in service.calls
    assert service.campaigns[C1]["status"] == "active"
    assert "not changed: unsupported_recording_notice; it was paused by this tool and is live again" in capsys.readouterr().out


async def test_show_mode_says_what_apply_would_do_for_each_case(script, capsys):
    service = Service([
        campaign(C1, "Paused one"),
        campaign(C2, "Live one", status="active"),
        campaign("c3c3c3c3-3333-4333-8333-000000000003", "Done one", recording=True, notice="x"),
        campaign("c4c4c4c4-4444-4444-8444-000000000004", "Broken one", status="active",
                 blockers=("trunk_ready",)),
    ])
    assert await script.run(args("on", pause=True), service=service, db=FakeDb()) == 1
    out = capsys.readouterr().out
    assert "would switch recording on" in out
    assert "would switch recording on (pauses the live campaign" in out
    assert "already on" in out
    assert "would be refused: the campaign would not pass its own readiness check as it stands: trunk_ready" in out
    assert "live=yes" in out and "live=no" in out
    assert service.calls == []


async def test_show_mode_says_a_live_campaign_needs_the_flag(script, capsys):
    service = Service([campaign(status="active")])
    assert await script.run(args(), service=service, db=FakeDb()) == 0
    assert "would need --pause-and-resume because the campaign is live" in capsys.readouterr().out
