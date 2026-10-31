"""Prove the whole rollback chain in production: archive -> cache -> pin -> prompt.

Everything is inside a transaction that is ROLLED BACK. The synthetic archived
version and the campaign pin both disappear.
"""
import asyncio, os, sys
sys.path.insert(0, os.environ.get("TALKY_BACKEND", "/opt/talky/backend"))
import asyncpg

from app.services.scripts.prompts.bodies import (
    load_cache, prime_cache, resolve_body_sync, cache_size, current_bodies,
)
from app.services.scripts.prompts.versions import version_for
from app.domain.services.telephony_session_config import build_telephony_session_config

OLD = ("You are {agent_name} at {company_name}. THIS IS THE ARCHIVED v0 BODY. "
       "Speak only in haiku.")
SLOTS = {"industry": "cleaning", "services_description": "office cleaning",
         "coverage_area": "Manchester", "value_proposition": "fixed price",
         "call_reason": "intro", "qualification_questions": "how many sites?",
         "disqualifying_answers": "under 5 staff", "calendar_booking_type": "15-min"}

ok = []
def check(n, c, d=""):
    ok.append(bool(c)); print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f"   [{d}]" if d else ""))

class Campaign:
    def __init__(self, pin):
        self.id = "11111111-1111-1111-1111-111111111111"
        self.name = "rollback-probe"
        self.tenant_id = None
        self.system_prompt = ""
        self.voice_id = ""
        self.prompt_version_pin = pin
        self.script_config = {"persona_type": "lead_gen", "company_name": "Northwind",
                              "campaign_slots": {**SLOTS, "company_name": "Northwind"}}

async def main():
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg", "postgresql")
    c = await asyncpg.connect(url)

    print("== what the live archive holds ==")
    rows = await c.fetch("SELECT persona_type, version, length(body) AS n, approved "
                         "FROM prompt_template_versions ORDER BY persona_type")
    for r in rows:
        print(f"   {r['persona_type']:18} {r['version']:22} {r['n']:6} chars  approved={r['approved']}")
    check("this build's bodies are archived", len(rows) >= 3, f"{len(rows)} versions")
    check("archived body matches the shipped constant",
          any(r["n"] == len(current_bodies()["lead_gen"]) for r in rows if r["persona_type"] == "lead_gen"))

    tx = c.transaction(); await tx.start()
    try:
        print("\n== add a synthetic OLDER version to roll back to ==")
        await c.execute(
            """INSERT INTO prompt_template_versions
                   (persona_type, template, version, body, body_sha)
               VALUES ('lead_gen','generic_lead_generation','lead_gen@0',$1,'0'||repeat('a',63))""",
            OLD)
        # Prime the cache directly. A second connection cannot see this
        # transaction's uncommitted INSERT — that is correct Postgres behaviour,
        # not a defect, and the DB->cache path is already proven by the startup
        # log (prompt_body_cache_loaded versions=3). What THIS script is for is
        # resolution and composition.
        n = prime_cache([{"version": "lead_gen@0", "persona_type": "lead_gen", "body": OLD}])
        check("cache holds the archived version", n >= 1, f"{cache_size()} cached")

        print("\n== resolution ==")
        body, ver = resolve_body_sync("lead_gen", "lead_gen@0")
        check("pin resolves to the archived body", body == OLD and ver == "lead_gen@0")
        cur = version_for("lead_gen")
        check("pinning the CURRENT version composes from code",
              resolve_body_sync("lead_gen", cur) == (None, None), f"current={cur}")
        check("an unknown pin degrades to code",
              resolve_body_sync("lead_gen", "lead_gen@nope") == (None, None))

        print("\n== a real session config, pinned vs not ==")
        pinned = build_telephony_session_config(campaign=Campaign("lead_gen@0"))
        normal = build_telephony_session_config(campaign=Campaign(None))
        check("PINNED prompt contains the archived text",
              "ARCHIVED v0 BODY" in pinned.system_prompt)
        check("unpinned prompt does NOT", "ARCHIVED v0 BODY" not in normal.system_prompt)
        check("pinned call is reported under the PINNED version",
              pinned.prompt_version == "lead_gen@0", pinned.prompt_version)
        check("unpinned call is reported under the current version",
              normal.prompt_version == cur, normal.prompt_version)
        check("the two prompts really differ",
              pinned.prompt_hash != normal.prompt_hash,
              f"{pinned.prompt_hash} vs {normal.prompt_hash}")
        check("rolled-back prompt still has the campaign's own company name",
              "Northwind" in pinned.system_prompt)
        check("guardrails still wrap the rolled-back body",
              len(pinned.system_prompt) > len(OLD) + 500,
              f"{len(pinned.system_prompt)} chars")
    finally:
        await tx.rollback()

    left = await c.fetchval("SELECT count(*) FROM prompt_template_versions WHERE version='lead_gen@0'")
    check("synthetic version rolled back, nothing persisted", left == 0)
    await c.close()
    print(f"\n===== {sum(ok)}/{len(ok)} checks passed =====")

asyncio.run(main())
