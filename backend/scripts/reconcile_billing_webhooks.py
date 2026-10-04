"""Run from backend: python -m scripts.reconcile_billing_webhooks --help.

Default/list and inspect are read-only. Retrying money handlers requires one
explicit event ID; legacy/ambiguous events require a recorded review first.
"""

import argparse
import asyncio
import json


async def run(args):
    from app.core.db import init_db_pool, close_db_pool
    from app.core.postgres_adapter import PostgresClient
    from app.domain.services.billing_service import BillingService
    from app.domain.services.billing_webhook_reconciliation import BillingReconciliation

    pool = await init_db_pool()
    try:
        billing = BillingService(PostgresClient(pool))
        reconciliation = BillingReconciliation(pool, billing)
        if args.action == "list":
            result = await reconciliation.list_unresolved(args.limit)
        elif args.action == "inspect":
            result, _ = await reconciliation.inspect(args.event_id)
        elif args.action == "authorize-retry":
            result = await reconciliation.authorize_retry(
                args.event_id, operator=args.operator, reason=args.reason
            )
        elif args.action == "retry-unsent-notification":
            result = await reconciliation.retry_unsent_notification(
                args.notification_id, operator=args.operator, reason=args.reason
            )
        else:
            result = await reconciliation.retry(args.event_id)
        print(json.dumps(result, default=str, indent=2))
    finally:
        await close_db_pool()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("list").add_argument("--limit", type=int, default=25)
    for action in ("inspect", "retry", "authorize-retry"):
        command = commands.add_parser(action)
        command.add_argument("--event-id", required=True)
        if action == "authorize-retry":
            command.add_argument("--operator", required=True)
            command.add_argument("--reason", required=True)
    command = commands.add_parser("retry-unsent-notification")
    command.add_argument("--notification-id", required=True)
    command.add_argument("--operator", required=True)
    command.add_argument("--reason", required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
