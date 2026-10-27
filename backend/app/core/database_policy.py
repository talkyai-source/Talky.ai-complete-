"""Database connection policy: verified TLS off-host, a restricted runtime role.

Production deliverable 4 moves PostgreSQL off the application server. Two
rules keep that move safe, and both are no-ops for today's loopback database:

1. A DATABASE_URL / READ_DATABASE_URL that leaves this host must verify TLS
   (``sslmode=verify-full`` or ``verify-ca``). asyncpg's default is
   ``prefer``: it silently falls back to plaintext and never checks the
   server certificate. Loopback and Unix-socket URLs never cross a network
   and are exempt.
2. The runtime role must not be able to step around tenant isolation or the
   schema: no SUPERUSER, BYPASSRLS, CREATEROLE or CREATEDB, and no ownership
   of application relations (an owner can ALTER/DROP its tables and switch
   their row-level security off). The posture is logged on every pool start
   and refused when DATABASE_REQUIRE_RESTRICTED_ROLE is true, which the
   cut-over to the separate database sets.

Messages name the variable and host only, never the URL (it holds the
password).
"""
from __future__ import annotations

import ipaddress
import logging
import os
from typing import Mapping, Optional
from urllib.parse import parse_qsl, urlsplit

logger = logging.getLogger(__name__)

URL_VARIABLES = ("DATABASE_URL", "READ_DATABASE_URL")
VERIFIED_SSLMODES = frozenset({"verify-full", "verify-ca"})
_TRUE = frozenset({"1", "true", "yes", "on"})


class DatabasePolicyError(RuntimeError):
    """The database connection would violate the production policy."""


def _is_production(environ: Mapping[str, str]) -> bool:
    return (environ.get("ENVIRONMENT", "development") or "").strip().lower() == "production"


def restricted_role_required(environ: Mapping[str, str] = os.environ) -> bool:
    return (environ.get("DATABASE_REQUIRE_RESTRICTED_ROLE", "") or "").strip().lower() in _TRUE


def _is_local_host(host: str) -> bool:
    if not host or host.startswith("/") or host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _hosts(parsed, query: dict[str, str]) -> list[str]:
    # A ``host=`` query option overrides the authority (libpq/asyncpg rule);
    # either may list several comma-separated hosts. No host means the
    # default Unix socket.
    if query.get("host") is not None:
        return [host.strip() for host in query["host"].split(",")]
    hosts = []
    for part in parsed.netloc.rpartition("@")[2].split(","):
        part = part.strip()
        if part.startswith("["):
            hosts.append(part[1:part.find("]")] if "]" in part else part[1:])
        else:
            hosts.append(part.split(":", 1)[0])
    return hosts


def url_problems(name: str, url: str, environ: Mapping[str, str] = os.environ) -> list[str]:
    """Problems with one connection URL; never echoes the URL itself."""
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return [f"{name} is not a valid PostgreSQL URL"]
    scheme = parsed.scheme.split("+", 1)[0].lower()
    if scheme not in {"postgres", "postgresql"}:
        return [f"{name} must be a postgresql:// URL"]
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    remote = [host for host in _hosts(parsed, query) if not _is_local_host(host)]
    if not remote:
        return []
    sslmode = (query.get("sslmode") or environ.get("PGSSLMODE") or "prefer").strip().lower()
    if sslmode in VERIFIED_SSLMODES:
        return []
    return [
        f"{name} connects to {', '.join(remote)} (off this host) with sslmode={sslmode}; "
        "a remote database must verify TLS: add ?sslmode=verify-full&sslrootcert=<CA file>"
    ]


def database_url_problems(environ: Mapping[str, str] = os.environ) -> list[str]:
    problems: list[str] = []
    if _is_production(environ) and not (environ.get("DATABASE_URL") or "").strip():
        problems.append(
            "DATABASE_URL is not set; the pool would fall back to the development "
            "default on localhost"
        )
    for name in URL_VARIABLES:
        url = (environ.get(name) or "").strip()
        if url:
            problems.extend(url_problems(name, url, environ))
    return problems


def enforce_database_url_policy(environ: Mapping[str, str] = os.environ) -> None:
    """Refuse an unverified off-host connection in production; warn elsewhere."""
    problems = database_url_problems(environ)
    if not problems:
        return
    if _is_production(environ):
        raise DatabasePolicyError("Database connection refused: " + "; ".join(problems))
    for problem in problems:
        logger.warning("db_url_policy_warning %s", problem)


POSTURE_SQL = """
SELECT r.rolname AS role, r.rolsuper AS superuser, r.rolbypassrls AS bypass_rls,
       r.rolcreaterole AS create_role, r.rolcreatedb AS create_database,
       (SELECT count(*) FROM pg_catalog.pg_class c
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relowner = r.oid
           AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
           AND n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND n.nspname NOT LIKE 'pg_toast%') AS owned_relations,
       COALESCE((SELECT s.ssl FROM pg_catalog.pg_stat_ssl s
                  WHERE s.pid = pg_catalog.pg_backend_pid()), false) AS tls
  FROM pg_catalog.pg_roles r
 WHERE r.rolname = current_user
"""


def unsafe_reasons(posture: Mapping[str, object]) -> list[str]:
    reasons = [attribute for attribute in ("superuser", "bypass_rls", "create_role", "create_database")
               if posture.get(attribute)]
    if int(posture.get("owned_relations") or 0) > 0:
        reasons.append(f"owns {posture['owned_relations']} relations")
    return reasons


async def enforce_runtime_posture(pool, *, pool_name: str,
                                  environ: Mapping[str, str] = os.environ) -> Optional[dict]:
    """Log the runtime role's capabilities; refuse an unrestricted role when required."""
    required = restricted_role_required(environ)
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(POSTURE_SQL)
    except Exception as exc:  # the posture probe must never be what breaks boot
        if required:
            raise DatabasePolicyError(
                f"could not verify the {pool_name} database role ({type(exc).__name__})"
            ) from None
        logger.warning("db_runtime_posture_unavailable pool=%s error=%s", pool_name, type(exc).__name__)
        return None
    posture = dict(row) if row else {}
    reasons = unsafe_reasons(posture) if posture else ["role not found"]
    logger.info(
        "db_runtime_posture pool=%s role=%s superuser=%s bypass_rls=%s create_role=%s "
        "create_database=%s owned_relations=%s tls=%s restricted=%s required=%s",
        pool_name, posture.get("role"), posture.get("superuser"), posture.get("bypass_rls"),
        posture.get("create_role"), posture.get("create_database"),
        posture.get("owned_relations"), posture.get("tls"), not reasons, required,
    )
    if reasons:
        detail = f"{pool_name} database role {posture.get('role')!r} is not restricted: " + ", ".join(reasons)
        if required:
            raise DatabasePolicyError(detail + " (DATABASE_REQUIRE_RESTRICTED_ROLE=true)")
        logger.warning(
            "db_runtime_role_not_restricted %s; use the restricted runtime role "
            "(scripts/database/roles.py) and set DATABASE_REQUIRE_RESTRICTED_ROLE=true", detail,
        )
    return posture
