"""Read-only diagnostics: synthetic database/provider results, no network calls."""
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
from app.core.postgres_adapter import QueryBuilder, PostgrestResponse, _LocalStorageBucket
from app.api.v1.endpoints import connectors


async def slow_query(self):
    await asyncio.sleep(0.05)
    return PostgrestResponse(data=[])


async def event_loop_probe(use_public_execute):
    loop = asyncio.get_running_loop()
    started = time.perf_counter()
    observed = []
    loop.call_later(0.01, lambda: observed.append(time.perf_counter() - started))
    with patch.object(QueryBuilder, '_execute_async', slow_query):
        for _ in range(4):
            query = QueryBuilder(None, 'synthetic')
            if use_public_execute:
                await query.execute()
            else:
                await query._execute_async()
    await asyncio.sleep(0.02)
    return round(observed[0] * 1000, 2)


class FailedDB:
    def __init__(self, fail_read=False, single=False):
        self.fail_read = fail_read
        self.single_result = single
        self.calls = []

    def table(self, table):
        db = self

        class Query:
            def __init__(self):
                self.operation = 'select'

            def select(self, *args): return self
            def eq(self, *args): return self
            def single(self): return self

            def delete(self):
                self.operation = 'delete'
                return self

            def execute(self):
                db.calls.append((table, self.operation))
                if self.operation == 'delete' or db.fail_read:
                    return PostgrestResponse(error='synthetic database failure')
                row = {'id': 'synthetic-connector', 'provider': 'gmail'}
                return PostgrestResponse(data=row if db.single_result else [row])

        return Query()


async def main():
    print('event_loop_10ms_callback', json.dumps({
        'public_await_execute_delay_ms': await event_loop_probe(True),
        'genuinely_async_control_delay_ms': await event_loop_probe(False),
        'synthetic_query_delay_ms': 50,
        'query_count': 4,
    }))
    user = SimpleNamespace(tenant_id='11111111-1111-1111-1111-111111111111')
    for label, db in [('lookup_failure', FailedDB(True)), ('both_deletes_fail', FailedDB())]:
        result = await connectors.disconnect_connector_by_type('email', current_user=user, db_client=db)
        print('disconnect_' + label, json.dumps({'result': result, 'operations': db.calls}))
    db = FailedDB(single=True)
    result = await connectors.delete_connector('synthetic-connector', current_user=user, db_client=db)
    print('legacy_disconnect_both_deletes_fail', json.dumps({'result': result, 'operations': db.calls}))
    # Resolve only: no directory creation, file read or write.
    root = Path(sys.argv[1]).resolve() / 'synthetic-storage'
    bucket = _LocalStorageBucket(root, 'recordings')
    escaped = bucket._abs_path('../recordings-sibling/proof.wav')
    print('legacy_storage_boundary', json.dumps({
        'accepted_outside_bucket': not escaped.is_relative_to(root / 'recordings'),
        'read_or_write_performed': False,
    }))


asyncio.run(main())
