"""Write each SIP trunk's REAL Asterisk registration state into the DB.

Run by talky-trunk-status.timer every ~15s (one-shot). Reads
`asterisk -rx 'pjsip show registrations'` — the live truth — maps each
registration to its trunk, and updates tenant_sip_trunks.live_registration_status
+ live_status_checked_at. The Settings trunk card renders this (auto-refresh), so
the card reflects reality, never a frozen Test snapshot or dummy data.

Mapping:
  * own-trunk registration object   = ``trunk-<id>-reg``  -> that trunk's status.
  * a trunk with no own registration (uses the shared platform default) -> the
    ``blazedigitel-reg`` (env default) status, when active.
  * inactive trunk -> ``inactive``. Active own-reg not present yet -> ``unregistered``.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys

import asyncpg

ENV_PATH = "/opt/talky/backend/.env"
DEFAULT_REG = "blazedigitel-reg"
_STATUSES = ("Registered", "Rejected", "Unregistered", "Registering", "Stopped", "Failed")


def load_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    with open(ENV_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.partition("=")[2].strip().strip('"').strip("'")
    raise SystemExit("DATABASE_URL not found")


def read_registrations() -> dict[str, str]:
    """reg-object-name -> normalized status (lowercase)."""
    try:
        out = subprocess.run(
            ["asterisk", "-rx", "pjsip show registrations"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception as exc:  # asterisk down / no perms -> everything unknown
        print(f"asterisk query failed: {exc}", file=sys.stderr)
        return {}
    reg: dict[str, str] = {}
    for line in out.splitlines():
        if "/sip:" not in line:
            continue
        name = line.split("/sip:", 1)[0].strip()
        for s in _STATUSES:
            if re.search(rf"\b{s}\b", line):
                reg[name] = s.lower()
                break
    return reg


def status_for(trunk: dict, reg: dict[str, str]) -> str:
    tid = trunk["id"]
    own = reg.get(f"trunk-{tid}-reg")
    if not trunk["is_active"]:
        return "inactive"
    if own:
        return own
    # No own registration object → the trunk rides the shared platform default.
    register_enabled = bool((trunk.get("metadata") or {}).get("register"))
    if not register_enabled:
        return reg.get(DEFAULT_REG, "unknown")
    return "unregistered"


async def main() -> None:
    reg = read_registrations()
    conn = await asyncpg.connect(load_database_url())
    try:
        rows = await conn.fetch("SELECT id, is_active, metadata FROM tenant_sip_trunks")
        for r in rows:
            raw = r["metadata"]
            md = raw if isinstance(raw, dict) else (
                json.loads(raw) if isinstance(raw, str) and raw else {}
            )
            st = status_for({"id": str(r["id"]), "is_active": r["is_active"], "metadata": md}, reg)
            await conn.execute(
                "UPDATE tenant_sip_trunks "
                "SET live_registration_status=$1, live_status_checked_at=NOW() "
                "WHERE id=$2",
                st, r["id"],
            )
    finally:
        await conn.close()
    print(f"updated {len(rows)} trunks; registrations seen: {reg}")


if __name__ == "__main__":
    asyncio.run(main())
