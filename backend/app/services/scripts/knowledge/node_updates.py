"""One source-edit contract for HTTP and authorized assistant mutations."""


def prepare_node_changes(changes: dict) -> dict:
    fields = dict(changes)
    if "heading" in fields or "content" in fields:
        # Generated text describes the previous source revision. Retain only
        # summary/phrasing explicitly supplied together with the new source.
        fields.setdefault("summary", None)
        fields.setdefault("voice_answer", None)
        fields["keywords"] = []
        fields["example_questions"] = []
    return fields


async def write_node_update(conn, *, tenant_id, campaign_id, node_id, fields, current):
    """Caller owns the authorization transaction; both revisions commit together."""
    allowed = {
        "heading",
        "content",
        "enabled",
        "priority",
        "summary",
        "voice_answer",
        "keywords",
        "example_questions",
    }
    if not fields or not set(fields).issubset(allowed):
        raise ValueError("Invalid knowledge node fields")
    setters = [f"{key} = ${index + 4}" for index, key in enumerate(fields)]
    params = list(fields.values())
    if "heading" in fields or "content" in fields:
        search_text = " ".join(
            str(fields.get(key, current.get(key)) or "") for key in ("heading", "content")
        ).strip()
        index = len(params) + 4
        params.append(search_text)
        setters += [f"search_text = ${index}", f"search_tsv = to_tsvector('english', ${index})"]
    return await conn.fetchval(
        f"""
        WITH changed AS (
            UPDATE campaign_knowledge_nodes n
            SET {', '.join(setters)}, updated_at = NOW()
            WHERE n.id = $1 AND n.campaign_id = $2 AND n.tenant_id = $3
              AND EXISTS (SELECT 1 FROM campaign_knowledge_sources s
                          WHERE s.id=n.source_id AND s.campaign_id=$2 AND s.tenant_id=$3)
            RETURNING n.id, n.source_id
        ), revised AS (
            UPDATE campaign_knowledge_sources s
            SET version = s.version + 1, updated_at = NOW()
            FROM changed WHERE s.id = changed.source_id
              AND s.campaign_id = $2 AND s.tenant_id = $3
            RETURNING s.id
        )
        SELECT changed.id FROM changed JOIN revised ON revised.id=changed.source_id
        """,
        node_id,
        campaign_id,
        tenant_id,
        *params,
    )
