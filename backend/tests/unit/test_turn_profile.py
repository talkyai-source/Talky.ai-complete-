import hashlib
import json
from types import SimpleNamespace

from app.domain.services.voice_pipeline.profile import turn_profile


def test_effective_prompt_and_knowledge_version_are_private_and_change_with_content():
    session = SimpleNamespace(knowledge_mode="retrieve", _knowledge_snapshot_checksum="a" * 64,
        _knowledge_evidence={"status": "matched", "passages": [
            {"node_id": "private-node", "version": 7, "text": "private customer facts"}]})
    profile = turn_profile(session, "full dynamic instructions", "private customer facts")
    assert profile["instructions_sha256"] == hashlib.sha256(b"full dynamic instructions").hexdigest()
    assert profile["knowledge_version_status"] == "snapshot"
    assert "private" not in json.dumps(profile)
    assert "dynamic instructions" not in json.dumps(profile)
    changed = turn_profile(session, "changed full instructions", "updated private facts")
    assert changed["instructions_sha256"] != profile["instructions_sha256"]
    assert changed["knowledge_block_sha256"] != profile["knowledge_block_sha256"]
    session._knowledge_evidence["passages"][0]["version"] = 8
    assert turn_profile(session, "same", "same")["knowledge_versions_sha256"] != profile["knowledge_versions_sha256"]


def test_unversioned_and_stale_knowledge_are_not_claimed_as_current():
    session = SimpleNamespace(knowledge_mode="retrieve", _knowledge_snapshot_checksum="private text",
        _knowledge_evidence={"status": "matched", "passages": [{"text": "previous turn"}]})
    current = turn_profile(session, "instructions", "unversioned source")
    assert current["knowledge_version_status"] == "unversioned"
    assert current["knowledge_snapshot_checksum"] is None
    skipped = turn_profile(session, "instructions", None)
    assert skipped["knowledge_status"] == "not_retrieved_this_turn"
    assert skipped["knowledge_passage_count"] == 0
