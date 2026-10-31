"""Reload receipts must observe the owned command, not another request's intent."""
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.infrastructure.telephony import pjsip_config_generator as pjsip


@pytest.fixture(autouse=True)
def reload_state(monkeypatch):
    monkeypatch.setattr(pjsip, "_reload_lock", asyncio.Lock())
    monkeypatch.setenv("TELEPHONY_PJSIP_AUTO_RELOAD", "on")


def row(trunk_id):
    return {
        "id": trunk_id, "tenant_id": "receipt-tenant", "trunk_name": "owned",
        "sip_domain": "sip.example.test", "port": 5060, "transport": "udp",
        "auth_username": "test-user", "metadata": {"register": True},
    }


def install_ports(monkeypatch, sleep, spawn):
    # Do not monkeypatch the interpreter's global asyncio module.
    monkeypatch.setattr(pjsip, "asyncio", SimpleNamespace(
        sleep=sleep, create_subprocess_exec=spawn, subprocess=asyncio.subprocess,
        create_task=asyncio.create_task, wait_for=asyncio.wait_for,
        shield=asyncio.shield, CancelledError=asyncio.CancelledError,
        TimeoutError=asyncio.TimeoutError,
    ))


@pytest.mark.asyncio
async def test_second_required_change_waits_and_observes_reload_failure(tmp_path, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    spawned = []

    async def debounce(_):
        entered.set()
        await release.wait()

    async def spawn(*args, **kwargs):
        spawned.append(args)

        async def communicate():
            return b"", b"synthetic reload rejected"

        return SimpleNamespace(returncode=1, communicate=communicate)

    install_ports(monkeypatch, debounce, spawn)
    first = asyncio.create_task(pjsip.apply_trunk_config(
        row("first"), decrypted_password="synthetic", base_dir=tmp_path,
        require_reload=True,
    ))
    await entered.wait()
    second = asyncio.create_task(pjsip.apply_trunk_config(
        row("second"), decrypted_password="synthetic", base_dir=tmp_path,
        require_reload=True,
    ))
    await asyncio.sleep(0)
    returned_before_acknowledgement = second.done()
    release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert not returned_before_acknowledgement
    assert all(isinstance(result, pjsip.PJSIPReloadError) for result in results)
    assert spawned == [("asterisk", "-rx", "pjsip reload")] * 2


@pytest.mark.asyncio
async def test_production_sync_hook_rejects_both_activations_and_removes_uncommitted_files(tmp_path, monkeypatch):
    from app.api.v1.endpoints.telephony_sip import trunk_probe, trunks

    entered, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(trunks, "_requires_confirmed_pjsip_apply", lambda: True)
    monkeypatch.setattr(trunk_probe, "resolve_sip_target", AsyncMock(return_value=None))
    monkeypatch.setattr(pjsip, "pjsip_d_dir", lambda: tmp_path)

    async def debounce(_):
        entered.set()
        await release.wait()

    async def spawn(*args, **kwargs):
        async def communicate():
            return b"", b"synthetic failure"

        return SimpleNamespace(returncode=1, communicate=communicate)

    install_ports(monkeypatch, debounce, spawn)
    records = [dict(row(identity), auth_password_encrypted=None, metadata={"register": False})
               for identity in ("first", "second")]
    first = asyncio.create_task(trunks._sync_trunk_pjsip_config(records[0], active=True))
    await entered.wait()
    second = asyncio.create_task(trunks._sync_trunk_pjsip_config(records[1], active=True))
    for _ in range(4):
        await asyncio.sleep(0)
    second_returned_early = second.done()
    release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert not second_returned_early
    assert all(isinstance(result, HTTPException) and result.status_code == 503 for result in results)
    assert not pjsip.trunk_conf_path("first", base_dir=tmp_path).exists()
    assert not pjsip.trunk_conf_path("second", base_dir=tmp_path).exists()


@pytest.mark.asyncio
async def test_commands_do_not_overlap_and_each_receipt_is_independent(monkeypatch):
    first_started, finish_first = asyncio.Event(), asyncio.Event()
    spawned = []

    async def debounce(_):
        await asyncio.sleep(0)

    async def spawn(*args, **kwargs):
        number = len(spawned)
        spawned.append(number)

        async def communicate():
            if number == 0:
                first_started.set()
                await finish_first.wait()
                return b"", b"first failed"
            return b"Reload queued", b""

        return SimpleNamespace(returncode=1 if number == 0 else 0, communicate=communicate)

    install_ports(monkeypatch, debounce, spawn)
    first = asyncio.create_task(pjsip.request_pjsip_reload(execute=True))
    await first_started.wait()
    second = asyncio.create_task(pjsip.request_pjsip_reload(execute=True))
    for _ in range(4):
        await asyncio.sleep(0)
    commands_before_first_finished = list(spawned)
    finish_first.set()
    results = await asyncio.gather(first, second)
    assert commands_before_first_finished == [0]
    assert [result.accepted for result in results] == [False, True]


def test_pending_intent_is_not_an_accepted_reload_receipt():
    assert not pjsip.PJSIPReloadResult("coalesced", "Another request is pending").accepted


@pytest.mark.asyncio
async def test_cancelling_waiting_caller_never_spawns_or_accepts(monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()
    spawned = []

    async def debounce(_):
        started.set()
        await finish.wait()

    async def spawn(*args, **kwargs):
        spawned.append(args)

        async def communicate():
            return b"Reload queued", b""

        return SimpleNamespace(returncode=0, communicate=communicate)

    install_ports(monkeypatch, debounce, spawn)
    first = asyncio.create_task(pjsip.request_pjsip_reload(execute=True))
    await started.wait()
    second = asyncio.create_task(pjsip.request_pjsip_reload(execute=True))
    await asyncio.sleep(0)
    second.cancel()
    finish.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert results[0].accepted
    assert isinstance(results[1], asyncio.CancelledError)
    assert len(spawned) == 1


@pytest.mark.asyncio
async def test_cancelled_command_is_reaped_before_next_reload_even_if_cancelled_again(monkeypatch):
    started, reap = asyncio.Event(), asyncio.Event()
    processes = []

    async def debounce(_):
        await asyncio.sleep(0)

    class Process:
        def __init__(self, number):
            self.number = number
            self.returncode = None
            self.killed = False
            self.reaped = False

        def kill(self):
            self.killed = True

        async def wait(self):
            return self.returncode

        async def communicate(self):
            if self.number == 0:
                started.set()
                await reap.wait()
            self.returncode = -9 if self.killed else 0
            self.reaped = True
            return b"", b""

    async def spawn(*args, **kwargs):
        process = Process(len(processes))
        processes.append(process)
        return process

    install_ports(monkeypatch, debounce, spawn)
    first = asyncio.create_task(pjsip.request_pjsip_reload(execute=True))
    await started.wait()
    first.cancel()
    for _ in range(4):
        await asyncio.sleep(0)
    first.cancel()
    second = asyncio.create_task(pjsip.request_pjsip_reload(execute=True))
    for _ in range(4):
        await asyncio.sleep(0)
    count_before_reap = len(processes)
    reap.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert count_before_reap == 1
    assert processes[0].killed and processes[0].reaped
    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1].accepted


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["pipe_error", "kill_error"])
@pytest.mark.parametrize("cancel_again", [False, True])
async def test_cancel_preserves_cancellation_and_ownership_when_cleanup_has_error(monkeypatch, fault, cancel_again):
    started, finish = asyncio.Event(), asyncio.Event()
    processes = []

    async def debounce(_):
        await asyncio.sleep(0)

    class Process:
        returncode = None
        reaped = False

        def __init__(self, number):
            self.number = number

        def kill(self):
            if fault == "kill_error":
                raise PermissionError("synthetic kill denied")

        async def communicate(self):
            if self.number == 0:
                started.set()
                await finish.wait()
                if fault == "pipe_error":
                    raise OSError("synthetic pipe failure")
            self.returncode = 0
            return b"", b""

        async def wait(self):
            await finish.wait()
            self.returncode = 0
            self.reaped = True
            return 0

    async def spawn(*args, **kwargs):
        process = Process(len(processes))
        processes.append(process)
        return process

    install_ports(monkeypatch, debounce, spawn)
    first = asyncio.create_task(pjsip.request_pjsip_reload(execute=True))
    await started.wait()
    first.cancel()
    if cancel_again:
        for _ in range(4):
            await asyncio.sleep(0)
        first.cancel()
    second = asyncio.create_task(pjsip.request_pjsip_reload(execute=True))
    for _ in range(8):
        await asyncio.sleep(0)
    count_before_finish = len(processes)
    finish.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert count_before_finish == 1
    assert processes[0].reaped
    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1].accepted


@pytest.mark.asyncio
async def test_timed_out_command_has_no_success_receipt_and_owned_child_is_reaped(monkeypatch):
    finish = asyncio.Event()
    monkeypatch.setattr(pjsip, "_RELOAD_TIMEOUT_S", 0.01, raising=False)

    async def debounce(_):
        await asyncio.sleep(0)

    class Process:
        returncode = None
        killed = False
        reaped = False

        def kill(self):
            self.killed = True
            finish.set()

        async def wait(self):
            return self.returncode

        async def communicate(self):
            await finish.wait()
            self.returncode = -9
            self.reaped = True
            return b"", b""

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    install_ports(monkeypatch, debounce, spawn)
    try:
        result = await asyncio.wait_for(pjsip.request_pjsip_reload(execute=True), 0.2)
    finally:
        finish.set()
    assert not result.accepted
    assert result.status == "failed"
    assert process.killed and process.reaped


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "timeout"])
async def test_owned_local_subprocess_is_reaped_without_running_asterisk(monkeypatch, stop):
    started = asyncio.Event()
    processes = []
    monkeypatch.setattr(pjsip, "_RELOAD_TIMEOUT_S", 0.03 if stop == "timeout" else 10.0)

    async def debounce(_):
        await asyncio.sleep(0)

    async def spawn(*args, **kwargs):
        assert args == ("asterisk", "-rx", "pjsip reload")
        # Only this harmless local child runs: no Asterisk, network or config IO.
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            'import time; print("ready", flush=True); time.sleep(30)',
            **kwargs,
        )
        processes.append(process)
        assert (await process.stdout.readline()).strip() == b"ready"
        started.set()
        return process

    install_ports(monkeypatch, debounce, spawn)
    request = asyncio.create_task(pjsip.request_pjsip_reload(execute=True))
    try:
        await asyncio.wait_for(started.wait(), 5)
        if stop == "cancel":
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(request, 5)
        else:
            result = await asyncio.wait_for(request, 5)
            assert result.status == "failed" and not result.accepted
        assert len(processes) == 1
        assert processes[0].returncode is not None
    finally:
        request.cancel()
        for process in processes:
            if process.returncode is None:
                process.kill()
            await process.wait()
        await asyncio.gather(request, return_exceptions=True)
