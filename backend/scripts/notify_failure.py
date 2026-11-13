#!/usr/bin/env python3
"""Notify for a failed unit, or explicitly send a synthetic operator test."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.operational_alerts import claim_alert_slot, release_alert_slot, send_alert


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", default="talky-alert-test.service")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--send", action="store_true", help="Actually dispatch to configured destinations")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.send:
        print("alert-test dry-run: no notification sent; use --send for a synthetic delivery test")
        return 0
    try:
        detail = ""
        if not args.test:
            # A unit that keeps failing alerts once an hour, not on every run.
            send, detail = asyncio.run(claim_alert_slot(args.unit))
            if not send:
                print(f"alert suppressed: {args.unit} already alerted within the hour")
                return 0
        result = asyncio.run(send_alert(args.unit, synthetic=args.test, detail=detail))
        if not result["delivered"] and not args.test:
            asyncio.run(release_alert_slot(args.unit, detail))
        print(json.dumps(result, sort_keys=True))
        return 0 if result["delivered"] else 1
    except Exception as exc:
        print(f"alert not delivered: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
