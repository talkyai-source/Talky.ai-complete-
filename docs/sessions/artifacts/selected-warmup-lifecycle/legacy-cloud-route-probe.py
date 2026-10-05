import asyncio
from contextlib import ExitStack, asynccontextmanager
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.api.v1.endpoints import twilio_bridge as tw, vonage_bridge as vo
from app.domain.models.ai_config import AIProviderConfig
from app.domain.services.telephony import inbound_router
from app.domain.services.tenant_ai_config_resolver import TenantAIConfigResolver
from app.domain.services.voice_tuning import VoiceTuningResolver

TENANT = 'c4442497-fbc8-4675-8424-995456831b39'
BINDING = dict(tenant_id=TENANT, campaign_id='b16e4330-63a8-4ef9-854f-0056837f3f00', sip_trunk_id='083493f5-752b-4d32-8ed7-a9aad8b2861e', inbound_campaign_id='c99ff902-d9eb-4c22-94f9-8e9ff8286d93', config_id='b9534053-c71f-489d-bf6e-8c9b49645b04', called_did_id='56114eb3-d47b-42b9-afaa-3c952d8e3d6b', route_version=1, config_version=1)
DID = '+12025550123'

class Socket:
    def __init__(self):
        self.headers = {}
        self.accepts = 0
        self.closes = []
        self.frames = iter([json.dumps({'event':'start','start':{'streamSid':'MZsynthetic','callSid':'CAsynthetic'}}), json.dumps({'event':'stop'})])
    async def accept(self): self.accepts += 1
    async def close(self, **kwargs): self.closes.append(kwargs)
    async def receive_text(self): return next(self.frames)
    async def receive(self): return {'type':'websocket.disconnect'}

async def run_case(provider, mode):
    module = tw if provider == 'twilio' else vo
    captured = []
    route_results = []
    saved = AIProviderConfig(pipeline_mode='realtime', realtime_voice='ash')
    ai, tuning = TenantAIConfigResolver(), VoiceTuningResolver()
    ai_lookup = AsyncMock(return_value=saved)
    tuning_lookup = AsyncMock(return_value={'stt_eot_timeout_ms':1800})
    ai.set_db_lookup(ai_lookup)
    tuning.set_db_lookup(tuning_lookup)
    conn = NS(fetch=AsyncMock(return_value=[BINDING]))
    if mode == 'query_failure': conn.fetch.side_effect = RuntimeError('synthetic route dependency unavailable')
    elif mode == 'no_binding': conn.fetch.return_value = []
    elif mode == 'ambiguous': conn.fetch.return_value = [BINDING, BINDING]
    pool = None if mode == 'pool_missing' else object()
    @asynccontextmanager
    async def acquire(*args, **kwargs): yield conn
    actual_route = inbound_router.resolve_inbound_route
    async def recording_route(*args, **kwargs):
        result = await actual_route(*args, **kwargs)
        route_results.append({'resolved':result.resolved, 'rejected':result.rejected, 'reason':result.reason})
        return result
    async def create(config):
        captured.append({'tenant_id':config.tenant_id, 'pipeline_mode':config.pipeline_mode, 'realtime_voice':config.realtime_voice, 'llm_provider':config.llm_provider_type, 'llm_model':config.llm_model, 'stt_eot_timeout_ms':config.stt_eot_timeout_ms})
        return NS(call_id='synthetic-voice-session', media_gateway=NS(set_stream_sid=Mock()))
    orchestrator = NS(create_voice_session=AsyncMock(side_effect=create), start_pipeline=AsyncMock(), end_session=AsyncMock())
    ws = Socket()
    with ExitStack() as stack:
        stack.enter_context(patch.dict('os.environ', {provider.upper()+'_BRIDGE_ENABLED':'false' if mode == 'disabled' else 'true', provider.upper()+'_WS_TOKEN_SECRET':'synthetic-offline-only-token-secret'}))
        stack.enter_context(patch('app.core.container.get_container', return_value=NS(db_pool=pool)))
        stack.enter_context(patch('app.core.db_utils.acquire_with_tenant', acquire))
        stack.enter_context(patch.object(inbound_router, 'resolve_inbound_route', recording_route))
        stack.enter_context(patch('app.domain.services.tenant_ai_config_resolver.get_tenant_ai_config_resolver', return_value=ai))
        stack.enter_context(patch('app.domain.services.voice_tuning.get_voice_tuning_resolver', return_value=tuning))
        stack.enter_context(patch.object(module, '_get_orchestrator', return_value=orchestrator))
        stack.enter_context(patch.object(module, '_get_semaphore', return_value=asyncio.Semaphore(1)))
        if provider == 'twilio':
            token = module._mint_ws_token(to_number=DID, call_sid='CAsynthetic')
            assert module._verify_ws_token(token)
            await module.twilio_media_stream(ws, token=token)
        else:
            token = module._mint_ws_token(to_number=DID, call_uuid='synthetic-vonage-call')
            assert module._verify_ws_token(token)
            await module.vonage_ws_audio(ws, 'synthetic-vonage-call', token=token)
    return {'provider':provider, 'route_case':mode, 'enabled':mode != 'disabled', 'actual_token_verified':True, 'socket_accept_count':ws.accepts, 'socket_closes':ws.closes, 'route_results':route_results, 'route_query_count':conn.fetch.await_count, 'tenant_ai_lookup_count':ai_lookup.await_count, 'tenant_tuning_lookup_count':tuning_lookup.await_count, 'factory_count':orchestrator.create_voice_session.await_count, 'pipeline_start_count':orchestrator.start_pipeline.await_count, 'factory_configs':captured}

async def main():
    def blocked(*args, **kwargs): raise AssertionError('No network allowed in offline route probe')
    with patch.object(socket.socket,'connect',blocked), patch.object(socket.socket,'connect_ex',blocked), patch('socket.getaddrinfo',blocked):
        rows = [await run_case(provider, mode) for provider in ('twilio','vonage') for mode in ('resolved_saved','query_failure','pool_missing','no_binding','ambiguous','disabled')]
    print(json.dumps({'scope':'Actual enabled WebSocket handlers, real token mint/verify, route decision/query method, real profile resolvers/builders. Fake socket, DB acquire/fetch, session factory and pipeline. Socket/DNS blocked; no external providers or real DB. Signed token represents existing /answer-minted credential, not provider webhook signature exercise.','results':rows}, indent=2))
asyncio.run(main())
