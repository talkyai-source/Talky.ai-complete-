"""Copy an owned outbound campaign's configuration without operational activity."""
from uuid import UUID, uuid4

from app.core.db_utils import acquire_with_tenant
from app.domain.services.audit_logger import AuditEvent
from app.domain.services.campaign_direction_guard import acquire_campaign_direction_lock


class CampaignDuplicationRefused(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


async def duplicate_campaign_snapshot(pool, *, tenant_id: str, campaign_id: str,
                                      actor_id: str, actor_role: str, audit):
    tenant, original = UUID(str(tenant_id)), UUID(str(campaign_id))
    async with acquire_with_tenant(pool, str(tenant)) as conn:
        # The audit chain owns the first lock. Knowledge publication/removal
        # uses the same direction lock; campaign/lead-field writers also update
        # the campaign row. Keep those changes outside this copy's snapshot.
        await audit.lock_transaction(conn)
        await acquire_campaign_direction_lock(conn, str(original))
        source = await conn.fetchrow(
            "SELECT * FROM campaigns WHERE id=$1 AND tenant_id=$2 "
            "AND status <> 'deleted' FOR UPDATE",
            original, tenant,
        )
        if source is None:
            raise CampaignDuplicationRefused(404, "Campaign not found")
        if source["direction"] != "outbound":
            raise CampaignDuplicationRefused(409, "Inbound campaigns must use their managed routing lifecycle.")

        sources = await conn.fetch(
            "SELECT id,status FROM campaign_knowledge_sources "
            "WHERE campaign_id=$1 AND tenant_id=$2 ORDER BY id FOR SHARE",
            original, tenant,
        )
        if any(row["status"] != "ready" for row in sources):
            raise CampaignDuplicationRefused(409, "Campaign knowledge must be ready before duplicating.")
        nodes = await conn.fetch(
            "SELECT id,source_id,parent_id FROM campaign_knowledge_nodes "
            "WHERE campaign_id=$1 AND tenant_id=$2 ORDER BY depth,position,id FOR SHARE",
            original, tenant,
        )
        source_ids = {str(row["id"]): uuid4() for row in sources}
        node_ids = {str(row["id"]): uuid4() for row in nodes}
        seen = {}
        for node in nodes:
            parent = str(node["parent_id"]) if node["parent_id"] is not None else None
            owner = str(node["source_id"])
            if owner not in source_ids or (parent is not None and seen.get(parent) != owner):
                raise CampaignDuplicationRefused(409, "Campaign knowledge links must be repaired before duplicating.")
            seen[str(node["id"])] = owner

        # Explicit settings allowlist: future lifecycle/counter columns cannot
        # silently become copyable. JSON settings retain the selected voice,
        # first speaker, campaign brief and calling schedule without providers.
        name = str(source["name"])[:248] + " - Copy"
        copied = await conn.fetchrow(
            """INSERT INTO campaigns
                (tenant_id,name,description,status,system_prompt,voice_id,tts_provider,
                 max_concurrent_calls,retry_failed,max_retries,goal,script_config,calling_config,
                 direction,knowledge_mode,knowledge_model,prompt_version_pin,
                 total_leads,calls_completed,calls_failed,started_at,completed_at)
               SELECT tenant_id,$3,description,'draft',system_prompt,voice_id,tts_provider,
                      max_concurrent_calls,retry_failed,max_retries,goal,script_config,calling_config,
                      'outbound',knowledge_mode,knowledge_model,prompt_version_pin,0,0,0,NULL,NULL
               FROM campaigns WHERE id=$1 AND tenant_id=$2 AND direction='outbound'
               RETURNING *""",
            original, tenant, name,
        )
        if copied is None:
            raise CampaignDuplicationRefused(409, "Campaign changed before it could be duplicated.")
        new_id = copied["id"]
        for row in sources:
            saved = await conn.fetchval(
                """INSERT INTO campaign_knowledge_sources
                    (id,campaign_id,tenant_id,filename,raw_md,token_count,version,status,error)
                   SELECT $1,$2,tenant_id,filename,raw_md,token_count,version,status,error
                   FROM campaign_knowledge_sources
                   WHERE id=$3 AND tenant_id=$4 AND campaign_id=$5 AND status='ready'
                   RETURNING id""",
                source_ids[str(row["id"])], new_id, row["id"], tenant, original,
            )
            if saved is None:
                raise CampaignDuplicationRefused(409, "Campaign knowledge changed before it could be duplicated.")
        for row in nodes:
            parent = str(row["parent_id"]) if row["parent_id"] is not None else None
            saved = await conn.fetchval(
                """INSERT INTO campaign_knowledge_nodes
                    (id,campaign_id,tenant_id,source_id,parent_id,depth,path,position,heading,
                     content,summary,voice_answer,keywords,example_questions,search_text,search_tsv,
                     priority,enabled,hit_count)
                   SELECT $1,$2,tenant_id,$3,$4,depth,path,position,heading,content,summary,
                          voice_answer,keywords,example_questions,search_text,search_tsv,priority,enabled,0
                   FROM campaign_knowledge_nodes
                   WHERE id=$5 AND tenant_id=$6 AND campaign_id=$7 AND source_id=$8
                   RETURNING id""",
                node_ids[str(row["id"])], new_id, source_ids[str(row["source_id"])],
                node_ids[parent] if parent is not None else None,
                row["id"], tenant, original, row["source_id"],
            )
            if saved is None:
                raise CampaignDuplicationRefused(409, "Campaign knowledge changed before it could be duplicated.")
        fields = await conn.fetch(
            """INSERT INTO campaign_lead_fields
                (tenant_id,campaign_id,field_key,label,field_type,is_required,
                 agent_visible,user_visible,options,sort_order)
               SELECT tenant_id,$3,field_key,label,field_type,is_required,
                      agent_visible,user_visible,options,sort_order
               FROM campaign_lead_fields WHERE campaign_id=$1 AND tenant_id=$2
               RETURNING id""",
            original, tenant, new_id,
        )
        # Keep copied document/prompt contents out of the audit log. The receipt
        # identifies both durable campaigns and the exact copied resource counts.
        await audit.log_in_transaction(
            conn, event_type=AuditEvent.CONFIG_CHANGED, action="campaign.duplicate",
            actor_id=actor_id, actor_role=actor_role, tenant_id=str(tenant),
            resource_type="campaign", resource_id=new_id,
            before_state={"id": str(original), "name": source["name"], "status": source["status"]},
            after_state={"id": str(new_id), "name": name, "status": "draft",
                         "voice_id": copied["voice_id"], "tts_provider": copied["tts_provider"],
                         "knowledge_sources": len(sources), "knowledge_nodes": len(nodes),
                         "lead_field_definitions": len(fields)},
        )
        result = dict(copied)
    return result
