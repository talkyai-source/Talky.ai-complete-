"""A release must retain secrets and identify the processes it actually starts."""
import importlib.util
import os
from pathlib import Path

import pytest
from dotenv import dotenv_values


SPEC = importlib.util.spec_from_file_location(
    "set_release_version", Path(__file__).parents[2] / "scripts/set_release_version.py")
version = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(version)
SHA = "a" * 40


def test_multiline_and_commented_other_values_survive(tmp_path):
    env = tmp_path / ".env"
    original = b"# preserved\r\nSECRET='first\nAPP_VERSION=inside-secret\nlast'\r\nCORS='[\"example\"]'\r\n"
    env.write_bytes(original)
    before = dotenv_values(env, interpolate=False)
    version.publish_version(env, SHA)
    assert env.read_bytes() == original + f"APP_VERSION={SHA}\n".encode()
    assert dotenv_values(env, interpolate=False) == {**before, "APP_VERSION": SHA}


def test_duplicate_exported_versions_become_one_exact_binding(tmp_path):
    env = tmp_path / ".env"
    env.write_text("export APP_VERSION='old'\nTOKEN=private\nAPP_VERSION=stale\n", encoding="utf-8")
    version.publish_version(env, SHA)
    assert env.read_text(encoding="utf-8") == f"APP_VERSION={SHA}\nTOKEN=private\n"
    original_stat = env.stat()
    version.publish_version(env, SHA)
    assert env.stat().st_mtime_ns == original_stat.st_mtime_ns


@pytest.mark.parametrize("contents", [b"SECRET='unterminated", b"KEY=\xff"])
def test_invalid_input_never_replaces_original(tmp_path, contents):
    env = tmp_path / ".env"
    env.write_bytes(contents)
    with pytest.raises((ValueError, UnicodeError)):
        version.publish_version(env, SHA)
    assert env.read_bytes() == contents
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


def test_replace_failure_keeps_original_and_removes_temporary(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_bytes(b"TOKEN=private\n")
    def fail(*args):
        raise OSError("unavailable")
    monkeypatch.setattr(version.os, "replace", fail)
    with pytest.raises(OSError):
        version.publish_version(env, SHA)
    assert env.read_bytes() == b"TOKEN=private\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


@pytest.mark.skipif(os.name != "posix", reason="Unix ownership/modes")
def test_mode_and_owner_are_preserved_and_symlink_is_refused(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(b"TOKEN=private\n")
    env.chmod(0o600)
    before = env.stat()
    version.publish_version(env, SHA)
    after = env.stat()
    assert (after.st_uid, after.st_gid, after.st_mode) == (before.st_uid, before.st_gid, before.st_mode)
    link = tmp_path / "linked.env"
    link.symlink_to(env)
    with pytest.raises(ValueError):
        version.publish_version(link, SHA)


@pytest.mark.parametrize("value", [b"APP_VERSION=old\0SECRET=private", b"SECRET=private", b"APP_VERSION=" + SHA.encode() + b"\0APP_VERSION=old"])
def test_wrong_absent_or_duplicate_process_version_fails(tmp_path, value):
    proc = tmp_path / "42"
    proc.mkdir()
    (proc / "environ").write_bytes(value)
    with pytest.raises(RuntimeError):
        version.check_process_version(42, SHA, tmp_path)


def test_exact_started_process_version_passes(tmp_path):
    proc = tmp_path / "42"
    proc.mkdir()
    (proc / "environ").write_bytes(b"SECRET=private\0APP_VERSION=" + SHA.encode() + b"\0")
    version.check_process_version(42, SHA, tmp_path)
