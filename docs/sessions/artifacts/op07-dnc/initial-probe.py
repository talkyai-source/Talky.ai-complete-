"""OP07 actual native DNC path and shared partial-purge proof; synthetic ports only."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pytest import MonkeyPatch

from tests.unit.test_realtime_end_call_ownership import _fixture, _events
from app.realtime.openai import RealtimeEvent
from app.domain.services.dialer import opt_out


async def main():
    mp=MonkeyPatch()
    dnc_write=AsyncMock(return_value={"id":"synthetic-dnc"})
    mp.setattr(opt_out.DNCService,'add_caller_opt_out',dnc_write)
    attempts=[]
    class FailingQuery:
        def __init__(self,name): self.name=name
        def update(self,*args,**kwargs): return self
        def eq(self,*args,**kwargs): return self
        def in_(self,*args,**kwargs): return self
        def execute(self):
            attempts.append(self.name)
            raise RuntimeError('synthetic follow-up DB write unavailable')
    client=SimpleNamespace(table=FailingQuery)
    voice=SimpleNamespace(_dialer_tenant_id='20000000-0000-4000-8000-000000000017',
                          _dialer_lead_id='30000000-0000-4000-8000-000000000017',
                          _dialer_phone='+15555550107',_dialer_call_id='10000000-0000-4000-8000-000000000017')
    state=SimpleNamespace(get_voice_session=lambda call_id:voice)
    mp.setattr('app.domain.services.telephony.lifecycle._state',lambda:state)
    mp.setattr('app.core.container.get_container',lambda:SimpleNamespace(is_initialized=True,db_pool=object(),db_client=client))
    bridge,provider,gateway,ended,session=_fixture()
    session.call_id='synthetic-close'
    voice.call_session=session
    try:
        await _events(bridge,provider,RealtimeEvent(kind='caller_transcript',is_final=True,
            text='Do not call me again, but first I need help with my account.'))
        continued={
            'opt_out_flag':bool(getattr(session,'_caller_opted_out',False)),
            'durable_dnc_write_attempts':dnc_write.await_count,
            'hangup_attempts':ended.await_count,
            'termination_task_armed':bridge._termination_task is not None,
        }
        first=await opt_out.purge_opt_out_before_farewell(session)
        writes_after_first=dnc_write.await_count
        cleanups_after_first=list(attempts)
        second=await opt_out.purge_opt_out_before_farewell(session)
        result={
            'native_opt_out_with_continued_question':continued,
            'shared_partial_purge':{
                'dnc_receipt_success':first,'cleanup_attempts_failed':cleanups_after_first,
                'full_purge_marker':bool(getattr(voice,'_opt_out_purged',False)),
                'second_call_success':second,'second_dnc_attempt_delta':dnc_write.await_count-writes_after_first,
                'second_cleanup_attempt_delta':len(attempts)-len(cleanups_after_first),
            },
            'limits':'Actual bridge event pump and shared purge helper; synthetic DNC/client/state only. No DB/network/provider calls. DNC success does block future contact; the marker defect skips ancillary cleanup retry.',
        }
        print(json.dumps(result,indent=2))
    finally:
        await bridge.stop()
        mp.undo()

asyncio.run(main())
