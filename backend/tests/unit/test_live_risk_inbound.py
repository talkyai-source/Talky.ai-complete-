"""Live-risk inbound fixes (audit 2026-10-20): inbound-1..6 and ops-8."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from scripts import reconcile_pjsip_configs as reconcile
import app.domain.services.telephony.inbound_admission as admission_module
from app.domain.services.inbound_campaign_service import InboundCampaignService
from app.domain.services.telephony.inbound_admission import (
    InboundAdmissionRequest,
    InboundAdmissionService,
)
from tests.unit import test_inbound_admission as adm
from tests.unit import test_inbound_campaign_service as camp
from tests.unit import test_pjsip_config_reconciliation as rec

BACKEND = Path(__file__).resolve().parents[2]


def _build(rows, tmp_path):
    return reconcile.build_candidate_set(
        rows,
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "secret",
        observed_at=rec.NOW,
    )


# ------------------------------------------------------------ inbound-2 / inbound-5
def test_duplicate_verified_account_trunk_is_named_not_raised(tmp_path):
    rogue = rec._row(rec.TRUNK_B, username="150001", inbound_did=None)
    candidate = _build([rec._assignment_row(), rogue], tmp_path)
    assert [r.trunk_id for r in candidate.routes] == [rec.TRUNK_A]
    assert f"trunk-{rec.TRUNK_B}:duplicate_verified_account" in candidate.unrouted_conflict_trunks


def test_duplicate_account_across_tenants_keeps_live_route_and_names_newcomer(tmp_path):
    other = rec._assignment_row(
        rec.TRUNK_B, trunk_tenant_id=rec.TENANT_B, assignment_tenant_id=rec.TENANT_B
    )
    other["assignment_id"] = "55555555-5555-4555-8555-555555555555"
    candidate = _build([rec._assignment_row(), other], tmp_path)
    assert [r.trunk_id for r in candidate.routes] == [rec.TRUNK_A]
    named = " ".join(candidate.unrouted_conflict_trunks)
    assert rec.TRUNK_B in named and rec.TRUNK_A not in named


def test_non_numeric_login_on_assigned_line_is_named_not_raised(tmp_path):
    candidate = _build([rec._assignment_row(account="alice")], tmp_path)
    assert candidate.routes == ()
    assert f"trunk-{rec.TRUNK_A}:unroutable_account" in candidate.unrouted_conflict_trunks


def test_assignment_did_conflicting_with_verified_map_is_named_not_raised(tmp_path):
    candidate = _build([rec._assignment_row(did="+17789249977")], tmp_path)
    assert candidate.routes == ()
    assert (
        f"trunk-{rec.TRUNK_A}:conflicts_with_verified_routing"
        in candidate.unrouted_conflict_trunks
    )


def test_assignment_on_non_inbound_trunk_is_named_not_raised(tmp_path):
    row = rec._assignment_row()
    row["direction"] = "outbound"
    candidate = _build([row], tmp_path)
    assert candidate.routes == ()
    assert f"trunk-{rec.TRUNK_A}:non_inbound_trunk" in candidate.unrouted_conflict_trunks


def test_one_bad_tenant_line_does_not_stop_a_good_line(tmp_path):
    bad = rec._assignment_row(
        rec.TRUNK_B,
        account="alice",
        trunk_tenant_id=rec.TENANT_B,
        assignment_tenant_id=rec.TENANT_B,
    )
    bad["assignment_id"] = "55555555-5555-4555-8555-555555555555"
    candidate = _build([rec._assignment_row(), bad], tmp_path)
    assert [r.trunk_id for r in candidate.routes] == [rec.TRUNK_A]


def test_active_assignment_with_unknown_account_is_named(tmp_path):
    candidate = _build([rec._assignment_row(account="250001")], tmp_path)
    assert candidate.routes == ()
    assert candidate.unrouted_unknown_account_trunks == (f"trunk-{rec.TRUNK_A}",)


def test_reconciler_output_carries_the_named_lists():
    src = (BACKEND / "scripts" / "reconcile_pjsip_configs.py").read_text(encoding="utf-8")
    assert '"unrouted_conflict_trunks"' in src
    assert '"unrouted_unknown_account_trunks"' in src


# ------------------------------------------------------------------- inbound-1
async def _admit(monkeypatch, endpoint):
    conn = adm._AllowedConn()
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_a: adm._acquire(conn))
    return await InboundAdmissionService(object(), adm._Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="pbx-call-1",
            called_did="+15551234567",
            caller_ani="+15550001111",
            reservation_seconds=90,
            metadata={"ingress_endpoint": endpoint},
        )
    )


@pytest.mark.asyncio
async def test_call_arriving_on_another_tenants_trunk_endpoint_is_denied(monkeypatch):
    decision = await _admit(monkeypatch, "trunk-99999999-9999-9999-9999-999999999999")
    assert decision.allowed is False
    assert decision.reason == "tenant_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [f"trunk-{adm.TRUNK}", "blazedigitel-endpoint", None])
async def test_own_trunk_shared_carrier_or_unknown_endpoint_is_admitted(monkeypatch, endpoint):
    decision = await _admit(monkeypatch, endpoint)
    assert decision.allowed is True, decision.reason


# ------------------------------------------------------------------- inbound-3
def test_platform_inbound_controls_route_to_the_call_process():
    conf = (BACKEND / "deploy" / "infra" / "nginx" / "api.talkleeai.com.conf").read_text(
        encoding="utf-8"
    )
    lines = [ln for ln in conf.splitlines() if "talky_call;" in ln and "admin/inbound" in ln]
    assert lines, "admin/inbound/controls must map to talky_call"
    pattern = re.search(r'"~(.+?)"', lines[0]).group(1)
    assert re.search(pattern, "PATCH:/api/v1/admin/inbound/controls")


# ------------------------------------------------------------------- inbound-4
def test_inbound_override_that_busts_the_guidance_budget_blocks_readiness():
    result = InboundCampaignService._readiness(
        camp._ready_bundle(qualification_config={"system_prompt": "x" * 15_000})
    )
    assert result["ready"] is False
    assert "qualification_runtime_supported" in {b["code"] for b in result["blockers"]}


def test_inbound_override_within_budget_stays_ready():
    result = InboundCampaignService._readiness(
        camp._ready_bundle(qualification_config={"system_prompt": "x" * 500})
    )
    assert "qualification_runtime_supported" not in {b["code"] for b in result["blockers"]}


# ------------------------------------------------------------------- inbound-6
def test_installer_starts_the_reconcile_timer():
    text = (BACKEND / "systemd" / "install-services.sh").read_text(encoding="utf-8")
    assert "systemctl enable --now talky-asterisk-reconcile.timer" in text


# ------------------------------------------------------------------- ops-8
def _find_bash():
    if os.name == "nt":
        for c in (
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
        ):
            if c.exists():
                return str(c)
    from shutil import which

    return which("bash")


def _sh(path: Path) -> str:
    r = str(path.resolve()).replace("\\", "/")
    if os.name == "nt" and r[1:3] == ":/":
        return f"/{r[0].lower()}{r[2:]}"
    return r


def _probe(tmp_path, *, preexisting: bool, new_after_originate: bool):
    bash = _find_bash()
    if not bash:
        pytest.skip("bash required")
    fake = tmp_path / "bin"
    fake.mkdir()
    state = tmp_path / "originated"
    body = (
        "#!/bin/bash\n"
        'case "$*" in\n'
        '  *"channel originate"*) touch "$STUB_STATE"; exit 0 ;;\n'
        '  *"core show channels concise"*)\n'
        '    [ -n "$STUB_PRE" ] && echo "PJSIP/real-caller-0000000a!from-talky-inbound!150001!3!Up!Stasis!x"\n'
        '    [ -n "$STUB_NEW" ] && [ -e "$STUB_STATE" ] && echo "PJSIP/blazedigitel-endpoint-0000000b!from-talky-inbound!150001!3!Up!Stasis!x"\n'
        "    exit 0 ;;\n"
        "esac\n"
    )
    for name, text in {
        "asterisk": body,
        "timeout": '#!/bin/bash\nshift\nexec "$@"\n',
        "sleep": "#!/bin/bash\nexit 0\n",
    }.items():
        t = fake / name
        t.write_text(text, encoding="utf-8", newline="\n")
        t.chmod(0o755)
    env = {
        **os.environ,
        "STUB_STATE": _sh(state),
        "STUB_PRE": "1" if preexisting else "",
        "STUB_NEW": "1" if new_after_originate else "",
        "INBOUND_SYNTHETIC_DID": "+442046132300",
        "INBOUND_SYNTHETIC_TRUNK_ENDPOINT": "blazedigitel-endpoint",
        "INBOUND_SYNTHETIC_WAIT_SECONDS": "8",
    }
    return subprocess.run(
        [
            bash,
            "-c",
            'export PATH="$1:$PATH"; exec bash "$2"',
            "bash",
            _sh(fake),
            _sh(BACKEND / "deploy" / "inbound-synthetic-call.sh"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_a_real_call_already_in_the_context_does_not_fake_a_pass(tmp_path):
    result = _probe(tmp_path, preexisting=True, new_after_originate=False)
    assert result.returncode == 1, result.stdout


def test_a_new_channel_after_the_probe_originates_passes(tmp_path):
    result = _probe(tmp_path, preexisting=True, new_after_originate=True)
    assert result.returncode == 0, result.stderr


# ---------------------------------------------- reviewer: no cross-tenant route eviction
def test_second_claimant_of_live_route_does_not_evict_the_live_route(tmp_path):
    live = rec._assignment_row()
    rival = rec._assignment_row(rec.TRUNK_B)
    rival["assignment_id"] = "55555555-5555-4555-8555-555555555555"
    candidate = _build([live, rival], tmp_path)
    assert [r.trunk_id for r in candidate.routes] == [rec.TRUNK_A]
    assert any(
        n.startswith(f"trunk-{rec.TRUNK_B}:") for n in candidate.unrouted_conflict_trunks
    )
    assert not any(
        n.startswith(f"trunk-{rec.TRUNK_A}:") for n in candidate.unrouted_conflict_trunks
    )
