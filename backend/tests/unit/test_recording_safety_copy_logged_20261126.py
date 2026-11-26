"""The log shows, once per call, that a recording safety copy was really written
(the folder is root-only on the server, so nobody can look at the disk)."""
import logging

from app.domain.services import recording_checkpoint as cp
from tests.unit.test_recording_checkpoint import FakeGw, chunk, env, make_session  # noqa: F401

LINE = "recording_checkpoint_first_copy"


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if LINE in r.getMessage()]


async def test_first_successful_copy_logs_one_line_with_seconds(env, caplog):  # noqa: F811
    caplog.set_level(logging.INFO)
    gw = FakeGw()
    env.live["pbx1"] = make_session("sess-1", gw)
    gw.caller += [chunk(16000, 1)]  # 16000 bytes at 8000 Hz 16-bit = 1.0 s
    assert await cp.checkpoint_pass() == 1
    lines = _lines(caplog)
    assert lines == [f"{LINE} call=sess-1 seconds=1.0"]


async def test_later_passes_add_no_new_line(env, caplog):  # noqa: F811
    caplog.set_level(logging.INFO)
    gw = FakeGw()
    env.live["pbx1"] = make_session("sess-1", gw)
    gw.caller += [chunk(1600, 1)]
    await cp.checkpoint_pass()
    gw.caller += [chunk(1600, 2)]
    await cp.checkpoint_pass()
    await cp.checkpoint_pass()
    assert len(_lines(caplog)) == 1


async def test_a_failed_write_logs_no_line_and_the_next_good_one_does(env, caplog, monkeypatch):  # noqa: F811
    caplog.set_level(logging.INFO)
    gw = FakeGw()
    env.live["pbx1"] = make_session("sess-1", gw)
    gw.caller += [chunk(1600, 1)]
    real = cp._write_files

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(cp, "_write_files", boom)
    assert await cp.checkpoint_pass() == 0
    assert _lines(caplog) == []
    monkeypatch.setattr(cp, "_write_files", real)
    assert await cp.checkpoint_pass() == 1
    assert len(_lines(caplog)) == 1


async def test_a_new_call_gets_its_own_line(env, caplog):  # noqa: F811
    caplog.set_level(logging.INFO)
    gw1, gw2 = FakeGw(), FakeGw()
    env.live["pbx1"] = make_session("sess-1", gw1)
    gw1.caller += [chunk(1600, 1)]
    await cp.checkpoint_pass()
    env.live["pbx2"] = make_session("sess-2", gw2)
    gw2.caller += [chunk(1600, 2)]
    await cp.checkpoint_pass()
    await cp.checkpoint_pass()
    lines = _lines(caplog)
    assert len(lines) == 2
    assert "call=sess-1 " in lines[0] and "call=sess-2 " in lines[1]


async def test_the_log_call_cannot_break_the_pass_or_the_write(env, monkeypatch):  # noqa: F811
    gw = FakeGw()
    env.live["pbx1"] = make_session("sess-1", gw)
    gw.caller += [chunk(1600, 1)]

    def bad_info(*a, **k):
        raise RuntimeError("logger broke")

    monkeypatch.setattr(cp.logger, "info", bad_info)
    assert await cp.checkpoint_pass() == 1
    assert (env.spool / "live" / "sess-1" / "caller.pcm").read_bytes() == chunk(1600, 1)


async def test_the_flag_goes_away_with_the_call_state(env, caplog):  # noqa: F811
    caplog.set_level(logging.INFO)
    gw = FakeGw()
    env.live["pbx1"] = make_session("sess-1", gw)
    gw.caller += [chunk(1600, 1)]
    await cp.checkpoint_pass()
    cp.checkpoint_done("sess-1")
    assert cp._STATE == {}
