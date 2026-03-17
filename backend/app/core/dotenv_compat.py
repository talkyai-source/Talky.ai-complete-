"""
Compatibility wrapper for optional python-dotenv usage.
"""
from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv as _load_dotenv  # type: ignore[import-not-found]
except ModuleNotFoundError:
    _load_dotenv = None


def load_dotenv(
    dotenv_path: str | os.PathLike[str] | None = None,
    override: bool = False,
    encoding: str = "utf-8",
    **_: Any,
) -> bool:
    """
    Load key/value pairs from a .env file.

    If python-dotenv is installed, defer to it. Otherwise use a small fallback
    parser so local development does not fail on import.
    """
    if _load_dotenv is not None:
        return bool(
            _load_dotenv(
                dotenv_path=dotenv_path,
                override=override,
                encoding=encoding,
            )
        )

    path = _resolve_dotenv_path(dotenv_path)
    if path is None or not path.is_file():
        return False

    loaded = False
    for raw_line in path.read_text(encoding=encoding).splitlines():
        parsed = _parse_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        if override or key not in os.environ:
            os.environ[key] = value
            loaded = True
    return loaded


def _resolve_dotenv_path(dotenv_path: str | os.PathLike[str] | None) -> Path | None:
    if dotenv_path is not None:
        return Path(dotenv_path).expanduser()

    checked: set[Path] = set()
    for root in _candidate_roots():
        for directory in (root, *root.parents):
            candidate = directory / ".env"
            if candidate in checked:
                continue
            checked.add(candidate)
            if candidate.is_file():
                return candidate
    return None


def _candidate_roots() -> list[Path]:
    roots = [Path.cwd().resolve()]
    stack = inspect.stack()
    try:
        for frame_info in stack[1:4]:
            filename = frame_info.filename
            if filename:
                roots.append(Path(filename).resolve().parent)
    finally:
        del stack

    unique_roots: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root not in seen:
            seen.add(root)
            unique_roots.append(root)
    return unique_roots


def _parse_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    if stripped.startswith("export "):
        stripped = stripped[7:].lstrip()

    if "=" not in stripped:
        return None

    key, raw_value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None

    return key, _parse_value(raw_value.strip())


def _parse_value(raw_value: str) -> str:
    if not raw_value:
        return ""

    if raw_value[0] in {"'", '"'} and raw_value[-1] == raw_value[0]:
        value = raw_value[1:-1]
        if raw_value[0] == '"':
            return bytes(value, "utf-8").decode("unicode_escape")
        return value

    if " #" in raw_value:
        raw_value = raw_value.split(" #", 1)[0].rstrip()

    return raw_value
