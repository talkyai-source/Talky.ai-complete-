"""Write each SIP trunk's REAL Asterisk registration state into the DB.

Run by talky-trunk-status.timer every ~15s (one-shot). Reads
`asterisk -rx 'pjsip show registrations'` — the live truth — maps each
registration to its trunk, and updates tenant_sip_trunks.live_registration_status
+ live_status_checked_at. The Settings trunk card renders this (auto-refresh), so
the card reflects reality, never a frozen Test snapshot or dummy data.

Mapping:
  * registration trunk: namespaced registration ``trunk-<id>-reg`` must be
    registered and its endpoint must exist;
  * IP-auth trunk: there is no registration object, so namespaced endpoint
    presence is the runtime proof and is stored as ``loaded``;
  * hand-managed platform default: use its configured registration/endpoint;
  * inactive trunk: ``inactive``.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import asyncpg

ENV_PATH = "/opt/talky/backend/.env"
DEFAULT_REG = "blazedigitel-reg"
DEFAULT_ENDPOINT = "blazedigitel-endpoint"
DEFAULT_PLATFORM_TRUNK_NAME = "platform-default"
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


def _asterisk_cli(command: str) -> tuple[str, bool]:
    try:
        proc = subprocess.run(
            ["asterisk", "-rx", command],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as exc:
        print(f"asterisk query failed command={command!r}: {exc}", file=sys.stderr)
        return "", False
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        print(
            f"asterisk query failed command={command!r} rc={proc.returncode}: {detail}",
            file=sys.stderr,
        )
        return proc.stdout or "", False
    return proc.stdout or "", True


def read_registrations() -> tuple[dict[str, str], bool]:
    """Return ``(registration-name -> status, query_succeeded)``."""
    out, ok = _asterisk_cli("pjsip show registrations")
    reg: dict[str, str] = {}
    for line in out.splitlines():
        if "/sip:" not in line:
            continue
        name = line.split("/sip:", 1)[0].strip()
        for s in _STATUSES:
            if re.search(rf"\b{s}\b", line):
                reg[name] = s.lower()
                break
    return reg, ok


def read_endpoints() -> tuple[set[str], bool]:
    """Return the PJSIP endpoint object names currently loaded by Asterisk."""
    out, ok = _asterisk_cli("pjsip show endpoints")
    endpoints: set[str] = set()
    for line in out.splitlines():
        match = re.match(r"^\s*Endpoint:\s+(\S+)", line)
        if not match:
            continue
        # Asterisk prints Endpoint/CID in this column.  Object names cannot
        # contain '/', so the part before it is the endpoint identity.
        name = match.group(1).split("/", 1)[0].strip()
        if name and not name.startswith("<"):
            endpoints.add(name)
    return endpoints, ok


def read_reg_failures() -> dict[str, str]:
    """registered-identity (number/login) -> 'CODE Reason' (e.g. '403 Forbidden')
    parsed from recent Asterisk registration-failure log lines, so the card can
    show the REAL reason a trunk is Rejected."""
    for path in ("/var/log/asterisk/messages.log", "/var/log/asterisk/full"):
        try:
            out = subprocess.run(
                ["tail", "-n", "500", path], capture_output=True, text=True, timeout=10,
            ).stdout
        except Exception:
            continue
        if not out:
            continue
        fails: dict[str, str] = {}
        pat = re.compile(
            r"(\d{3} [A-Za-z][A-Za-z ]*?) (?:fatal |temporal |non-fatal )?response "
            r"received.*registration attempt to 'sip:([^@]+)@"
        )
        for line in out.splitlines():
            m = pat.search(line)
            if m:
                fails[m.group(2).strip()] = m.group(1).strip()  # most-recent wins
        if fails:
            return fails
    return {}


def status_for(
    trunk: dict,
    reg: dict[str, str],
    endpoints: set[str],
    *,
    registrations_ok: bool = True,
    endpoints_ok: bool = True,
) -> str:
    tid = trunk["id"]
    if not trunk["is_active"]:
        return "inactive"

    platform_name = os.getenv(
        "PLATFORM_SIP_TRUNK_NAME", DEFAULT_PLATFORM_TRUNK_NAME
    ).strip().lower()
    is_platform_default = str(trunk.get("trunk_name") or "").strip().lower() == platform_name
    endpoint_name = (
        os.getenv("TELEPHONY_PJSIP_OUTBOUND_ENDPOINT", DEFAULT_ENDPOINT)
        if is_platform_default
        else f"trunk-{tid}"
    )
    registration_name = (
        os.getenv("TELEPHONY_PJSIP_DEFAULT_REGISTRATION", DEFAULT_REG)
        if is_platform_default
        else f"trunk-{tid}-reg"
    )

    if endpoints_ok and endpoint_name not in endpoints:
        return "missing_config"

    register_enabled = bool((trunk.get("metadata") or {}).get("register"))
    # The platform-default row is hand-managed and its legacy metadata does
    # not carry register=true even though the global upstream may register.
    if register_enabled or is_platform_default:
        live = reg.get(registration_name)
        if live:
            return live
        if not registrations_ok:
            # Endpoint presence still proves an IP-auth platform trunk is
            # loaded, but it cannot prove a configured registration healthy.
            return "loaded" if is_platform_default and endpoint_name in endpoints else "unknown"
        if is_platform_default and endpoint_name in endpoints:
            return "loaded"
        return "unregistered"

    if not endpoints_ok:
        return "unknown"
    return "loaded"


def reconcile_inactive_config_files(rows, endpoints: set[str]) -> int:
    """Remove stale managed files and request one reload when cleanup is due.

    Deactivation is database-authoritative so calls are denied immediately.
    This root timer provides the durable second half: if the API could not
    reload Asterisk, the next run observes the still-loaded endpoint and
    retries without needing a separate worker or queue.
    """
    base = Path(os.getenv("TELEPHONY_PJSIP_CONF_DIR", "/etc/asterisk/pjsip.d"))
    platform_name = os.getenv(
        "PLATFORM_SIP_TRUNK_NAME", DEFAULT_PLATFORM_TRUNK_NAME
    ).strip().lower()
    cleanup_due = 0
    for row in rows:
        if row["is_active"]:
            continue
        if str(row["trunk_name"] or "").strip().lower() == platform_name:
            continue
        tid = str(row["id"])
        target = base / f"trunk-{tid}.conf"
        endpoint_loaded = f"trunk-{tid}" in endpoints
        try:
            file_present = target.exists()
            if file_present:
                target.unlink()
        except OSError as exc:
            print(f"inactive trunk file cleanup failed trunk={tid}: {exc}", file=sys.stderr)
            continue
        if file_present or endpoint_loaded:
            cleanup_due += 1
    if cleanup_due:
        _out, ok = _asterisk_cli("pjsip reload")
        if not ok:
            print(
                f"inactive trunk cleanup reload deferred count={cleanup_due}",
                file=sys.stderr,
            )
    return cleanup_due


async def main() -> None:
    reg, registrations_ok = read_registrations()
    endpoints, endpoints_ok = read_endpoints()
    fails = read_reg_failures()
    conn = await asyncpg.connect(load_database_url())
    try:
        # This is a platform-wide maintenance task: it must observe every
        # tenant's trunks, and it opens a raw connection rather than going
        # through postgres_adapter, so nothing has set the tenant GUC for it.
        # Once the app role loses BYPASSRLS the 0013 policy
        #   COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE),''
        #            )::boolean, FALSE) OR tenant_id = ...
        # evaluates FALSE on an unset GUC and this SELECT returns zero rows.
        # The loop below would then exit 0 having updated nothing, freezing
        # live_status_checked_at until evaluate_trunk_runtime() starts denying
        # every inbound call "trunk_not_ready" while `pjsip show registrations`
        # still reports Registered.
        await conn.execute("SET app.bypass_rls = 'true'")

        rows = await conn.fetch(
            "SELECT id, trunk_name, is_active, metadata, auth_username FROM tenant_sip_trunks"
        )
        if not rows:
            # Never fail quietly: a zero-row read here is indistinguishable from
            # a healthy no-op, and admission depends on this timestamp.
            print(
                "trunk status updater read 0 trunks — refusing to report success. "
                "Check that app.bypass_rls is honoured by the 0013 policy for this "
                "role, or that tenant_sip_trunks is genuinely empty.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        cleanup_count = reconcile_inactive_config_files(rows, endpoints)
        for r in rows:
            raw = r["metadata"]
            md = raw if isinstance(raw, dict) else (
                json.loads(raw) if isinstance(raw, str) and raw else {}
            )
            st = status_for(
                {
                    "id": str(r["id"]),
                    "trunk_name": r["trunk_name"],
                    "is_active": r["is_active"],
                    "metadata": md,
                },
                reg,
                endpoints,
                registrations_ok=registrations_ok,
                endpoints_ok=endpoints_ok,
            )
            detail = None
            if st in ("rejected", "unregistered"):
                ident = (md.get("caller_id") or r["auth_username"] or "").strip()
                detail = fails.get(ident)
                if not detail and r["auth_username"]:
                    detail = fails.get(r["auth_username"].strip())
            elif st == "missing_config":
                detail = "Configured PJSIP endpoint is not loaded by Asterisk"
            elif st == "unknown":
                detail = "Asterisk runtime query failed; inspect talky-trunk-status logs"
            await conn.execute(
                "UPDATE tenant_sip_trunks "
                "SET live_registration_status=$1, live_status_detail=$2, live_status_checked_at=NOW() "
                "WHERE id=$3",
                st, detail, r["id"],
            )
    finally:
        await conn.close()
    print(
        f"updated {len(rows)} trunks; registrations_ok={registrations_ok}; "
        f"endpoints_ok={endpoints_ok}; registrations={reg}; "
        f"endpoint_count={len(endpoints)}; inactive_cleanup={cleanup_count}; failures={fails}"
    )


if __name__ == "__main__":
    asyncio.run(main())
