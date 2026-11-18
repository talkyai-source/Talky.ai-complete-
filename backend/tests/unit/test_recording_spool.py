"""The durable pending folder for call recordings (no database code)."""
import os
import sys

import pytest

from app.domain.services import recording_spool as spool


@pytest.fixture(autouse=True)
def _dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path / "pending"))
    return tmp_path / "pending"


def _entry(key="call-1", **kw):
    base = dict(
        key=key, pbx_call_id="pbx-1", session_call_id=key, tenant_id=None,
        campaign_id=None, phone_hint=None, sample_rate=16000, duration_seconds=1.5,
        disclosure_state="transmitted", callee_country="PK", callee_country_known=True,
        inbound=False, strict_notice=False, awaiting=None,
        transfer_recording_name=None, transfer_part=None, created_at=spool.utc_now_iso(),
    )
    base.update(kw)
    return spool.SpoolEntry(**base)


def test_default_dir_is_pending_inside_local_recordings_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("RECORDING_SPOOL_DIR")
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(tmp_path / "recs"))
    assert spool.spool_dir() == os.path.join(os.path.abspath(str(tmp_path / "recs")), ".pending")


def test_round_trip():
    entry = _entry()
    spool.write_entry(entry, b"RIFFdata")
    assert spool.read_wav("call-1") == b"RIFFdata"
    assert spool.read_entry("call-1") == entry
    assert spool.list_entries() == [entry]
    entry.attempts = 3
    entry.last_error = "x"
    spool.update_entry(entry)
    assert spool.read_entry("call-1").attempts == 3
    assert spool.read_wav("call-1") == b"RIFFdata"


@pytest.mark.parametrize("key", ["", "../x", "a/b", "a b", "x" * 81, "a.b"])
def test_bad_key_refused(key):
    with pytest.raises(ValueError):
        spool.write_entry(_entry(key=key), b"x")
    with pytest.raises(ValueError):
        spool.remove_entry(key)


def test_wav_without_json_is_not_listed_and_tmp_is_ignored(_dir):
    _dir.mkdir()
    (_dir / "orphan.wav").write_bytes(b"x")
    (_dir / "half.wav.tmp").write_bytes(b"x")
    (_dir / "half.json.tmp").write_bytes(b"{}")
    (_dir / "broken.json").write_text("{not json")
    (_dir / "nowav.json").write_text("{}")
    assert spool.list_entries() == []
    assert spool.pending_summary()["count"] == 0


def test_json_without_wav_is_not_listed():
    entry = _entry()
    spool.write_entry(entry, b"x")
    os.unlink(os.path.join(spool.spool_dir(), "call-1.wav"))
    assert spool.list_entries() == []


def test_remove_entry_twice_is_fine():
    spool.write_entry(_entry(), b"x")
    spool.remove_entry("call-1")
    spool.remove_entry("call-1")
    assert spool.list_entries() == []
    assert spool.read_wav("call-1") is None
    assert spool.read_entry("call-1") is None


def test_pending_summary_counts_and_never_raises(monkeypatch):
    assert spool.pending_summary() == {"count": 0, "oldest_age_seconds": 0.0, "awaiting": 0}
    spool.write_entry(_entry("a"), b"x")
    spool.write_entry(_entry("b", awaiting="transfer_part"), b"x")
    summary = spool.pending_summary()
    assert summary["count"] == 2 and summary["awaiting"] == 1
    assert summary["oldest_age_seconds"] >= 0

    def boom():
        raise PermissionError("secret path")

    monkeypatch.setattr(spool, "list_entries", boom)
    assert spool.pending_summary() == {"count": None, "error": "PermissionError"}


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX modes only")
def test_files_are_private():
    spool.write_entry(_entry(), b"x")
    base = spool.spool_dir()
    for name in ("call-1.wav", "call-1.json"):
        assert os.stat(os.path.join(base, name)).st_mode & 0o777 == 0o600
    assert os.stat(base).st_mode & 0o777 == 0o700
