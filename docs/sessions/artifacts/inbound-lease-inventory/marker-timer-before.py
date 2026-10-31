"""Execute the unchanged baseline expiry function with synthetic marker state."""
import ast
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess

BASE = 'c9d3f87e6dd7c9011008f886419e17888a1a6825'
source = subprocess.check_output(['git','show',BASE+':backend/app/domain/services/telephony/lifecycle.py'], text=True, encoding='utf-8')
tree = ast.parse(source)
node = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name == '_release_ended_marker_later')
body = ast.get_source_segment(source,node)

async def main():
    pending, completed, tasks = set(), set(), []
    def track(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task
    namespace = dict(asyncio=asyncio, _track_task=track, _ended_calls_in_flight=pending, _ended_calls_logically_completed=completed)
    exec(compile(body, '<baseline-expiry-function>', 'exec'), namespace)
    pending.add('synthetic-call')
    namespace['_release_ended_marker_later']('synthetic-call')
    await asyncio.sleep(0)
    pending.discard('synthetic-call')  # old attempt releases its failed marker
    pending.add('synthetic-call')     # a later callback owns this generation
    completed.add('synthetic-call')   # marker-level completion control
    before = dict(in_flight=True, logically_completed=True)
    tasks[0].cancel()
    await asyncio.gather(*tasks,return_exceptions=True)
    report = dict(source=BASE, extracted_function_sha256=hashlib.sha256(body.encode()).hexdigest(), scope='Actual baseline expiry function; synthetic markers/tasks; no calls, database or providers.', before_old_timer_cancel=before, after_old_timer_cancel=dict(in_flight='synthetic-call' in pending,logically_completed='synthetic-call' in completed))
    Path(__file__).with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
asyncio.run(main())
