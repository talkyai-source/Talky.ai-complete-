import asyncio,json,socket,sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.domain.models.ai_config import AIProviderConfig
from app.domain.services.tenant_ai_config_resolver import TenantAIConfigResolver
from app.domain.services.voice_tuning import VoiceTuningResolver
from app.domain.services.telephony import prewarm
from app.api.v1.endpoints import twilio_bridge,vonage_bridge

def summary(config):
 return {'pipeline_mode':config.pipeline_mode,'llm_provider':config.llm_provider_type,'llm_model':config.llm_model,'realtime_voice':config.realtime_voice,'stt_eot_timeout_ms':config.stt_eot_timeout_ms,'tenant_id':config.tenant_id}
async def main():
 rows=[]
 async def denied(*args,**kwargs): raise RuntimeError('synthetic lookup unavailable')
 saved=AIProviderConfig(pipeline_mode='realtime',realtime_voice='ash')
 for failure in ('none','none_cascaded','ai','tuning','unwired_ai','unwired_tuning','no_row'):
  ai=TenantAIConfigResolver();tuning=VoiceTuningResolver();captured=[]
  chosen = saved.model_copy(update={'pipeline_mode':'cascaded'}) if failure in ('none_cascaded','tuning','unwired_tuning') else saved
  if failure!='unwired_ai': ai.set_db_lookup(AsyncMock(side_effect=RuntimeError('synthetic AI lookup unavailable')) if failure=='ai' else AsyncMock(return_value=None if failure=='no_row' else chosen))
  if failure!='unwired_tuning': tuning.set_db_lookup(AsyncMock(side_effect=RuntimeError('synthetic tuning lookup unavailable')) if failure=='tuning' else AsyncMock(return_value=None if failure=='no_row' else {'stt_eot_timeout_ms':1800}))
  async def create(config):
   captured.append(summary(config))
   return NS(call_id='synthetic-config-call',call_session=NS(call_id='synthetic-config-call'),realtime_bridge=object() if config.pipeline_mode=='realtime' else None,stt_provider=NS(),tts_provider=NS(),llm_provider=NS())
  with ExitStack() as stack:
   stack.enter_context(patch('app.domain.services.tenant_ai_config_resolver.get_tenant_ai_config_resolver',return_value=ai))
   stack.enter_context(patch('app.domain.services.voice_tuning.get_voice_tuning_resolver',return_value=tuning))
   stack.enter_context(patch.object(prewarm,'_get_orchestrator',return_value=NS(create_voice_session=create,end_session=AsyncMock())))
   for name in ('warm_tts_inference_path','warm_llm_stream','prepare_pre_originate_greeting','_resolve_session_accent','_attach_llm_opener'):
    stack.enter_context(patch.object(prewarm,name,AsyncMock()))
   stack.enter_context(patch.object(prewarm,'_start_opening_ladder_generation'))
   stack.enter_context(patch('app.services.scripts.knowledge.session_inject.apply_campaign_knowledge',AsyncMock()))
   result=await prewarm.prepare_prewarmed_session(first_speaker='agent',campaign_id='b16e4330-63a8-4ef9-854f-0056837f3f00',agent_name='Ava',container=NS(db_client=None),campaign_row={'id':'b16e4330-63a8-4ef9-854f-0056837f3f00','tenant_id':'c4442497-fbc8-4675-8424-995456831b39','script_config':{'company_name':'Synthetic Company'}})
   await asyncio.sleep(0)
  rows.append({'boundary':'actual_prepare_prewarmed_session','lookup':failure,'ready':result.session is not None,'failure':result.failure_reason,'factory_config':captured})
 for name,builder in [('twilio',twilio_bridge._build_twilio_session_config),('vonage',vonage_bridge._build_vonage_session_config)]:
  for failure in ('none','ai','tuning'):
   ai=TenantAIConfigResolver();tuning=VoiceTuningResolver()
   ai.set_db_lookup(AsyncMock(side_effect=RuntimeError('synthetic AI lookup unavailable')) if failure=='ai' else AsyncMock(return_value=saved))
   tuning.set_db_lookup(AsyncMock(side_effect=RuntimeError('synthetic tuning lookup unavailable')) if failure=='tuning' else AsyncMock(return_value={'stt_eot_timeout_ms':1800}))
   with patch('app.core.container.get_container',return_value=NS(db_pool=object())),patch('app.domain.services.telephony.inbound_router.resolve_inbound_route',AsyncMock(return_value=NS(resolved=True,tenant_id='c4442497-fbc8-4675-8424-995456831b39'))),patch('app.domain.services.tenant_ai_config_resolver.get_tenant_ai_config_resolver',return_value=ai),patch('app.domain.services.voice_tuning.get_voice_tuning_resolver',return_value=tuning):
    try: config=await builder('+12025550123');row={'config_returned':True,'config':summary(config)}
    except Exception as e: row={'config_returned':False,'exception_type':type(e).__name__}
   rows.append({'boundary':'actual_'+name+'_session_builder','lookup':failure,**row})
 print(json.dumps({'scope':'Actual resolver/prewarm/session builders; fake lookup, provider factory/warmup and DID route ports, no DB or provider calls','results':rows},indent=2))

async def offline():
 def blocked(*args,**kwargs): raise AssertionError('network forbidden')
 with patch.object(socket.socket,'connect',blocked),patch.object(socket.socket,'connect_ex',blocked),patch('socket.getaddrinfo',blocked): await main()
asyncio.run(offline())
