"""Read-only application probe: synthetic DB/ARI ports; no live effects."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import telephony_bridge
from app.api.v1.endpoints.admin import calls
from app.domain.services.telephony import termination
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter
from scripts.evaluate_ag04_conversations import offline_network_guard


class Conn:
    def __init__(self, provider):
        self.row = dict(id="00000000-0000-4000-8000-000000000001",
            tenant_id=UUID("00000000-0000-4000-8000-000000000002"),
            external_call_uuid="synthetic-original-provider-leg",
            provider_call_id="synthetic-original-provider-leg", provider=provider,
            direction="outbound", status="in_call", campaign_id=None,
            answered_at=datetime(2026, 10, 5, tzinfo=timezone.utc))
        self.writes=[]

    async def fetchrow(self, sql, *args):
        if "SELECT id, tenant_id, external_call_uuid" in sql:
            return dict(self.row)
        if "SELECT id::text AS call_id" in sql:
            return {**self.row, "call_id": self.row["id"]}
        if "UPDATE calls" in sql:
            self.writes.append("terminal_update")
            self.row["status"]="ended"
            return dict(status="ended", outcome="agent_hung_up", duration_seconds=12)
        raise AssertionError(sql)

    async def fetch(self, sql, *args):
        assert "FROM call_legs" in sql
        return []

    async def execute(self, sql, *args):
        assert "SET status='termination_pending'" in sql
        self.row["status"]="termination_pending"
        self.writes.append("termination_pending")
        return "UPDATE 1"


class Audit:
    def __init__(self): self.events=[]
    async def log(self, **kwargs): self.events.append(kwargs["action"])


async def probe(provider):
    conn, audit, ari_requests = Conn(provider), Audit(), []
    adapter=AsteriskAdapter()
    @asynccontextmanager
    async def acquire(*_args, **_kwargs): yield conn
    async def ari(method,path,**_kwargs):
        ari_requests.append({"method":method,"path":path,"status":404})
        return 404, {}
    adapter._ari=ari
    with patch.object(calls,"acquire_with_tenant",acquire), patch.object(termination,"acquire_with_tenant",acquire), patch.object(telephony_bridge,"_adapter",adapter):
        result=await calls.terminate_call(conn.row["id"],
            admin_user=CurrentUser(id="00000000-0000-4000-8000-000000000003",email="synthetic@example.com",role="platform_admin"),
            db_client=SimpleNamespace(pool=object()),audit_logger=audit)
    return {"saved_provider":provider,"adapter":adapter.name,"ari_requests":ari_requests,"db_writes":conn.writes,"saved_status_after":conn.row["status"],"result":result,"audit_actions":audit.events}


async def main():
    attempts=[]
    with offline_network_guard(attempts):
        rows=[await probe(provider) for provider in ("asterisk","twilio","vonage",None,"unknown")]
    report={"source_candidate":"bbbb9450dfbff9e0436493a3b7b870a64481b77a","scope":"Actual Admin terminate endpoint, durable-context helper, shared HangupProof helper and Asterisk confirmation adapter; synthetic in-memory SQL/ARI/audit ports. Direct method invocation does not qualify HTTP auth, real database or actual provider termination.","blocked_network_attempts":attempts,"rows":rows}
    out=Path.cwd().parent/'tmp/op05-provider-termination-before.json'
    out.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__ == '__main__': asyncio.run(main())
