"""Configured silence deadline, without generated dialogue."""
import pytest
from app.domain.services.voice_pipeline.audio_ingest import silence_action

@pytest.mark.parametrize("elapsed,grace,limit,expected", [
    (0, False, 60, "wait"), (2.5, False, 60, "wait"),
    (16, False, 60, "wait"), (59.9, False, 60, "wait"),
    (60, False, 60, "hangup"), (90, True, 60, "wait"),
    (10, False, 10, "hangup"), (9.9, False, 10, "wait"),
])
def test_deadline_only(elapsed, grace, limit, expected):
    assert silence_action(caller_silence_s=elapsed, in_grace=grace, hangup_s=limit) == expected
