"""Offline baseline. Actual admission methods; synthetic authenticated/DB/queue/PBX ports."""
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from contextlib import asynccontextmanager

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

async def main():
    import pytest
    def blocked(*args, **kwargs):
        raise AssertionError("Network operation forbidden in offline baseline")
    socket.socket.connect = blocked
    socket.getaddrinfo = blocked
    os.environ["ENVIRONMENT"] = "production"
    os.environ["TELEPHONY_ADAPTER"] = "asterisk"
    os.environ["TWILIO_BRIDGE_ENABLED"] = "true"
    os.environ["VONAGE_BRIDGE_ENABLED"] = "true"
    from app.api.v1.endpoints import telephony_providers as providers
    from app.api.v1.endpoints import twilio_bridge, vonage_bridge
    from app.core import db_utils
    from app.domain.services.telephony import trunk_resolver
    from app.domain.services.campaign_service import CampaignService
    from tests.unit import test_campaign_start_dispatch_truth as starts
    from tests.unit import test_outbound_campaign_boundaries as calls
    from tests.unit import test_call_redial_service as redials

    class Context:
        def __init__(self, value=None): self.value = value
        async def __aenter__(self): return self.value
        async def __aexit__(self, *args): return False
    class Conn:
        def __init__(self, provider, credential_status="active"):
            self.provider = provider
            self.credential_status = credential_status
            self.pointer_writes = 0
            self.queries = []
        def transaction(self): return Context()
        async def execute(self, sql, *args):
            if "UPDATE tenants" in sql:
                self.pointer_writes += 1
            return "UPDATE 1"
        async def fetchrow(self, sql, *args):
            self.queries.append(sql)
            if "tenant_telephony_credentials" in sql:
                return {"status": self.credential_status}
            if "active_telephony_provider" in sql:
                return {"active_telephony_provider": self.provider, "pool_trunk": None, "has_trunks": True}
            raise AssertionError(sql)
    class Pool:
        def __init__(self, conn): self.conn=conn
        def acquire(self): return Context(self.conn)

    results = []
    for provider in ("twilio", "vonage"):
        conn = Conn(provider)
        result = await providers.activate_provider(
            providers.ProviderActivateRequest(provider=provider),
            SimpleNamespace(tenant_id=calls.TENANT_ID), Pool(conn),
        )
        results.append({"case":"production_activation", "provider":provider,
            "bridge_enabled": (twilio_bridge if provider=="twilio" else vonage_bridge)._bridge_enabled(),
            "response":result,"pointer_writes":conn.pointer_writes,
            "expected_rejected_without_pointer_change": conn.pointer_writes == 0})

        db, queue = starts._DB(), starts._Queue([True])
        campaign = db.tables["campaigns"][0]
        campaign.update(id=calls.CAMPAIGN_ID,tenant_id=calls.TENANT_ID)
        db.tables["leads"][0].update(id=calls.LEAD_ID,campaign_id=calls.CAMPAIGN_ID,tenant_id=calls.TENANT_ID)
        db.pool = Pool(Conn(provider))
        result = await CampaignService(db,queue_service=queue).start_campaign(calls.CAMPAIGN_ID,tenant_id=calls.TENANT_ID)
        results.append({"case":"production_campaign_start", "provider":provider,
            "success":result.success,"jobs_enqueued":len(queue.enqueued),
            "expected_no_dispatch":len(queue.enqueued)==0})

        with pytest.MonkeyPatch.context() as mp:
            actual_requires = trunk_resolver.requires_sip_readiness
            h = redials.harness.__wrapped__(mp)
            mp.setattr(trunk_resolver,"requires_sip_readiness",actual_requires)
            @asynccontextmanager
            async def scope(*args, **kwargs): yield Conn(provider)
            mp.setattr(db_utils,"acquire_with_tenant",scope)
            preview = await redials.preview(h)
            result = await redials.request(h)
            results.append({"case":"production_manual_redial","provider":provider,
                "preview_eligible":preview["eligible"],"receipt_status":result["status"],
                "queue_submissions":h.queue.schedule_job_once.await_count,
                "expected_no_dispatch":h.queue.schedule_job_once.await_count==0})

        with pytest.MonkeyPatch.context() as mp:
            h = calls._install_call_path(mp,campaign_rows=[calls._campaign(),calls._campaign()])
            fetchrow = h.conn.fetchrow
            provider_reads = []
            async def row(sql,*args):
                if "active_telephony_provider" in sql:
                    provider_reads.append(True)
                    return {"active_telephony_provider":provider,"pool_trunk":None,"has_trunks":True}
                return await fetchrow(sql,*args)
            mp.setattr(h.conn,"fetchrow",row)
            result = await calls.telephony_bridge.make_call(h.request,h.body)
            results.append({"case":"production_existing_durable_intent_final_call","provider":provider,
                "adapter":h.adapter.name,"originate_count":h.events.count("originate"),
                "selection_reads":len(provider_reads),
                "expected_no_sip_origination":h.events.count("originate")==0})

    with pytest.MonkeyPatch.context() as mp:
        h=calls._install_call_path(mp,campaign_rows=[calls._campaign()],internal=False,durable_intent=False)
        caught=await calls._assert_call_error(h,403)
        results.append({"case":"existing_direct_user_call_guard","status":caught.status_code,
            "originate_count":h.events.count("originate")})
    output={"scope":"offline actual methods; synthetic DB/queue/provider ports; no credentials, external network, real calls or production changes",
        "source_head":"add699c4f7bf1fbf603f078e862f7229a04406c8", "cases":results}
    (ROOT/"tmp/cloud-availability-before.json").write_text(json.dumps(output,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(output,indent=2))
    failures = [r for r in results if any(k.startswith("expected_") and value is False for k, value in r.items())]
    assert not failures, f"{len(failures)} expected cloud-availability admission controls failed"

asyncio.run(main())
