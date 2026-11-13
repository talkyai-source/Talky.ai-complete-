"""QA F1-15 / D4-2: inviting a teammate, end to end through the handlers.

Owner decisions, 11 Nov 2026: an email that already has an account is
refused; an invite lasts 7 days; any role up to tenant admin may be invited.
Admins invite, list and withdraw; the invited person previews the invite and
accepts it, which creates their account and active membership in one
transaction that also spends the token.
"""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import asyncpg
import fakeredis
import fakeredis.aioredis
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints.auth import invites as accept_api
from app.api.v1.endpoints.auth.schemas import InviteAcceptRequest, InviteTokenRequest
from app.api.v1.endpoints.rbac import tenant_invites as admin_api
from app.api.v1.endpoints.rbac.schemas import InviteTeammateRequest
from app.domain.services import legal_acceptance, team_invites

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
ADMIN = "33333333-3333-4333-8333-333333333333"
EMAIL = "sam@example.com"
PASSWORD = "Correct horse battery staple 42"


class FakeDB:
    """One Postgres connection: a failed transaction rolls back what it wrote."""

    def __init__(self, *, existing=(), tenant_status="active", caller_role="tenant_admin"):
        self.existing = set(existing)
        self.tenant_status = tenant_status
        self.caller_role = caller_role
        self.depth = 0
        self.locked = False
        self.users, self.members, self.receipts, self.acceptances = [], [], [], []
        self.other_admins = {}

    def is_in_transaction(self):
        return self.depth > 0

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        snapshot = deepcopy((self.users, self.members, self.receipts, self.acceptances))
        self.depth += 1
        try:
            yield self
        except BaseException:
            self.users, self.members, self.receipts, self.acceptances = snapshot
            raise
        finally:
            self.depth -= 1
            self.locked = self.locked and self.depth > 0

    @staticmethod
    def sql(text):
        return " ".join(text.lower().split())

    def taken(self, email):
        return email in self.existing or any(user["email"] == email for user in self.users)

    async def execute(self, sql, *args):
        query = self.sql(sql)
        if query.startswith(("set local", "select set_config")):
            return "SET"
        if query.startswith("insert into user_profiles"):
            if self.taken(args[1]):
                raise asyncpg.UniqueViolationError.new(
                    {"C": "23505", "M": "duplicate key", "n": "user_profiles_email_key"})
            self.users.append(dict(zip(("id", "email", "name", "tenant_id", "role", "password_hash"), args)))
            return "INSERT 0 1"
        if query.startswith("insert into legal_acceptances"):
            self.acceptances.append(args)
            return "INSERT 0 1"
        raise AssertionError(query)

    async def fetchval(self, sql, *args):
        query = self.sql(sql)
        if query == "select 1 from user_profiles where lower(email) = $1":
            return 1 if self.taken(args[0]) else None
        if query == "select business_name from tenants where id = $1":
            return "Acme Ltd" if str(args[0]) == TENANT else None
        if query == "select id from roles where name = $1":
            return f"role-{args[0]}"
        if query.startswith("insert into tenant_users"):
            assert "tenant_id" in query and str(args[1]) == TENANT
            self.members.append(dict(zip(("user_id", "tenant_id", "role_id", "invited_by", "invited_at"), args)))
            return "membership-1"
        raise AssertionError(query)

    async def fetchrow(self, sql, *args):
        query = self.sql(sql)
        if query.startswith("select r.name from tenant_users tu"):
            mine = (str(args[0]), str(args[1])) == (ADMIN, TENANT)
            if not mine:
                other = self.other_admins.get((str(args[0]), str(args[1])))
                return {"name": other} if other else None
            return {"name": self.caller_role} if self.caller_role else None
        if query == "select id, status from tenants where id = $1":
            return {"id": args[0], "status": self.tenant_status} if str(args[0]) == TENANT else None
        raise AssertionError(query)


class Audit:
    def __init__(self, db, *, fail=False):
        self.db, self.fail = db, fail

    async def lock_transaction(self, conn):
        assert conn is self.db and conn.is_in_transaction()
        conn.locked = True

    async def log_in_transaction(self, conn, **entry):
        assert conn is self.db and conn.is_in_transaction() and conn.locked
        if self.fail:
            raise RuntimeError("synthetic audit outage")
        conn.receipts.append(entry)


class Mailer:
    def __init__(self, delivered=True):
        self.delivered, self.sent = delivered, []

    async def send_team_invite_email(self, **message):
        self.sent.append(message)
        return self.delivered


@pytest.fixture
def redis(monkeypatch):
    store = fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    container = SimpleNamespace(is_initialized=True, redis_enabled=True, redis=store)
    monkeypatch.setattr("app.core.container.get_container", lambda: container)
    return store


@pytest.fixture
def mailer(monkeypatch):
    sent = Mailer()
    monkeypatch.setattr(admin_api, "get_email_service", lambda: sent)
    return sent


def admin(role="tenant_admin"):
    return CurrentUser(id=ADMIN, email="admin@example.com", tenant_id=TENANT, role=role, name="Ada Admin")


def http_request():
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"user-agent", b"pytest")],
                    "client": ("203.0.113.5", 4242)})


async def invite(db, *, email=EMAIL, role="agent", audit=None, caller=None):
    return await admin_api.invite_teammate(
        request=InviteTeammateRequest(email=email, role_name=role), current_user=caller or admin(),
        db_client=SimpleNamespace(pool=db), audit_logger=audit or Audit(db))


async def accept(db, token, **fields):
    body = {"token": token, "name": "Sam Agent", "password": PASSWORD, "confirm_password": PASSWORD, **fields}
    return await accept_api.accept_invite(request=http_request(), body=InviteAcceptRequest(**body),
                                          db_client=SimpleNamespace(pool=db), audit_logger=Audit(db))


# --- inviting -------------------------------------------------------------

async def test_an_admin_invites_by_email_and_the_link_carries_the_token(redis, mailer):
    db = FakeDB()
    created = await invite(db)
    assert created.email == EMAIL and created.role == "agent" and created.email_sent is True
    [message] = mailer.sent
    assert message["recipient_email"] == EMAIL and message["workspace_name"] == "Acme Ltd"
    assert message["inviter"] == "Ada Admin" and message["role_name"] == "agent"
    found = await team_invites.by_token(redis, message["token"], now=datetime.fromisoformat(created.created_at))
    assert found is not None and found.id == created.id and found.tenant_id == TENANT
    [receipt] = db.receipts
    assert receipt["action"] == "rbac.invite.create" and str(receipt["tenant_id"]) == TENANT
    assert receipt["before_state"] is None and receipt["after_state"]["email"] == EMAIL
    assert db.users == [] and db.members == [], "nothing is created until the invite is accepted"


async def test_an_email_that_already_has_an_account_is_refused(redis, mailer):
    db = FakeDB(existing={EMAIL})
    with pytest.raises(HTTPException) as caught:
        await invite(db, email="Sam@Example.com")
    assert caught.value.status_code == 409
    assert "already has an account" in str(caught.value.detail)
    assert mailer.sent == [] and db.receipts == []
    assert await team_invites.pending(redis, TENANT, now=datetime.now(timezone.utc)) == []


def test_only_roles_up_to_tenant_admin_can_be_invited():
    for role in ("platform_admin", "partner_admin", "owner"):
        with pytest.raises(ValidationError):
            InviteTeammateRequest(email=EMAIL, role_name=role)
    assert InviteTeammateRequest(email=EMAIL, role_name="tenant_admin").role_name == "tenant_admin"


async def test_a_caller_who_is_no_longer_an_active_admin_cannot_invite(redis, mailer):
    db = FakeDB(caller_role=None)
    with pytest.raises(HTTPException) as caught:
        await invite(db)
    assert caught.value.status_code == 403
    assert mailer.sent == [] and db.receipts == []


async def test_inviting_again_issues_a_new_link_and_the_old_one_stops_working(redis, mailer):
    db = FakeDB()
    first = await invite(db)
    second = await invite(db, role="readonly")
    old_token, new_token = mailer.sent[0]["token"], mailer.sent[1]["token"]
    now = datetime.fromisoformat(second.created_at)
    assert await team_invites.by_token(redis, old_token, now=now) is None
    assert (await team_invites.by_token(redis, new_token, now=now)).role_name == "readonly"
    assert db.receipts[1]["before_state"]["id"] == first.id


async def test_a_failed_email_keeps_the_invite_and_says_so(redis, monkeypatch):
    monkeypatch.setattr(admin_api, "get_email_service", lambda: Mailer(delivered=False))
    db = FakeDB()
    created = await invite(db)
    assert created.email_sent is False
    listed = await admin_api.list_tenant_invites(current_user=admin(), db_client=SimpleNamespace(pool=db))
    assert [entry.id for entry in listed] == [created.id]


async def test_an_audit_outage_leaves_no_working_invite(redis, mailer):
    db = FakeDB()
    with pytest.raises(RuntimeError):
        await invite(db, audit=Audit(db, fail=True))
    assert mailer.sent == []
    listed = await admin_api.list_tenant_invites(current_user=admin(), db_client=SimpleNamespace(pool=db))
    assert listed == []


# --- listing and withdrawing ---------------------------------------------

async def test_admins_list_and_withdraw_invites(redis, mailer):
    db = FakeDB()
    created = await invite(db)
    listed = await admin_api.list_tenant_invites(current_user=admin(), db_client=SimpleNamespace(pool=db))
    assert [(entry.id, entry.email, entry.role) for entry in listed] == [(created.id, EMAIL, "agent")]
    await admin_api.withdraw_invite(invite_id=created.id, current_user=admin(),
                                    db_client=SimpleNamespace(pool=db), audit_logger=Audit(db))
    now = datetime.fromisoformat(created.created_at)
    assert await team_invites.by_token(redis, mailer.sent[0]["token"], now=now) is None
    assert db.receipts[-1]["action"] == "rbac.invite.withdraw"
    assert db.receipts[-1]["before_state"]["id"] == created.id and db.receipts[-1]["after_state"] is None
    with pytest.raises(HTTPException) as caught:
        await admin_api.withdraw_invite(invite_id=created.id, current_user=admin(),
                                        db_client=SimpleNamespace(pool=db), audit_logger=Audit(db))
    assert caught.value.status_code == 404


async def test_another_workspaces_invite_cannot_be_seen_or_withdrawn(redis, mailer):
    now = datetime.now(timezone.utc)
    theirs, _, _ = await team_invites.issue(redis, tenant_id=OTHER, email="x@example.com", role_name="user",
                                            invited_by="someone", now=now)
    db = FakeDB()
    assert await admin_api.list_tenant_invites(current_user=admin(), db_client=SimpleNamespace(pool=db)) == []
    with pytest.raises(HTTPException) as caught:
        await admin_api.withdraw_invite(invite_id=theirs.id, current_user=admin(),
                                        db_client=SimpleNamespace(pool=db), audit_logger=Audit(db))
    assert caught.value.status_code == 404
    assert await team_invites.find(redis, OTHER, theirs.id, now=now) == theirs


# --- previewing and accepting ---------------------------------------------

async def test_the_invited_person_sees_what_they_were_invited_to(redis, mailer):
    db = FakeDB()
    await invite(db)
    preview = await accept_api.preview_invite(request=http_request(), body=InviteTokenRequest(token=mailer.sent[0]["token"]),
                                              db_client=SimpleNamespace(pool=db))
    assert (preview.email, preview.workspace_name, preview.role) == (EMAIL, "Acme Ltd", "agent")


async def test_accepting_creates_the_account_and_membership_and_spends_the_link(redis, mailer, monkeypatch):
    monkeypatch.setattr(legal_acceptance, "current_versions", lambda: ("terms-v3", "privacy-v2"))
    db = FakeDB()
    created = await invite(db)
    token = mailer.sent[0]["token"]
    joined = await accept(db, token, terms_version="terms-v3", privacy_version="privacy-v2")
    assert joined.email == EMAIL
    [user] = db.users
    assert (user["email"], user["name"], str(user["tenant_id"]), user["role"]) == (EMAIL, "Sam Agent", TENANT, "agent")
    assert user["password_hash"] and PASSWORD not in user["password_hash"]
    [member] = db.members
    assert member["user_id"] == user["id"] and member["role_id"] == "role-agent" and member["invited_by"] == ADMIN
    [acceptance] = db.acceptances
    assert acceptance[2:4] == ("terms-v3", "privacy-v2")
    assert db.receipts[-1]["action"] == "rbac.invite.accept" and str(db.receipts[-1]["actor_id"]) == user["id"]
    now = datetime.fromisoformat(created.created_at)
    assert await team_invites.by_token(redis, token, now=now) is None
    assert await team_invites.pending(redis, TENANT, now=now) == []
    with pytest.raises(HTTPException) as again:
        await accept(db, token)
    assert again.value.status_code == 410


async def test_a_bad_or_withdrawn_link_creates_nothing(redis, mailer):
    db = FakeDB()
    created = await invite(db)
    await admin_api.withdraw_invite(invite_id=created.id, current_user=admin(),
                                    db_client=SimpleNamespace(pool=db), audit_logger=Audit(db))
    for token in (mailer.sent[0]["token"], "x" * 43):
        with pytest.raises(HTTPException) as caught:
            await accept(db, token)
        assert caught.value.status_code == 410
        with pytest.raises(HTTPException) as previewed:
            await accept_api.preview_invite(request=http_request(), body=InviteTokenRequest(token=token),
                                            db_client=SimpleNamespace(pool=db))
        assert previewed.value.status_code == 410
    assert db.users == [] and db.members == []


async def test_a_link_withdrawn_while_accepting_rolls_everything_back(redis, mailer, monkeypatch):
    db = FakeDB()
    await invite(db)

    async def already_spent(_redis, _invite):
        return False

    monkeypatch.setattr(team_invites, "spend", already_spent)
    with pytest.raises(HTTPException) as caught:
        await accept(db, mailer.sent[0]["token"])
    assert caught.value.status_code == 410
    assert db.users == [] and db.members == [] and [r["action"] for r in db.receipts] == ["rbac.invite.create"]


async def test_an_email_that_got_an_account_meanwhile_is_refused(redis, mailer):
    db = FakeDB()
    await invite(db)
    db.existing.add(EMAIL)
    with pytest.raises(HTTPException) as caught:
        await accept(db, mailer.sent[0]["token"])
    assert caught.value.status_code == 409
    assert db.users == [] and db.members == []


async def test_a_suspended_workspace_cannot_be_joined(redis, mailer):
    db = FakeDB()
    await invite(db)
    db.tenant_status = "suspended"
    with pytest.raises(HTTPException) as caught:
        await accept(db, mailer.sent[0]["token"])
    assert caught.value.status_code == 410
    assert db.users == []


async def test_passwords_must_match_and_be_strong(redis, mailer):
    db = FakeDB()
    await invite(db)
    token = mailer.sent[0]["token"]
    with pytest.raises(HTTPException) as mismatch:
        await accept(db, token, confirm_password=PASSWORD + "x")
    assert mismatch.value.status_code == 400
    with pytest.raises(HTTPException) as weak:
        await accept(db, token, password="short", confirm_password="short")
    assert weak.value.status_code == 400
    assert db.users == []


async def test_guessing_tokens_is_rate_limited(redis):
    db = FakeDB()
    statuses = []
    for _ in range(21):
        try:
            await accept_api.preview_invite(request=http_request(), body=InviteTokenRequest(token="y" * 43),
                                            db_client=SimpleNamespace(pool=db))
        except HTTPException as exc:
            statuses.append(exc.status_code)
    assert statuses[:20] == [410] * 20 and statuses[20] == 429


# --- security review, 11 Nov 2026 -------------------------------------------

@pytest.mark.parametrize("inviter_now", ["user", None], ids=["demoted", "removed"])
async def test_an_invite_dies_with_its_inviters_admin_rights(redis, mailer, inviter_now):
    db = FakeDB()
    await invite(db, role="tenant_admin")
    db.caller_role = inviter_now
    with pytest.raises(HTTPException) as caught:
        await accept(db, mailer.sent[0]["token"])
    assert caught.value.status_code == 410
    assert db.users == [] and db.members == []


async def test_invites_are_capped_per_recipient_and_per_workspace(redis, mailer):
    db = FakeDB()
    for _ in range(3):
        await invite(db)
    with pytest.raises(HTTPException) as recipient:
        await invite(db)
    assert recipient.value.status_code == 429
    # Every authorized attempt counts toward the workspace's 20 a day, the
    # refused one included: the cap bounds attempts, not only emails.
    for n in range(16):
        await invite(db, email=f"person{n}@example.com")
    with pytest.raises(HTTPException) as workspace:
        await invite(db, email="one-more@example.com")
    assert workspace.value.status_code == 429
    assert len(mailer.sent) == 19 and all(m["recipient_email"] != "one-more@example.com" for m in mailer.sent)


async def test_a_caller_who_is_not_an_admin_spends_no_invite_budget(redis, mailer):
    db = FakeDB(caller_role=None)
    for _ in range(25):
        with pytest.raises(HTTPException) as caught:
            await invite(db)
        assert caught.value.status_code == 403
    db.caller_role = "tenant_admin"
    assert (await invite(db)).email_sent is True


async def test_one_workspace_cannot_use_up_anothers_invites_to_a_person(redis, mailer):
    rival_admin = "44444444-4444-4444-8444-444444444444"
    db = FakeDB()
    db.other_admins[(rival_admin, OTHER)] = "tenant_admin"
    rival = CurrentUser(id=rival_admin, email="rival@example.com", tenant_id=OTHER, role="tenant_admin", name="Rival")
    for _ in range(3):
        await invite(db, caller=rival)
    with pytest.raises(HTTPException) as capped:
        await invite(db, caller=rival)
    assert capped.value.status_code == 429
    assert (await invite(db)).email_sent is True


def rival_workspaces(db, count):
    callers = []
    for n in range(count):
        admin_id, tenant_id = f"5555555{n}-5555-4555-8555-555555555555", f"6666666{n}-6666-4666-8666-666666666666"
        db.other_admins[(admin_id, tenant_id)] = "tenant_admin"
        callers.append(CurrentUser(id=admin_id, email=f"a{n}@example.com", tenant_id=tenant_id, role="tenant_admin"))
    return callers


async def test_at_most_ten_workspaces_can_invite_one_person_a_day(redis, mailer):
    # Each of them at most 3 times: one inbox gets at most 30 invites a day.
    db = FakeDB()
    callers = rival_workspaces(db, 11)
    for caller in callers[:10]:
        await invite(db, caller=caller)
    with pytest.raises(HTTPException) as eleventh:
        await invite(db, caller=callers[10])
    assert eleventh.value.status_code == 429
    assert eleventh.value.detail == "Too many attempts. Please try again later."
    await invite(db, caller=callers[0])
    assert len(mailer.sent) == 11


async def test_blocking_someones_invites_takes_ten_workspaces_not_one(redis, mailer):
    db = FakeDB()
    for caller in rival_workspaces(db, 9):
        for _ in range(3):
            await invite(db, caller=caller)
    assert (await invite(db)).email_sent is True
    assert (await invite(db)).email_sent is True
