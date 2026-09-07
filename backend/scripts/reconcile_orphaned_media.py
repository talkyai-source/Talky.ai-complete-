"""Reviewed maintenance entry point for the same reconciler used by the watchdog.

Default is read-only. --apply requires the exact digest from a prior inspection
and independently verifies zero gateway sessions and no human PBX channels
before every DELETE. It never restarts services, modifies billing or generates
Asterisk configuration. Load the normal backend environment before running.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.infrastructure.telephony.media_reconciliation import discover_orphan_media, reconcile_orphan_media  # noqa: E402


def plan_digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def run(args):
    app = os.getenv("ASTERISK_ARI_APP", "talky_ai")
    ari_url = f"http://{os.getenv('ASTERISK_ARI_HOST', '127.0.0.1')}:{os.getenv('ASTERISK_ARI_PORT', '8088')}/ari"
    gateway_url = os.getenv("ASTERISK_GATEWAY_BASE_URL", "http://127.0.0.1:18080").rstrip("/")
    auth = aiohttp.BasicAuth(os.getenv("ASTERISK_ARI_USER", "talky"), os.environ["ASTERISK_ARI_PASSWORD"])
    maintenance_ok = False
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        async def request(method, path, **kwargs):
            ok = kwargs.pop("ok", (200,))
            if "json_body" in kwargs:
                kwargs["json"] = kwargs.pop("json_body")
            async with session.request(method, ari_url + path, auth=auth, **kwargs) as response:
                if response.status not in ok:
                    raise RuntimeError(f"ARI {method} {path} returned {response.status}")
                return {} if response.status in (204, 404) else await response.json()

        async def prove_quiescence():
            nonlocal maintenance_ok
            maintenance_ok = False
            channels = await request("GET", "/channels")
            if not isinstance(channels, list) or any(not isinstance(c, dict) or not c.get("name", "").startswith("UnicastRTP/") for c in channels):
                raise RuntimeError("maintenance refused: PBX has human or unknown channels")
            token = os.environ["VOICE_GATEWAY_AUTH_TOKEN"]
            async with session.get(gateway_url + "/v1/sessions", headers={"Authorization": "Bearer " + token}) as response:
                if response.status != 200:
                    raise RuntimeError("maintenance refused: gateway inventory unavailable")
                payload = await response.json()
            # Even stopping/evidence entries defer maintenance until retirement;
            # do not equate a process counter or /ready response with a drain.
            if not isinstance(payload, dict) or payload.get("sessions") != []:
                raise RuntimeError("maintenance refused: gateway is not fully empty")
            maintenance_ok = True

        async def ari(method, path, **kwargs):
            if method != "GET":
                if not args.apply or method != "DELETE":
                    raise RuntimeError("read-only maintenance mode")
                await prove_quiescence()
            return await request(method, path, **kwargs)

        plan = await discover_orphan_media(ari, app, excluded=set())
        digest = plan_digest(plan)
        if not args.apply:
            print(json.dumps({"mode": "check", "digest": digest, "plan": plan}, sort_keys=True))
            return
        if args.expected_digest != digest:
            raise RuntimeError("maintenance refused: reviewed plan digest changed")
        await prove_quiescence()
        count = await reconcile_orphan_media(ari, app, owner=lambda: maintenance_ok,
                                            exclusions=lambda: set(), expected_plan=plan)
        remaining = await discover_orphan_media(ari, app, excluded=set())
        print(json.dumps({"mode": "apply", "reviewed_digest": digest, "reconciled_groups": count,
                          "remaining": remaining}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-digest")
    args = parser.parse_args()
    if args.apply != bool(args.expected_digest):
        parser.error("--apply and --expected-digest must be supplied together")
    try:
        asyncio.run(run(args))
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
