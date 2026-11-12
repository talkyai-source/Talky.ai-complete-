"""Formatting helpers for the support bot tools.

The tools' read-only SQL lives in ``diagnostics.py``, beside the executor that runs it.
"""
from __future__ import annotations

from typing import Any


def mask_phone(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return f"***{digits[-4:]}" if digits else "***"


def iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (None if value is None else str(value))
