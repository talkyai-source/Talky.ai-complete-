"""Small helpers for redacting secrets out of log lines.

Several startup-time log statements print the full Redis connection URL
(``redis://:PASSWORD@host:port/db``) for diagnostics. That's useful for
confirming *which* host/db a service connected to, but leaking the password
into journald/log aggregators is an unnecessary credential-exposure surface.

``redact_redis_url`` keeps the host/port/db visible and masks only the
password segment, e.g.::

    redis://:s3cr3t@10.0.0.5:6379/0  ->  redis://:****@10.0.0.5:6379/0
    redis://10.0.0.5:6379/0          ->  redis://10.0.0.5:6379/0   (no auth, unchanged)

This only changes what gets logged — it must never be used for the URL a
client actually connects with.
"""
from __future__ import annotations

import re

# Matches the userinfo portion of a redis/rediss URL: optional "user", a
# colon, then the password, up to the "@". Handles both
# "redis://:password@host" (no username) and "redis://user:password@host".
_REDIS_URL_AUTH_RE = re.compile(r"(?P<scheme>rediss?://)(?P<userinfo>[^@/]*:[^@/]*)@")


def redact_redis_url(url: str) -> str:
    """Return ``url`` with any embedded password replaced by ``****``.

    Safe to call on a URL with no credentials (returned unchanged) or on a
    non-Redis string (returned unchanged — the regex simply won't match).
    """
    if not url:
        return url

    def _mask(match: "re.Match[str]") -> str:
        userinfo = match.group("userinfo")
        if ":" not in userinfo:
            # No password segment (just a bare username) — nothing to mask.
            return match.group(0)
        user, _, _password = userinfo.partition(":")
        return f"{match.group('scheme')}{user}:****@"

    return _REDIS_URL_AUTH_RE.sub(_mask, url)
