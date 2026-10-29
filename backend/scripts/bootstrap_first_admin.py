#!/usr/bin/env python3
"""Provision one new platform administrator with proved MFA; never promote/reset.

See docs/operations/FIRST-ADMIN.md. No provider clients or application lifespan
are started. Credentials come from protected files and non-echoing TTY prompts.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import getpass
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4
import warnings

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


class BootstrapError(ValueError):
    """Safe operator message: never include driver errors or submitted values."""


@dataclass(frozen=True, repr=False)
class Credentials:
    email: str
    name: str
    password: str
    totp_secret: str
    totp_code: str
    owned_email_confirmed: bool


def read_private_file(path: Path, *, allow_group_read=False) -> str:
    if os.name != "posix":
        raise BootstrapError("Use this operator command on Linux with Unix file permissions.")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            details = os.fstat(handle.fileno())
            forbidden = 0o027 if allow_group_read else 0o077
            if (not stat.S_ISREG(details.st_mode) or details.st_uid not in {0, os.geteuid()}
                    or stat.S_IMODE(details.st_mode) & forbidden):
                raise BootstrapError("Input must be an owned regular file with protected permissions.")
            content = handle.read(262145)
            if len(content) > 262144:
                raise BootstrapError("Protected input file is too large.")
            return content
    except BootstrapError:
        raise
    except (OSError, UnicodeError):
        raise BootstrapError("Cannot safely read protected input file.") from None


def validate_database_url(dsn: str) -> None:
    try:
        parsed = urlsplit(dsn)
        query = parse_qs(parsed.query, strict_parsing=True)
        valid = (
            parsed.scheme in {"postgres", "postgresql"}
            and parsed.hostname and not parsed.hostname.startswith("/")
            and parsed.port and parsed.username and parsed.path not in {"", "/"}
            and not parsed.fragment
            and set(query) <= {"sslmode", "sslrootcert", "passfile"}
            and all(len(values) == 1 for values in query.values())
            and query.get("sslmode") == ["verify-full"]
            and Path(query.get("sslrootcert", [""])[0]).is_absolute()
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise BootstrapError("Database URL requires explicit host, port, user, database, verify-full and absolute CA path; unsupported options are refused.")


def expected_heads() -> set[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    config = Config()
    config.set_main_option("script_location", str(BACKEND / "Alembic"))
    return set(ScriptDirectory.from_config(config).get_heads())


async def check_target(conn, *, database: str, owner: str) -> None:
    row = await conn.fetchrow("""SELECT current_database() AS database, current_user AS username,
        session_user AS login, r.rolcanlogin, r.rolreplication,
        r.rolsuper, r.rolbypassrls, r.rolcreatedb, r.rolcreaterole,
        EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) AS memberships,
        (SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()) AS tls,
        (SELECT pg_get_userbyid(relowner) FROM pg_class
         WHERE oid='public.user_profiles'::regclass) AS table_owner
        FROM pg_roles r WHERE rolname=current_user""")
    if not row or row["database"] != database or row["username"] != owner or row["login"] != owner:
        raise BootstrapError("Database or login role does not match the explicit operator target.")
    if not row["rolcanlogin"] or row["table_owner"] != owner or any(row[key] for key in
            ("rolsuper", "rolbypassrls", "rolcreatedb", "rolcreaterole", "rolreplication", "memberships")):
        raise BootstrapError("Use the restricted migration owner, not a runtime or privileged database role.")
    if not row["tls"]:
        raise BootstrapError("The database connection must use verified TLS.")
    heads = {record["version_num"] for record in await conn.fetch("SELECT version_num FROM public.alembic_version")}
    if heads != expected_heads():
        raise BootstrapError("Database migrations do not match this checkout's migration heads.")


async def check_available(conn, email: str) -> None:
    # Call under app.bypass_rls so FORCE RLS cannot hide an existing administrator.
    if await conn.fetchval("SELECT EXISTS(SELECT 1 FROM public.user_profiles WHERE role='platform_admin')"):
        raise BootstrapError("A platform administrator already exists, including possibly inactive accounts; use the existing recovery or Admin workflow.")
    if await conn.fetchval("SELECT EXISTS(SELECT 1 FROM public.user_profiles WHERE lower(email)=lower($1))", email):
        raise BootstrapError("This email already belongs to an account; bootstrap never promotes or resets existing users.")


def prepare_credentials(credentials: Credentials):
    from email_validator import EmailNotValidError, validate_email
    from app.core.security.password import hash_password, validate_password_strength
    from app.core.security.totp import encrypt_totp_secret, verify_totp_step
    if not credentials.owned_email_confirmed:
        raise BootstrapError("Explicit confirmation of the operator's independently verified mailbox ownership is required.")
    try:
        email = validate_email(credentials.email, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        raise BootstrapError("A valid administrator email is required.") from None
    if len(email) > 255 or not 1 <= len(credentials.name.strip()) <= 255:
        raise BootstrapError("Administrator name/email length is invalid.")
    try:
        validate_password_strength(credentials.password)
    except ValueError:
        raise BootstrapError("Password must satisfy the application password policy.") from None
    # Fresh secret must have at least the same 160-bit entropy capacity as the
    # normal enrolment generator. Randomness itself is an operator requirement.
    if not re.fullmatch(r"[A-Z2-7]{32,128}", credentials.totp_secret):
        raise BootstrapError("Use a fresh random uppercase base32 TOTP secret of at least 32 characters.")
    matched = verify_totp_step(credentials.totp_secret, credentials.totp_code)
    if matched is None:
        raise BootstrapError("Authenticator proof was not accepted; no account was created.")
    try:
        encrypted = encrypt_totp_secret(credentials.totp_secret)
    except Exception:
        raise BootstrapError("The application's TOTP encryption key is missing or invalid.") from None
    return email, hash_password(credentials.password), encrypted, matched


async def provision(conn, credentials: Credentials, *, database: str, owner: str, audit_logger):
    """Atomic first-user creation; caller supplies an otherwise idle connection."""
    from app.core.security.platform_admin_guard import lock_user_administration
    from app.domain.services.audit_logger import AuditEvent
    if conn.is_in_transaction():
        raise BootstrapError("Bootstrap must own its complete transaction.")
    email, encoded, encrypted, matched = prepare_credentials(credentials)
    user_id = uuid4()
    async with conn.transaction(isolation="read_committed"):
        await conn.execute("SET LOCAL search_path=pg_catalog,public; SET LOCAL statement_timeout='15s'; SET LOCAL lock_timeout='5s'")
        await check_target(conn, database=database, owner=owner)
        await conn.execute("SET LOCAL app.bypass_rls='on'")
        # Same lock order as Admin writes: audit chain, authority, account rows.
        await audit_logger.lock_transaction(conn)
        await conn.execute("SET LOCAL lock_timeout='5s'")
        await lock_user_administration(conn)
        # Also exclude other INSERT/UPDATE statements that do not use the
        # application lock (for example a concurrent registration or operator).
        await conn.execute("LOCK TABLE public.user_profiles IN SHARE ROW EXCLUSIVE MODE")
        await check_available(conn, email)
        await conn.execute("""INSERT INTO public.user_profiles
            (id,email,name,role,tenant_id,password_hash,is_active,is_verified,
             email_verified_at,password_changed_at,mfa_enabled)
            VALUES($1,$2,$3,'platform_admin',NULL,$4,TRUE,TRUE,NOW(),NOW(),TRUE)""",
            user_id, email, credentials.name.strip(), encoded)
        await conn.execute("""INSERT INTO public.user_mfa
            (user_id,totp_secret_enc,enabled,verified_at,last_used_at)
            VALUES($1,$2,TRUE,NOW(),$3)""", user_id, encrypted, matched)
        event = await audit_logger.log_in_transaction(conn,
            event_type=AuditEvent.USER_CREATED, action="first_platform_admin_bootstrapped",
            actor_type="system", resource_type="user", resource_id=user_id,
            description="Operator bootstrapped the first administrator with verified authenticator proof.",
            after_state={"role": "platform_admin", "mfa_enabled": True},
            metadata={"email_ownership_attested": True, "database_operator": owner})
    return {"status": "created", "user_id": str(user_id), "audit_event_id": str(event), "mfa_enabled": True}


async def run(args, dsn, credentials=None):
    import asyncpg
    conn = await asyncpg.connect(dsn, timeout=10, command_timeout=20)
    try:
        if credentials is not None:
            from app.domain.services.audit_logger import AuditLogger
            return await provision(conn, credentials, database=args.expected_database,
                                   owner=args.expected_owner, audit_logger=AuditLogger(None))
        async with conn.transaction(readonly=True):
            await check_target(conn, database=args.expected_database, owner=args.expected_owner)
            await conn.execute("SET LOCAL app.bypass_rls='on'")
            await check_available(conn, args.email)
        return {"status": "eligible", "changed": False, "note": "Availability is checked again atomically on apply."}
    finally:
        await conn.close(timeout=5)


def hidden_input(prompt):
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise BootstrapError("Apply requires an interactive private terminal; redirected secret input is refused.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except (getpass.GetPassWarning, EOFError):
            raise BootstrapError("A non-echoing terminal is required.") from None


def configure_environment(values):
    """Use only the selected application profile, including audit key fallback."""
    base = {key: value for key, value in os.environ.items()
            if key in {"PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR"}}
    os.environ.clear()
    os.environ.update(base)
    os.environ.update({key: value for key, value in values.items() if not key.startswith("PG")})
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    # Pydantic's dotenv source uses dotenv_values directly and does not honor
    # load_dotenv's disable flag. Explicitly turn off that second file source
    # before any security module or AuditLogger obtains settings.
    from app.core.config import Settings, get_settings
    Settings.model_config["env_file"] = None
    get_settings.cache_clear()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path, help="Protected application environment, including its real TOTP/audit encryption keys")
    parser.add_argument("--database-url-file", required=True, type=Path, help="0600 file containing only the restricted migration owner's verified-TLS URL")
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--expected-owner", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--confirm-owned-email", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Without this flag only read-only eligibility is checked")
    args = parser.parse_args(argv)
    try:
        from io import StringIO
        from dotenv import dotenv_values
        # Explicit selected file only; no implicit .env discovery or expansion
        # from an unrelated shell environment.
        values = dotenv_values(stream=StringIO(read_private_file(args.env_file, allow_group_read=True)), interpolate=False)
        if any(value is None for value in values.values()):
            raise BootstrapError("Application environment contains an entry without a value.")
        if not values.get("TOTP_ENCRYPTION_KEY") or not any(values.get(key) for key in
                ("AUDIT_LOG_SIGNING_KEY", "APP_AUDIT_LOG_SIGNING_KEY", "JWT_SECRET", "SECRET_KEY")):
            raise BootstrapError("Application environment must supply its existing TOTP and audit/JWT keys.")
        configure_environment(values)
        dsn = read_private_file(args.database_url_file).strip()
        validate_database_url(dsn)
        if unquote(urlsplit(dsn).path[1:]) != args.expected_database:
            raise BootstrapError("Database URL does not match the explicit database name.")
        credentials = None
        if args.apply:
            if not args.confirm_owned_email:
                raise BootstrapError("--confirm-owned-email is required only after independently verifying ownership.")
            password = hidden_input("New administrator password: ")
            if password != hidden_input("Repeat password: "):
                raise BootstrapError("Passwords did not match.")
            secret = hidden_input("Fresh TOTP secret already enrolled in your authenticator: ")
            code = hidden_input("Current authenticator code: ")
            credentials = Credentials(args.email, args.name, password, secret, code, True)
        result = asyncio.run(asyncio.wait_for(run(args, dsn, credentials), 45))
        print(json.dumps(result, sort_keys=True))
        return 0
    except BootstrapError as exc:
        print(str(exc), file=sys.stderr)
    except KeyboardInterrupt:
        print("Bootstrap interrupted. Check account existence before retrying.", file=sys.stderr)
    except Exception:
        # Connection errors, database DETAIL and malformed files can contain
        # passwords or hashes. Never stringify them or print a traceback.
        print("Bootstrap failed; sensitive error detail withheld. Check the target and database logs privately. If the connection failed during commit, check account existence before retrying.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
