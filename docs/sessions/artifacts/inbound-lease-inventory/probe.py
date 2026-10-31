"""Synthetic persisted-child inventory fault at actual lease-loss teardown."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from app.domain.services.telephony import lifecycle, termination
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter
from scripts.evaluate_ag04_conversations import offline_network_guard

async def case(db_available):
    calls=[]
    parent='synthetic-parent'
    child='synthetic-persisted-child'
    class Conn:
        async def fetchrow(self, *args):
            return dict(call_id='00000000-0000-4000-8000-000000000001',tenant_id='00000000-0000-4000-8000-000000000002',provider_call_id=parent,status='in_progress',provider='asterisk',direction='inbound',campaign_id=None,answered_at=None)
        async def fetch(self,*args):
            return [{'provider_leg_id':child,'provider':'asterisk'}]
        async def execute(self,*args): return 'UPDATE 1'
    @asynccontextmanager
    async def acquire(*args,**kwargs):
        if not db_available:
            raise ConnectionError('synthetic storage unavailable')
        yield Conn()
    adapter=AsteriskAdapter()
    async def ari(method,path,**kwargs):
        calls.append([method,path])
        return 404,{}
    adapter._ari=ari
    finalizer=AsyncMock(return_value=True)
    state=SimpleNamespace(register_cleanup_obligation=AsyncMock())
    with patch.object(termination,'acquire_with_tenant',acquire),patch.object(lifecycle,'get_adapter',lambda:adapter),patch.object(lifecycle,'_state',lambda:state),patch.object(lifecycle,'_on_call_ended',finalizer):
        result=await lifecycle._fence_inbound_call_after_lease_loss(SimpleNamespace(db_pool=object()),pbx_call_id=parent,durable_call_id='00000000-0000-4000-8000-000000000001',admission={'provider':'asterisk','provider_call_id':parent,'tenant_id':'00000000-0000-4000-8000-000000000002'})
    return {'db_available':db_available,'synthetic_persisted_child':child,'adapter_local_transfer_mapping':'empty','ari_requests':calls,'logical_finalizer_calls':finalizer.await_count,'completed':result,'child_received_proof_request':any(child in path for _,path in calls)}
async def main():
    attempts=[]
    with offline_network_guard(attempts):
        rows=[await case(True),await case(False)]
    result={'source':'c9d3f87e6dd7c9011008f886419e17888a1a6825','scope':'actual lease-loss helper, actual force helper and Asterisk DELETE404 proof; synthetic persisted SQL/DB outage/ARI ports and logical finalizer. No actual DB, hidden live channel, carrier call, or settlement effect. Empty adapter transfer mapping is explicit.','blocked_network_attempts':attempts,'rows':rows}
    out=Path(__file__).with_suffix('.json');out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
asyncio.run(main())
