"""B1: private deployment logs must not make application source private."""
import os
import shutil
import stat
import subprocess

import pytest

from tests.unit import test_bh_ops_scripts_deploy as ops

captured_remote_script = ops.captured_remote_script


def test_checkout_preserves_inherited_umask(tmp_path, captured_remote_script, monkeypatch):
    monkeypatch.setenv("TALKY_PROD_HOST", "nobody@example.invalid")
    monkeypatch.setenv("TALKY_PROD_KEY", "synthetic")
    monkeypatch.setitem(ops.STUBS, "git", ops.STUBS["git"].replace(
        'echo "git $*" >> "$LOG"',
        'echo "git $*" >> "$LOG"\n'
        'if [ "$1" = checkout ]; then echo "checkout-umask $(umask)" >> "$LOG"; fi',
        1,
    ))
    result, log, _ = ops._run_remote(tmp_path, "umask 022\n" + captured_remote_script)
    assert result.returncode == 0, result.stdout + result.stderr + result.deploy_log
    modes = [line.split()[1].lstrip("0") or "0" for line in log if line.startswith("checkout-umask ")]
    assert modes == ["22"], "The deploy changed the inherited checkout umask"


@pytest.mark.skipif(os.name != "posix", reason="Linux/POSIX permission bits; Windows ACLs are not equivalent")
def test_real_checkout_source_is_group_and_world_readable(tmp_path, captured_remote_script, monkeypatch):
    monkeypatch.setenv("TALKY_PROD_HOST", "nobody@example.invalid")
    monkeypatch.setenv("TALKY_PROD_KEY", "synthetic")
    repo = tmp_path / "mode-repo"
    repo.mkdir()
    real_git = shutil.which("git")

    def git(*args):
        return subprocess.check_output(
            [real_git, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", *args],
            cwd=repo, text=True, stderr=subprocess.PIPE,
        ).strip()

    git("init", "-q")
    source = repo / "application.py"
    source.write_text("VERSION = 1\n", encoding="utf-8")
    git("add", "application.py")
    git("commit", "-qm", "old")
    previous = git("rev-parse", "HEAD")
    source.write_text("VERSION = 2\n", encoding="utf-8")
    git("commit", "-qam", "candidate")
    target = git("rev-parse", "HEAD")
    git("checkout", "--detach", previous)
    monkeypatch.setitem(ops.STUBS, "git", ops.STUBS["git"].replace(
        'echo "git $*" >> "$LOG"',
        'echo "git $*" >> "$LOG"\n'
        'if [ "$1" = checkout ]; then "$REAL_GIT" -C "$MODE_REPO" checkout --detach "$MODE_TARGET" || exit; fi',
        1,
    ))
    result, _, _ = ops._run_remote(
        tmp_path, "umask 022\n" + captured_remote_script,
        REAL_GIT=real_git, MODE_REPO=str(repo), MODE_TARGET=target,
    )
    assert result.returncode == 0, result.deploy_log
    assert source.read_text(encoding="utf-8") == "VERSION = 2\n"
    assert stat.S_IMODE(source.stat().st_mode) & 0o044 == 0o044
