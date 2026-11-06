"""Spreadsheet-safe CSV cells (QA F14-01).

A text cell that starts with = + - @, a tab or a carriage return is run as a
formula when the file is opened in Excel, Sheets or LibreOffice (CSV
injection), so a tenant-chosen business name or note could execute in staff
spreadsheets. A leading single quote keeps such text as text. Numbers and
None pass through unchanged.
"""
from __future__ import annotations

from typing import Any

_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value
