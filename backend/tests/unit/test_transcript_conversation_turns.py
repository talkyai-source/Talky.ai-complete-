"""The transcript shows one line per spoken turn, not every STT partial.

2026-09-28, call b847f447 (Dojo-PC → 940007): the call page showed
"Yeah. It is" twice and "Yeah. It is right." four times, and 132 rows for a
24-turn call, because every interim recognition was stored as a caller row.
"""
from app.api.v1.endpoints import calls as calls_endpoint
from app.domain.services.transcript_service import TranscriptService, conversation_turns


def _u(text, turn, final):
    return {"role": "user", "content": text, "turn_index": turn, "is_final": final}


def _a(text, turn):
    return {"role": "assistant", "content": text, "turn_index": turn, "is_final": True}


B847_TAIL = [
    _a("So that's plus 9 2 3 1 2 0 7 5 0 4 9 6 — did I get that right?", 9),
    _u("Yeah. It is", 9, False),
    _u("Yeah. It is", 9, False),
    _u("Yeah. It is right.", 9, False),
    _u("Yeah. It is right.", 9, True),
    _u("Yeah. It is right.", 9, False),
    _u("Yeah. It is right.", 9, False),
    _a("Thanks. I'll pass on what you've told me.", 10),
]


def test_partials_collapse_to_the_final_line():
    out = conversation_turns(B847_TAIL)
    assert [(t["role"], t["content"]) for t in out] == [
        ("assistant", "So that's plus 9 2 3 1 2 0 7 5 0 4 9 6 — did I get that right?"),
        ("user", "Yeah. It is right."),
        ("assistant", "Thanks. I'll pass on what you've told me."),
    ]


def test_a_line_that_never_finalised_keeps_its_latest_partial():
    """The caller hangs up mid-sentence: nothing finalises, keep what was heard."""
    out = conversation_turns([_a("Anything else?", 3), _u("No", 3, False), _u("No thank", 3, False)])
    assert [t["content"] for t in out] == ["Anything else?", "No thank"]


def test_separate_caller_turns_stay_separate():
    out = conversation_turns([_u("Hello?", 0, True), _u("Hello?", 1, True), _u("Still there?", 2, True)])
    # Two identical consecutive lines collapse; a different line stays.
    assert [t["content"] for t in out] == ["Hello?", "Still there?"]


def test_older_rows_without_is_final_are_kept():
    rows = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert conversation_turns(rows) == rows


def test_garbage_is_ignored():
    assert conversation_turns(None) == []
    assert conversation_turns(["x", None, _u("ok", 1, True)]) == [_u("ok", 1, True)]


def test_the_saved_transcript_json_is_collapsed():
    svc = TranscriptService()
    call = "conv-turns-test"
    svc.clear_buffer(call)
    try:
        svc.accumulate_turn(call, "user", "It's plus", turn_index=4, is_final=False, include_in_plaintext=False)
        svc.accumulate_turn(call, "user", "It's plus nine two", turn_index=4, is_final=True)
        svc.accumulate_turn(call, "assistant", "Got it.", turn_index=4, is_final=True)
        assert [t["content"] for t in svc.get_transcript_json(call)] == ["It's plus nine two", "Got it."]
    finally:
        svc.clear_buffer(call)


async def test_the_transcript_endpoint_collapses_stored_rows(monkeypatch):
    """Call the actual endpoint against a legacy structured snapshot."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    conn = SimpleNamespace(fetchrow=AsyncMock(return_value={
        "transcript": "obsolete flat representation", "transcript_json": B847_TAIL,
        "transcript_save_state": "unknown", "updated_at": None,
    }))
    @asynccontextmanager
    async def owned(*args, **kwargs):
        yield conn
    monkeypatch.setattr(calls_endpoint, "acquire_with_tenant", owned)
    result = await calls_endpoint.get_call_transcript(
        str(uuid4()), format="json", current_user=SimpleNamespace(tenant_id=str(uuid4())),
        db_client=SimpleNamespace(pool=object()),
    )
    assert result["turns"] == conversation_turns(B847_TAIL)
    assert result["transcript_save_state"] == "unknown"
    assert result["metadata"]["turn_count"] == 3
