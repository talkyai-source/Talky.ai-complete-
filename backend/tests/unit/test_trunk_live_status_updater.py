from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "trunk_live_status_updater.py"
SPEC = importlib.util.spec_from_file_location("trunk_live_status_updater", SCRIPT)
assert SPEC and SPEC.loader
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)


def _trunk(**overrides):
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "trunk_name": "tenant-byo",
        "is_active": True,
        "metadata": {"register": True},
    }
    row.update(overrides)
    return row


def test_registration_trunk_requires_loaded_endpoint_and_registration():
    tid = _trunk()["id"]
    endpoint = f"trunk-{tid}"
    registration = f"trunk-{tid}-reg"
    assert updater.status_for(
        _trunk(), {registration: "registered"}, {endpoint}
    ) == "registered"
    assert updater.status_for(
        _trunk(), {registration: "registered"}, set()
    ) == "missing_config"
    assert updater.status_for(
        _trunk(), {}, {endpoint}
    ) == "unregistered"


def test_ip_auth_trunk_uses_endpoint_presence_as_runtime_proof():
    tid = _trunk()["id"]
    row = _trunk(metadata={"register": False})
    assert updater.status_for(row, {}, {f"trunk-{tid}"}) == "loaded"
    assert updater.status_for(row, {}, set()) == "missing_config"
    assert updater.status_for(
        row, {}, set(), endpoints_ok=False
    ) == "unknown"


def test_platform_default_uses_hand_managed_registration_and_endpoint(monkeypatch):
    monkeypatch.delenv("PLATFORM_SIP_TRUNK_NAME", raising=False)
    row = _trunk(trunk_name="platform-default", metadata={})
    assert updater.status_for(
        row,
        {"blazedigitel-reg": "registered"},
        {"blazedigitel-endpoint"},
    ) == "registered"


def test_inactive_cleanup_removes_exact_managed_file_and_reloads_once(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEPHONY_PJSIP_CONF_DIR", str(tmp_path))
    tid = _trunk()["id"]
    target = tmp_path / f"trunk-{tid}.conf"
    target.write_text("secret", encoding="utf-8")
    calls: list[str] = []

    def cli(command: str):
        calls.append(command)
        return "", True

    monkeypatch.setattr(updater, "_asterisk_cli", cli)
    count = updater.reconcile_inactive_config_files(
        [_trunk(is_active=False)],
        {f"trunk-{tid}"},
    )
    assert count == 1
    assert not target.exists()
    assert calls == ["pjsip reload"]


def test_inactive_cleanup_never_touches_platform_default(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEPHONY_PJSIP_CONF_DIR", str(tmp_path))
    tid = _trunk()["id"]
    target = tmp_path / f"trunk-{tid}.conf"
    target.write_text("leave-me", encoding="utf-8")
    count = updater.reconcile_inactive_config_files(
        [_trunk(is_active=False, trunk_name="platform-default")],
        {f"trunk-{tid}"},
    )
    assert count == 0
    assert target.exists()
