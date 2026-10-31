"""Exact bounded integration: archived location assumes tmp/ when executing."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading

root = Path(__file__).resolve().parents[1]
folder = root / 'docs/sessions/artifacts/knowledge-evidence-budget'
folder.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(root / 'backend'))
local = threading.local()
original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex
original_socketpair = socket.socketpair
connections = {'internal_socketpairs': 0, 'prohibited_attempts': 0}


def guarded_connect(sock, address, *, original):
    if getattr(local, 'socketpair', False) and address[0] in {'127.0.0.1', '::1'}:
        return original(sock, address)
    connections['prohibited_attempts'] += 1
    raise AssertionError('No database or provider network connection is authorized for this unit run')


def guarded_socketpair(*args, **kwargs):
    local.socketpair = True
    try:
        pair = original_socketpair(*args, **kwargs)
        connections['internal_socketpairs'] += 1
        return pair
    finally:
        local.socketpair = False


socket.socket.connect = lambda sock, addr: guarded_connect(sock, addr, original=original_connect)
socket.socket.connect_ex = lambda sock, addr: guarded_connect(sock, addr, original=original_connect_ex)
socket.socketpair = guarded_socketpair

import pytest  # noqa: E402

modules = ['tests/unit/test_knowledge_evidence_budget_order.py', 'tests/unit/test_agent_knowledge_repairs.py', 'tests/unit/test_knowledge_uncertainty_contract.py', 'tests/unit/test_knowledge_relevance.py', 'tests/unit/test_realtime_knowledge_evidence.py', 'tests/unit/test_knowledge_tool.py', 'tests/unit/test_knowledge_render.py', 'tests/unit/test_knowledge_enrichment_routing.py', 'tests/unit/test_knowledge_enricher_model.py', 'tests/unit/test_knowledge_ingest_staging.py', 'tests/unit/test_reenrich_campaign_knowledge_script.py', 'tests/unit/test_knowledge_budget.py']
paths = ['backend/app/domain/services/voice_pipeline/kb_budget.py', 'backend/app/services/scripts/knowledge/enricher.py', 'backend/app/services/scripts/knowledge/retrieval.py', 'backend/app/services/scripts/knowledge/passages.py', 'backend/app/services/scripts/knowledge/ingest_service.py', 'backend/app/services/scripts/knowledge/md_tree.py', 'backend/app/domain/services/voice_pipeline/knowledge_tool.py', 'backend/tests/fixtures/knowledge/ag02_gold.json', 'backend/scripts/evaluate_ag02_knowledge.py', 'backend/tests/unit/test_knowledge_evidence_budget_order.py', 'backend/tests/unit/test_agent_knowledge_repairs.py', 'backend/tests/unit/test_knowledge_uncertainty_contract.py', 'backend/tests/unit/test_knowledge_relevance.py', 'backend/tests/unit/test_realtime_knowledge_evidence.py', 'backend/tests/unit/test_knowledge_tool.py', 'backend/tests/unit/test_knowledge_render.py', 'backend/tests/unit/test_knowledge_enrichment_routing.py', 'backend/tests/unit/test_knowledge_enricher_model.py', 'backend/tests/unit/test_knowledge_ingest_staging.py', 'backend/tests/unit/test_reenrich_campaign_knowledge_script.py', 'backend/tests/unit/test_knowledge_budget.py']


def hashes():
    return {path: hashlib.sha256((root / path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for path in paths}


before = hashes()
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
argv = [*modules, '-q', '--tb=short', '--disable-warnings']
result = int(pytest.main(argv))
after = hashes()
record = {
    'source_base_head': head, 'source_status': 'All source and tests committed; before/after hashes identify the executed combined changes', 'cwd': str(Path.cwd()), 'python': sys.executable,
    'pytest_argv': argv, 'source_hashes_before': before, 'source_hashes_after': after,
    'source_unchanged': before == after, 'exit_code': result, **connections,
    'limits': 'Twelve affected synthetic-port unit modules, no live provider, database or browser acceptance.',
}
(folder / 'result.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
assert before == after and connections['prohibited_attempts'] == 0
raise SystemExit(result)
