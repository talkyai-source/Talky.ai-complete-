"""Customer-safe error text for assistant tools.

A tool's ``error`` is shown on the assistant's card and handed to the model.
An exception's own text may be a database driver's or a provider's (SQL
fragments, constraint names, row values), so it must not be passed through
unless the exception's exact class is known to carry a sentence written for
people. ``tool_error_text`` does that: listed classes pass through, everything
else is logged once (error level, with traceback) and replaced by a fixed
sentence.
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
) -> str:
    """Return the text a customer may see for ``exc`` raised in ``tool``.

    Matching on ``public`` is by EXACT type: subclasses are not trusted (asyncpg's
    ``DataError`` is a ``ValueError`` and quotes query arguments). Never raises.
    """
    try:
        if type(exc) in public:
            text = str(exc).strip()
            if text:
                logger.info(
                    "assistant_tool_refused tool=%s error_type=%s",
                    tool, type(exc).__name__,
                )
                return text
        logger.error(
            "assistant_tool_failed tool=%s error_type=%s",
            tool, type(exc).__name__,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        if isinstance(exc, DATABASE_RETRYABLE_ERRORS):
            return fallback + RETRY_HINT
    except Exception:  # noqa: BLE001 - an error path never raises
        pass
    return fallback
