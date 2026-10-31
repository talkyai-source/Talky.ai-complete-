#!/usr/bin/env python3
"""Publish one immutable release version, or check a started process's version.

Only APP_VERSION is changed. Other environment bindings are retained verbatim;
errors never print environment contents. The caller controls service restart.
"""
from __future__ import annotations

import argparse
import io
import os
from pathlib import Path
import re
import stat
import tempfile

from dotenv.parser import parse_stream


def validate_sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        raise ValueError("a full lowercase commit SHA is required")
    return value


def versioned_environment(original: str, candidate_sha: str) -> str:
    value = validate_sha(candidate_sha)
    parts: list[str] = []
    found = False
    for binding in parse_stream(io.StringIO(original)):
        if binding.error:
            raise ValueError("environment file has an invalid binding")
        if binding.key == "APP_VERSION":
            if not found:
                parts.append(f"APP_VERSION={value}\n")
                found = True
        else:
            parts.append(binding.original.string)
    rendered = "".join(parts)
    if not found:
        if rendered and not rendered.endswith(("\n", "\r")):
            rendered += "\n"
        rendered += f"APP_VERSION={value}\n"
    return rendered


def publish_version(env_file: Path, candidate_sha: str) -> None:
    validate_sha(candidate_sha)
    metadata = env_file.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("environment path must be an existing regular file")
    original = env_file.read_bytes()
    replacement = versioned_environment(original.decode("utf-8"), candidate_sha).encode("utf-8")
    if original == replacement:
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix=".talky-version-", dir=env_file.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            if hasattr(os, "fchown"):
                os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        current = env_file.lstat()
        if (current.st_dev, current.st_ino, current.st_mtime_ns, current.st_size) != (
            metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns, metadata.st_size
        ) or env_file.read_bytes() != original:
            raise RuntimeError("environment changed during release preparation")
        os.replace(temporary, env_file)
    finally:
        temporary.unlink(missing_ok=True)


def check_process_version(pid: int, candidate_sha: str, proc_root: Path = Path("/proc")) -> None:
    validate_sha(candidate_sha)
    if pid <= 0:
        raise ValueError("service has no running process")
    values = [entry.split(b"=", 1)[1] for entry in (proc_root / str(pid) / "environ").read_bytes().split(b"\0")
              if entry.startswith(b"APP_VERSION=")]
    if values != [candidate_sha.encode("ascii")]:
        raise RuntimeError("running service version does not match the release")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-sha", required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--env-file", type=Path)
    target.add_argument("--check-pid", type=int)
    args = parser.parse_args()
    try:
        if args.env_file is not None:
            publish_version(args.env_file, args.candidate_sha)
        else:
            check_process_version(args.check_pid, args.candidate_sha)
    except (OSError, ValueError, RuntimeError, UnicodeError) as exc:
        print(f"RELEASE VERSION FAILED: {type(exc).__name__}")
        return 1
    print("RELEASE VERSION VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
