import asyncio
from pathlib import Path
import json
import socket
import sys
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.api.v1.endpoints import telephony_bridge as bridge
from app.domain.services.telephony import lifecycle
from app.domain.services.telephony.state_backend import LocalOnlyStateBackend
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter
from tests.unit.test_outbound_campaign_boundaries import _install_call_path, _campaign

class AdmissionObserved(BaseException): pass

async def run_case(age):
    baseline_tasks = set(asyncio.all_tasks())
    with pytest.MonkeyPatch.context() as m:
        path = _install_call_path(m, campaign_rows=[_campaign(), _campaign()])
        m.setenv('DIALER_RING_TIMEOUT_S', '300')
        m.setenv('POD_ID', 'offline-probe')
        for name in ('_telephony_sessions', '_ringing_warmups', '_ringing_warmup_created_at', '_ringing_events', '_gateway_session_to_call_id', '_early_audio_buffers', '_gateway_audio_last_sequence'):
            m.setattr(bridge, name, {})
        state = LocalOnlyStateBackend()
        m.setattr(bridge, 'get_state_backend', lambda:state)
        m.setattr(lifecycle, '_state', lambda:state)
        adapter = AsteriskAdapter()
        adapter._connected_flag = True
        requests = []
        async def ari(method, endpoint, **kwargs):
            assert method == 'POST' and endpoint == '/channels'
            requests.append({'method':method, 'endpoint':endpoint, 'timeout':kwargs['params']['timeout'], 'id':kwargs['params']['channelId']})
            assert state.get_ringing_warmup(kwargs['params']['channelId'])[0] is path.session
            return {'id':kwargs['params']['channelId']}
        m.setattr(adapter, '_ari', ari)
        m.setattr(bridge, '_adapter', adapter)
        m.setattr(lifecycle, 'get_adapter', lambda:adapter)
        path.session.config = NS(pipeline_mode='realtime', realtime_voice='ash', tenant_id=str(path.body.tenant_id))
        m.setattr('app.domain.services.call_status.record_call_state_by_provider_id', AsyncMock())
        response = await bridge.make_call(path.request, path.body)
        data = json.loads(response.body)
        assert data['status'] == 'calling'
        cid = data['call_id']
        assert state.get_ringing_warmup(cid)[0] is path.session
        # The adapter's real30s bookkeeping task does not simulate wall-clock advance.
        for task in set(asyncio.all_tasks()) - baseline_tasks:
            task.cancel()
        await asyncio.gather(*(set(asyncio.all_tasks()) - baseline_tasks), return_exceptions=True)
        state.set_ringing_started_at(cid, asyncio.get_running_loop().time() - age)
        ended = []
        factory = []
        async def end(session): ended.append(session is path.session)
        async def create(config):
            factory.append({'pipeline_mode':config.pipeline_mode, 'voice':config.realtime_voice, 'tenant_id':config.tenant_id, 'llm_model':config.llm_model})
            return NS(config=config, call_session=NS())
        orchestrator = NS(end_session=AsyncMock(side_effect=end), create_voice_session=AsyncMock(side_effect=create), _active_sessions={})
        m.setattr(lifecycle, '_get_orchestrator', lambda:orchestrator)
        m.setattr(adapter, 'list_active_channel_ids', AsyncMock(return_value={cid}))
        m.setattr(adapter, 'hangup', AsyncMock())
        m.setattr(adapter, 'hangup_confirmed', AsyncMock(return_value=True))
        m.setattr(lifecycle, 'recover_orphaned_calls', AsyncMock())
        # Run exactly one real watchdog sweep, ending at its next scheduled sleep.
        sleeps = 0
        async def one_tick(_seconds):
            nonlocal sleeps
            sleeps += 1
            if sleeps > 1: raise asyncio.CancelledError()
        with pytest.MonkeyPatch.context() as tick:
            tick.setattr(lifecycle.asyncio, 'sleep', one_tick)
            await lifecycle._session_watchdog()
        warmup_remaining = state.has_ringing_warmup(cid)
        admitted = []
        real_set = state.set_voice_session
        def observe(call_id, session, **kwargs):
            real_set(call_id, session, **kwargs)
            admitted.append({'original_saved_session':session is path.session, 'pipeline_mode':session.config.pipeline_mode, 'voice':session.config.realtime_voice, 'tenant_id':session.config.tenant_id})
            raise AdmissionObserved()
        m.setattr(state, 'set_voice_session', observe)
        m.setattr('app.domain.services.global_concurrency.acquire_lease', AsyncMock(return_value=True))
        try: await lifecycle._on_new_call(cid)
        except AdmissionObserved: pass
        assert admitted
        return {'simulated_ring_age_seconds':age, 'configured_and_submitted_ari_timeout_seconds':requests[0]['timeout'], 'endpoint_status':response.status_code, 'prewarmed_saved_session_published_before_ari':True, 'watchdog_saw_provider_channel_live':adapter.list_active_channel_ids.await_count == 1, 'watchdog_ended_saved_session':ended, 'warmup_retained_after_sweep':warmup_remaining, 'provider_hangup_requests':adapter.hangup.await_count + adapter.hangup_confirmed.await_count, 'answer_factory_configs':factory, 'answer_local_registration':admitted}

async def main():
    def blocked(*args, **kwargs): raise AssertionError('network forbidden')
    with pytest.MonkeyPatch.context() as m:
        m.setattr(socket.socket, 'connect', blocked)
        m.setattr(socket.socket, 'connect_ex', blocked)
        m.setattr(socket, 'getaddrinfo', blocked)
        rows = [await run_case(age) for age in (10, 181)]
    print(json.dumps({'scope':'Actual internal durable make_call endpoint, Asterisk originate method, local state, one watchdog iteration, on-answer config selection through local registration. Prewarm success/selected session, DB/guard ports, ARI transport, global lease and provider factory are synthetic. Age setup changes only local monotonic started_at; no real waiting/calls/DB/network. Stops at registration before pipeline/audio. Default30s production ring not claimed affected; configured300s demonstrates permitted timing.', 'results':rows}, indent=2))

asyncio.run(main())
