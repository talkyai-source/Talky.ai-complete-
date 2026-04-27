import { test } from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import {
    authMeFromRequest,
    authTokenFromRequest,
    buildSessionCookie,
    clearSessionCookie,
    ensureAuthSchema,
    hashPassword,
    randomSessionId,
    validatePasswordStrength,
    verifyPassword,
} from "@/server/auth-core";
import { getSql, isDatabaseConfigured } from "@/server/db";
import { ensureDefaultPartnerAndTenantForUser } from "@/server/rbac";

async function withEnv<T>(vars: Record<string, string | undefined>, fn: () => T | Promise<T>) {
    const prev: Record<string, string | undefined> = {};
    for (const [k, v] of Object.entries(vars)) {
        prev[k] = process.env[k];
        if (v === undefined) delete process.env[k];
        else process.env[k] = v;
    }
    try {
        return await fn();
    } finally {
        for (const [k, v] of Object.entries(prev)) {
            if (v === undefined) delete process.env[k];
            else process.env[k] = v;
        }
    }
}

test("password strength validator rejects weak passwords", () => {
    const r = validatePasswordStrength("password");
    assert.equal(r.ok, false);
    assert.ok(r.failures.length > 0);
});

test("argon2 hashes and verifies passwords", async () => {
    const hash = await hashPassword("Str0ng!Password-For-Tests");
    assert.ok(typeof hash === "string" && hash.length > 20);
    assert.equal(await verifyPassword(hash, "Str0ng!Password-For-Tests"), true);
    assert.equal(await verifyPassword(hash, "wrong-password"), false);
});

test("auth token is extracted from Authorization header", () => {
    const req = new Request("http://example.test/auth/me", { headers: { authorization: "Bearer token-123" } });
    assert.equal(authTokenFromRequest(req), "token-123");
});

test("auth token is extracted from cookie header", () => {
    const req = new Request("http://example.test/auth/me", { headers: { cookie: "a=b; talklee_auth_token=abc123; c=d" } });
    assert.equal(authTokenFromRequest(req), "abc123");
});

test("session cookies are httpOnly and sameSite", () => {
    const expiresAt = new Date(Date.now() + 60_000);
    const set = buildSessionCookie({ sessionId: "sid", expiresAt, secure: true });
    assert.ok(/talklee_auth_token=/.test(set));
    assert.ok(/HttpOnly/i.test(set));
    assert.ok(/SameSite=Lax/i.test(set));
    assert.ok(/Secure/i.test(set));

    const cleared = clearSessionCookie({ secure: true });
    assert.ok(/Max-Age=0/.test(cleared));
    assert.ok(/HttpOnly/i.test(cleared));
});

test("sessions enforce absolute expiry, idle timeout, binding, and rotation (db)", async (t) => {
    if (!isDatabaseConfigured()) {
        t.skip();
        return;
    }

    await ensureAuthSchema();
    const sql = getSql();

    const userId = crypto.randomUUID();
    await sql`
        insert into users (id, email, username, password_hash, status, role, name, business_name, created_at, updated_at)
        values (${userId}::uuid, ${`db-test-${userId}@example.com`}, null, 'x', 'active', 'user', null, null, now(), now())
    `;

    t.after(async () => {
        try {
            await sql`delete from users where id = ${userId}::uuid`;
        } catch {
        }
    });

    const ip = "1.1.1.1";
    const ua = "db-test-agent";

    const sessionId = randomSessionId();
    const expiresAt = new Date(Date.now() + 60_000);
    await sql`
        insert into sessions (session_id, user_id, expires_at, last_activity_at, ip_address, user_agent, revoked, revoked_at, rotated_from, replaced_by)
        values (${sessionId}, ${userId}::uuid, ${expiresAt}, now(), ${ip}, ${ua}, false, null, null, null)
    `;

    await withEnv({ AUTH_SESSION_IDLE_TIMEOUT_SECONDS: "2", AUTH_SESSION_BINDING_MODE: "strict", AUTH_SESSION_ROTATE_SECONDS: "1" }, async () => {
        const reqOk = new Request("http://example.test/api/v1/me", {
            headers: { cookie: `talklee_auth_token=${encodeURIComponent(sessionId)}`, "x-forwarded-for": ip, "user-agent": ua },
        });
        const ok = await authMeFromRequest(reqOk);
        assert.ok(ok);
        assert.equal(ok.me.id, userId);

        await sql`update sessions set created_at = now() - interval '5 seconds' where session_id = ${sessionId}`;
        const rotated = await authMeFromRequest(reqOk);
        assert.ok(rotated);
        assert.notEqual(rotated.sessionId, sessionId);
        assert.ok(typeof rotated.setCookie === "string" && rotated.setCookie.length > 0);

        const old = await sql<{ revoked: boolean; replaced_by: string | null }[]>`
            select revoked, replaced_by from sessions where session_id = ${sessionId} limit 1
        `;
        assert.equal(old[0]?.revoked, true);
        assert.equal(old[0]?.replaced_by, rotated.sessionId);

        await sql`update sessions set last_activity_at = now() - interval '10 minutes' where session_id = ${rotated.sessionId}`;
        const idleExpiredReq = new Request("http://example.test/api/v1/me", {
            headers: { cookie: `talklee_auth_token=${encodeURIComponent(rotated.sessionId)}`, "x-forwarded-for": ip, "user-agent": ua },
        });
        const idleExpired = await authMeFromRequest(idleExpiredReq);
        assert.equal(idleExpired, null);

        const newSid = randomSessionId();
        await sql`
            insert into sessions (session_id, user_id, expires_at, last_activity_at, ip_address, user_agent, revoked, revoked_at, rotated_from, replaced_by)
            values (${newSid}, ${userId}::uuid, ${new Date(Date.now() + 60_000)}, now(), ${ip}, ${ua}, false, null, null, null)
        `;
        await sql`update sessions set expires_at = now() - interval '1 second' where session_id = ${newSid}`;
        const expiredReq = new Request("http://example.test/api/v1/me", {
            headers: { cookie: `talklee_auth_token=${encodeURIComponent(newSid)}`, "x-forwarded-for": ip, "user-agent": ua },
        });
        const expired = await authMeFromRequest(expiredReq);
        assert.equal(expired, null);

        const newSid2 = randomSessionId();
        await sql`
            insert into sessions (session_id, user_id, expires_at, last_activity_at, ip_address, user_agent, revoked, revoked_at, rotated_from, replaced_by)
            values (${newSid2}, ${userId}::uuid, ${new Date(Date.now() + 60_000)}, now(), ${ip}, ${ua}, false, null, null, null)
        `;
        const hijackReq = new Request("http://example.test/api/v1/me", {
            headers: { cookie: `talklee_auth_token=${encodeURIComponent(newSid2)}`, "x-forwarded-for": "9.9.9.9", "user-agent": ua },
        });
        const hijack = await authMeFromRequest(hijackReq);
        assert.equal(hijack, null);
    });
});

test("sessions rotate on role/scope change and include usage/billing mapping (db)", async (t) => {
    if (!isDatabaseConfigured()) {
        t.skip();
        return;
    }

    await ensureAuthSchema();
    const sql = getSql();

    const userId = crypto.randomUUID();
    const email = `db-test-scope-${userId}@example.com`;
    await sql`
        insert into users (id, email, username, password_hash, status, role, name, business_name, created_at, updated_at)
        values (${userId}::uuid, ${email}, null, 'x', 'active', 'user', null, null, now(), now())
    `;

    t.after(async () => {
        try {
            await sql`delete from users where id = ${userId}::uuid`;
        } catch {
        }
    });

    await ensureDefaultPartnerAndTenantForUser({ userId, email, businessName: null });
    const tenantRows = await sql<{ tenant_id: string; partner_id: string }[]>`
        select tu.tenant_id, t.partner_id
        from tenant_users tu
        join tenants t on t.id = tu.tenant_id
        where tu.user_id = ${userId}::uuid
        limit 1
    `;
    const tenantId = tenantRows[0]?.tenant_id ?? null;
    const partnerId = tenantRows[0]?.partner_id ?? null;
    assert.ok(tenantId && partnerId);

    const ip = "2.2.2.2";
    const ua = "db-test-scope-agent";

    const sessionId = randomSessionId();
    const expiresAt = new Date(Date.now() + 60_000);
    await sql`
        insert into sessions (
            session_id,
            user_id,
            expires_at,
            last_activity_at,
            ip_address,
            user_agent,
            revoked,
            revoked_at,
            rotated_from,
            replaced_by,
            scope_role,
            scope_partner_id,
            scope_tenant_id
        )
        values (
            ${sessionId},
            ${userId}::uuid,
            ${expiresAt},
            now(),
            ${ip},
            ${ua},
            false,
            null,
            null,
            null,
            'user',
            ${partnerId},
            ${tenantId}
        )
    `;

    const req = new Request("http://example.test/api/v1/me", {
        headers: { cookie: `talklee_auth_token=${encodeURIComponent(sessionId)}`, "x-forwarded-for": ip, "user-agent": ua },
    });
    const res = await authMeFromRequest(req);
    assert.ok(res);
    assert.notEqual(res.sessionId, sessionId);
    assert.ok(typeof res.setCookie === "string" && res.setCookie.length > 0);

    const newRows = await sql<
        Array<{
            scope_role: string | null;
            scope_partner_id: string | null;
            scope_tenant_id: string | null;
            scope_usage_account_id: string | null;
            scope_billing_account_id: string | null;
            rotated_from: string | null;
        }>
    >`
        select
            scope_role,
            scope_partner_id,
            scope_tenant_id,
            scope_usage_account_id::text as scope_usage_account_id,
            scope_billing_account_id::text as scope_billing_account_id,
            rotated_from
        from sessions
        where session_id = ${res.sessionId}
        limit 1
    `;
    const newRow = newRows[0];
    assert.ok(newRow);
    assert.equal(newRow?.rotated_from, sessionId);
    assert.equal(newRow?.scope_partner_id, partnerId);
    assert.equal(newRow?.scope_tenant_id, tenantId);
    assert.ok(typeof newRow?.scope_usage_account_id === "string" && newRow.scope_usage_account_id.length > 0);
    assert.ok(typeof newRow?.scope_billing_account_id === "string" && newRow.scope_billing_account_id.length > 0);
});
