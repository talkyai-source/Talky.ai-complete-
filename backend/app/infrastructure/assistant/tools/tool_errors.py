"""Customer-safe error text for assistant tools.

A tool's ``error`` is shown on the assistant's card and handed to the model.
An exception's own text may be a database driver's or a provider's (SQL
fragments, constraint names, row values), so it must not be passed through
unless the exception's exact class is known to carry a sentence written for
people. ``tool_error_text`` does that: listed classes pass through, everything
else is logged here (error level, with traceback) and replaced by a fixed
sentence. The layer that raised may have logged the failure too.
"""
from __future__ import annotations

import logging
from typing import Tuple, Type

from app.core.db import DATABASE_RETRYABLE_ERRORS

logger = logging.getLogger(__name__)

RETRY_HINT = " Try again in a moment."


def tool_error_text(
    exc: BaseException,
    fallback: str,
    *,
    tool: str,
    public: Tuple[Type[BaseException], ...] = (),
    retry_hint: bool = True,
) -> str:
    """Return the text a customer may see for ``exc`` raised in ``tool``.

    Matching on ``public`` is by EXACT type: subclasses are not trusted (asyncpg's
    ``DataError`` quotes query arguments; pydantic's ``ValidationError`` is a
    ``ValueError``). ``retry_hint=False`` is for a ``fallback`` that already says
    what to do next. Never raises, and never returns ``fallback`` without a log
    record.
    """
    readable = True
    try:
        if type(exc) in public:
            text = str(exc).strip()
            if text:
                logger.info(
                    "assistant_tool_refused tool=%s error_type=%s",
                    tool, type(exc).__name__,
                )
                return text
    except Exception:  # noqa: BLE001 - a message that cannot be read is not passed on
        readable = False
    try:
        logger.error(
            "assistant_tool_failed tool=%s error_type=%s",
            tool, type(exc).__name__,
            # the traceback formatter reads the message too: leave it out when that raises
            exc_info=(type(exc), exc, exc.__traceback__) if readable else None,
        )
        if retry_hint and isinstance(exc, DATABASE_RETRYABLE_ERRORS):
            return fallback + RETRY_HINT
    except Exception:  # noqa: BLE001 - an error path never raises
        pass
    return fallback
