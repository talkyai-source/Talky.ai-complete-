"""Register the PayPal webhook for paid minute top-ups and save its id.

Sibling of ``stripe_catalog_setup.py``. Idempotent, a dry run unless ``--apply``.
It never prints a credential, token or settings value: only setting NAMES and
PayPal's webhook id (not a secret).

PayPal's settings come from the PROCESS ENVIRONMENT (load the service's
EnvironmentFile first, as for the Stripe tool): ``PAYPAL_ENV`` and the client
id and secret for that environment. The tool does not parse ``--env-file`` for
credentials; it only writes into it.

1. Lists the account's webhooks and finds the one for ``--webhook-url``.
2. Creates it with every event the billing code handles, or, when it exists
   with different events, replaces its event list. A ``*`` subscription counts
   as complete.
3. With ``--apply`` writes ``PAYPAL_WEBHOOK_ID`` (and, with ``--enable``,
   ``PAYPAL_TOPUPS_ENABLED``) into ``--env-file`` after a backup copy.

Restart the services afterwards so they read the new environment.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from stripe_catalog_setup import upsert_env  # noqa: E402

WEBHOOK_PATH = "/api/v1/billing/paypal/webhooks"


def _preflight(webhook_url: str | None) -> str | None:
    """A plain message when the run must stop before any call, else None."""
    from app.domain.services import paypal_mode

    if not webhook_url or not webhook_url.startswith("https://") or not webhook_url.endswith(WEBHOOK_PATH):
        return f"--webhook-url must be the public https URL ending with {WEBHOOK_PATH}"
    env = paypal_mode.paypal_env()
    if env is None:
        return "PayPal is not configured; missing setting: PAYPAL_ENV (live or sandbox)"
    if paypal_mode.paypal_credentials() is None:
        names = ["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"]
        if env == "sandbox":
            names = ["PAYPAL_SANDBOX_CLIENT_ID", "PAYPAL_SANDBOX_CLIENT_SECRET"]
        return "PayPal is not configured; missing settings: " + ", ".join(names)
    return None


async def run(args, *, client) -> int:
    from app.domain.services import paypal_mode
    from app.domain.services.paypal_client import PayPalError

    problem = _preflight(args.webhook_url)
    if problem:
        print(problem)
        return 1
    events = paypal_mode.PAYPAL_WEBHOOK_EVENTS
    env = paypal_mode.paypal_env()
    apply = bool(args.apply)
    print(f"PayPal environment: {env}{'' if apply else ' (dry run)'}")
    webhook_id: str | None = None
    try:
        existing = [h for h in await client.list_webhooks() if h.get("url") == args.webhook_url]
        if not existing:
            print(f"{'creating' if apply else 'would create'} webhook")
            if apply:
                created = await client.create_webhook(url=args.webhook_url, event_names=events)
                webhook_id = created.get("id")
                if not isinstance(webhook_id, str) or not webhook_id:
                    print("PayPal did not return a webhook id; nothing written")
                    return 1
        else:
            hook = existing[0]
            webhook_id = hook.get("id")
            have = {e.get("name") for e in (hook.get("event_types") or []) if isinstance(e, dict)}
            if "*" in have or have == set(events):
                print("webhook already registered")
            else:
                missing = len(set(events) - have)
                extra = len(have - set(events))
                if apply:
                    print(f"updating webhook events (missing: {missing}, extra: {extra})")
                    await client.update_webhook_events(webhook_id, event_names=events)
                else:
                    print(f"would update webhook events (missing: {missing}, extra: {extra})")
    except PayPalError as error:
        print(str(error))
        return 1

    if apply:
        if not isinstance(webhook_id, str) or not webhook_id:
            print("no webhook id to write; nothing written")
            return 1
        values = {"PAYPAL_WEBHOOK_ID": webhook_id}
        if args.enable:
            values["PAYPAL_TOPUPS_ENABLED"] = args.enable
        for change in upsert_env(args.env_file, values):
            print(change)
    else:
        print("would set PAYPAL_WEBHOOK_ID")
        if args.enable:
            print(f"would set PAYPAL_TOPUPS_ENABLED={args.enable}")
    print(f"environment: {env}" + (f", webhook id: {webhook_id}" if webhook_id else ""))
    print("done" if apply else "dry run complete; nothing changed")
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--webhook-url", required=True)
    parser.add_argument("--enable", choices=["off", "team", "on"])
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    problem = _preflight(args.webhook_url)
    if problem:
        print(problem)
        return 1
    from app.domain.services.paypal_client import get_paypal_client

    client = get_paypal_client()

    async def _go() -> int:
        try:
            return await run(args, client=client)
        finally:
            await client.aclose()

    return asyncio.run(_go())


if __name__ == "__main__":
    sys.exit(main())
