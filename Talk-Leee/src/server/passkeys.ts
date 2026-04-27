import crypto from "node:crypto";
import {
    generateAuthenticationOptions,
    generateRegistrationOptions,
    verifyAuthenticationResponse,
    verifyRegistrationResponse,
} from "@simplewebauthn/server";
import type { AuthenticationResponseJSON, PublicKeyCredentialCreationOptionsJSON, PublicKeyCredentialRequestOptionsJSON, RegistrationResponseJSON } from "@simplewebauthn/server";
import { consumeAuthRateLimit, ensureAuthSchema } from "@/server/auth-core";
import { verifyMfaForLogin } from "@/server/mfa";
import { getSql, isDatabaseConfigured } from "@/server/db";
import type { AuthRole } from "@/server/auth-core";

export type PasskeyEventName = "passkey_registered" | "passkey_login_succeeded" | "passkey_login_failed";
export type PasskeyEvent = {
    name: PasskeyEventName;
    userId?: string;
    credentialId?: string;
    ipAddress: string;
    at: string;
};

type PasskeyEventHandler = (event: PasskeyEvent) => void | Promise<void>;

let passkeyEventHandler: PasskeyEventHandler | null = null;

export function setPasskeyEventHandler(handler: PasskeyEventHandler | null) {
    passkeyEventHandler = handler;
}

function emitPasskeyEvent(event: PasskeyEvent) {
    const h = passkeyEventHandler;
    if (!h) return;
    try {
        void h(event);
    } catch {
    }
}

function parsePositiveInt(input: unknown, fallback: number) {
    const n = typeof input === "string" ? Number(input) : typeof input === "number" ? input : NaN;
    if (!Number.isFinite(n) || n <= 0) return fallback;
    return Math.floor(n);
}

function passkeyChallengeTtlSeconds() {
    return parsePositiveInt(process.env.WEBAUTHN_CHALLENGE_TTL_SECONDS, 5 * 60);
}

function passkeyRegistrationLimits() {
    return {
        perUserPerMinute: parsePositiveInt(process.env.PASSKEY_REGISTER_LIMIT_PER_USER_PER_MINUTE, 10),
        perIpPerMinute: parsePositiveInt(process.env.PASSKEY_REGISTER_LIMIT_PER_IP_PER_MINUTE, 30),
    };
}

function passkeyLoginLimits() {
    return {
        perIdentifierPerMinute: parsePositiveInt(process.env.PASSKEY_LOGIN_LIMIT_PER_IDENTIFIER_PER_MINUTE, 10),
        perIpPerMinute: parsePositiveInt(process.env.PASSKEY_LOGIN_LIMIT_PER_IP_PER_MINUTE, 30),
    };
}

function webAuthnRpName() {
    const raw = process.env.WEBAUTHN_RP_NAME;
    const v = raw ? String(raw).trim() : "";
    return v || "Talk-Lee";
}

function webAuthnRpIdFromEnv() {
    const raw = process.env.WEBAUTHN_RP_ID;
    const v = raw ? String(raw).trim() : "";
    return v || null;
}

function webAuthnAllowedOriginsFromEnv() {
    const raw = process.env.WEBAUTHN_ALLOWED_ORIGINS;
    const v = raw ? String(raw).trim() : "";
    if (!v) return [];
    return v
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
}

export function webAuthnConfigForRequest(request: Request) {
    const url = new URL(request.url);
    const rpId = webAuthnRpIdFromEnv() ?? url.hostname;

    const configuredOrigins = webAuthnAllowedOriginsFromEnv();
    const originHeader = (request.headers.get("origin") ?? "").trim();
    const fallbackOrigin = url.origin;
    const allowedOrigins =
        configuredOrigins.length > 0
            ? configuredOrigins
            : process.env.NODE_ENV === "production"
              ? []
              : [originHeader || fallbackOrigin].filter(Boolean);

    if (process.env.NODE_ENV === "production") {
        if (!webAuthnRpIdFromEnv()) {
            throw new Error("Missing environment variable: WEBAUTHN_RP_ID");
        }
        if (allowedOrigins.length === 0) {
            throw new Error("Missing environment variable: WEBAUTHN_ALLOWED_ORIGINS");
        }
    }

    return { rpId, rpName: webAuthnRpName(), expectedOrigins: allowedOrigins };
}

function base64UrlToBuffer(input: string) {
    const s = String(input ?? "").trim();
    if (!s) return Buffer.alloc(0);
    return Buffer.from(s, "base64url");
}

function parseClientDataJsonChallenge(base64Url: string) {
    const buf = base64UrlToBuffer(base64Url);
    if (buf.length === 0) return null;
    try {
        const json = JSON.parse(buf.toString("utf8")) as { challenge?: unknown };
        const challenge = typeof json.challenge === "string" ? json.challenge.trim() : "";
        if (!challenge) return null;
        return challenge;
    } catch {
        return null;
    }
}

async function rateLimitRegistration(input: { userId: string; ipAddress: string }) {
    const limits = passkeyRegistrationLimits();
    const ip = input.ipAddress || "unknown";
    const [ipLimit, userLimit] = await Promise.all([
        consumeAuthRateLimit({ bucket: `passkeys:register:ip:${ip}`, limit: limits.perIpPerMinute, windowSeconds: 60 }),
        consumeAuthRateLimit({ bucket: `passkeys:register:user:${input.userId}`, limit: limits.perUserPerMinute, windowSeconds: 60 }),
    ]);
    return {
        allowed: ipLimit.allowed && userLimit.allowed,
        retryAfterSeconds: Math.max(ipLimit.retryAfterSeconds, userLimit.retryAfterSeconds),
    };
}

async function rateLimitLogin(input: { identifier: string; ipAddress: string }) {
    const limits = passkeyLoginLimits();
    const ip = input.ipAddress || "unknown";
    const id = input.identifier.trim().toLowerCase() || "unknown";
    const [ipLimit, idLimit] = await Promise.all([
        consumeAuthRateLimit({ bucket: `passkeys:login:ip:${ip}`, limit: limits.perIpPerMinute, windowSeconds: 60 }),
        consumeAuthRateLimit({ bucket: `passkeys:login:id:${id}`, limit: limits.perIdentifierPerMinute, windowSeconds: 60 }),
    ]);
    return {
        allowed: ipLimit.allowed && idLimit.allowed,
        retryAfterSeconds: Math.max(ipLimit.retryAfterSeconds, idLimit.retryAfterSeconds),
    };
}

async function storeWebAuthnChallenge(input: {
    challenge: string;
    kind: "registration" | "authentication";
    userId?: string;
    identifier?: string;
    ipAddress: string;
    userAgent: string;
}) {
    const sql = getSql();
    const ttlSeconds = passkeyChallengeTtlSeconds();
    const expiresAt = new Date(Date.now() + ttlSeconds * 1000);
    await sql`
        insert into auth_webauthn_challenges (challenge, kind, user_id, identifier, ip_address, user_agent, expires_at, created_at)
        values (
            ${input.challenge},
            ${input.kind},
            ${input.userId ?? null}::uuid,
            ${input.identifier?.trim() ? input.identifier.trim() : null},
            ${input.ipAddress || null},
            ${input.userAgent || null},
            ${expiresAt},
            now()
        )
        on conflict (challenge)
        do update set
            kind = excluded.kind,
            user_id = excluded.user_id,
            identifier = excluded.identifier,
            ip_address = excluded.ip_address,
            user_agent = excluded.user_agent,
            expires_at = excluded.expires_at,
            created_at = excluded.created_at
    `;
}

async function consumeWebAuthnChallenge(input: {
    challenge: string;
    kind: "registration" | "authentication";
    userId?: string;
    ipAddress: string;
    userAgent: string;
}) {
    const sql = getSql();
    const userId = input.userId?.trim() ? input.userId.trim() : null;
    const ip = input.ipAddress || "";
    const ua = input.userAgent || "";
    const rows = await sql<
        { challenge: string; kind: string; user_id: string | null; ip_address: string | null; user_agent: string | null }[]
    >`
        delete from auth_webauthn_challenges
        where
            challenge = ${input.challenge}
            and kind = ${input.kind}
            and expires_at > now()
            and (${userId === null} or user_id = ${userId}::uuid)
            and (ip_address is null or ip_address = ${ip || null})
            and (user_agent is null or user_agent = ${ua || null})
        returning challenge, kind, user_id, ip_address, user_agent
    `;
    return rows[0] ?? null;
}

export async function getPasskeyRegistrationOptions(input: {
    request: Request;
    userId: string;
    email: string;
    displayName?: string;
    ipAddress: string;
    userAgent: string;
}) {
    if (!isDatabaseConfigured()) return { ok: false as const, code: "db_unavailable" as const };
    await ensureAuthSchema();

    const rl = await rateLimitRegistration({ userId: input.userId, ipAddress: input.ipAddress });
    if (!rl.allowed) return { ok: false as const, code: "rate_limited" as const, retryAfterSeconds: rl.retryAfterSeconds };

    const { rpId, rpName } = webAuthnConfigForRequest(input.request);

    const sql = getSql();
    const existing = await sql<{ credential_id: string }[]>`
        select credential_id
        from user_passkeys
        where user_id = ${input.userId}::uuid
        order by created_at asc
        limit 50
    `;

    const options: PublicKeyCredentialCreationOptionsJSON = await generateRegistrationOptions({
        rpName,
        rpID: rpId,
        userID: Buffer.from(input.userId, "utf8"),
        userName: input.email,
        userDisplayName: input.displayName?.trim() ? input.displayName.trim() : input.email,
        attestationType: "none",
        authenticatorSelection: {
            residentKey: "required",
            userVerification: "required",
        },
        excludeCredentials: existing
            .map((r) => r.credential_id)
            .filter(Boolean)
            .map((credentialId) => ({
                id: credentialId,
                type: "public-key",
            })),
    });

    await storeWebAuthnChallenge({
        challenge: options.challenge,
        kind: "registration",
        userId: input.userId,
        ipAddress: input.ipAddress,
        userAgent: input.userAgent,
    });

    return { ok: true as const, options };
}

export async function verifyPasskeyRegistration(input: {
    request: Request;
    userId: string;
    deviceName?: string;
    ipAddress: string;
    userAgent: string;
    attestation: RegistrationResponseJSON;
}) {
    if (!isDatabaseConfigured()) return { ok: false as const, code: "db_unavailable" as const };
    await ensureAuthSchema();

    const rl = await rateLimitRegistration({ userId: input.userId, ipAddress: input.ipAddress });
    if (!rl.allowed) return { ok: false as const, code: "rate_limited" as const, retryAfterSeconds: rl.retryAfterSeconds };

    const challenge = parseClientDataJsonChallenge(input.attestation.response.clientDataJSON);
    if (!challenge) return { ok: false as const, code: "invalid_attestation" as const };

    const consumed = await consumeWebAuthnChallenge({
        challenge,
        kind: "registration",
        userId: input.userId,
        ipAddress: input.ipAddress,
        userAgent: input.userAgent,
    });
    if (!consumed) return { ok: false as const, code: "invalid_or_expired_challenge" as const };

    const { rpId, expectedOrigins } = webAuthnConfigForRequest(input.request);
    const verification = await verifyRegistrationResponse({
        response: input.attestation,
        expectedChallenge: challenge,
        expectedOrigin: expectedOrigins,
        expectedRPID: rpId,
        requireUserVerification: true,
    }).catch(() => null);

    if (!verification?.verified || !verification.registrationInfo) return { ok: false as const, code: "invalid_attestation" as const };

    const credentialId = verification.registrationInfo.credential.id;
    const publicKey = Buffer.from(verification.registrationInfo.credential.publicKey);
    const counter = verification.registrationInfo.credential.counter ?? 0;

    const sql = getSql();
    try {
        const rows = await sql<{ id: string }[]>`
            insert into user_passkeys (id, user_id, credential_id, public_key, sign_count, device_name, created_at, updated_at)
            values (
                ${crypto.randomUUID()}::uuid,
                ${input.userId}::uuid,
                ${credentialId},
                ${publicKey},
                ${Math.max(0, Math.floor(counter))}::bigint,
                ${input.deviceName?.trim() ? input.deviceName.trim() : null},
                now(),
                now()
            )
            returning id
        `;
        void rows;
    } catch (err) {
        const message = err instanceof Error ? err.message : "";
        if (/user_passkeys_credential_id_unique/i.test(message) || /unique/i.test(message)) {
            return { ok: false as const, code: "duplicate_credential" as const };
        }
        throw err;
    }

    emitPasskeyEvent({ name: "passkey_registered", userId: input.userId, credentialId, ipAddress: input.ipAddress, at: new Date().toISOString() });
    return { ok: true as const };
}

type DbPasskeyRow = {
    id: string;
    user_id: string;
    credential_id: string;
    public_key: Uint8Array;
    sign_count: bigint;
    status: string;
    email: string;
    role: AuthRole;
};

export async function getPasskeyAuthenticationOptions(input: { request: Request; identifier: string; ipAddress: string; userAgent: string }) {
    if (!isDatabaseConfigured()) return { ok: false as const, code: "db_unavailable" as const };
    await ensureAuthSchema();

    const rl = await rateLimitLogin({ identifier: input.identifier, ipAddress: input.ipAddress });
    if (!rl.allowed) return { ok: false as const, code: "rate_limited" as const, retryAfterSeconds: rl.retryAfterSeconds };

    const { rpId } = webAuthnConfigForRequest(input.request);
    const options: PublicKeyCredentialRequestOptionsJSON = await generateAuthenticationOptions({
        rpID: rpId,
        userVerification: "preferred",
    });

    await storeWebAuthnChallenge({
        challenge: options.challenge,
        kind: "authentication",
        identifier: input.identifier,
        ipAddress: input.ipAddress,
        userAgent: input.userAgent,
    });

    return { ok: true as const, options };
}

export async function verifyPasskeyAuthentication(input: {
    request: Request;
    assertion: AuthenticationResponseJSON;
    ipAddress: string;
    userAgent: string;
    totpCode?: string;
    recoveryCode?: string;
}) {
    if (!isDatabaseConfigured()) return { ok: false as const, code: "db_unavailable" as const };
    await ensureAuthSchema();

    const credentialId = String(input.assertion.id ?? "").trim();
    if (!credentialId) return { ok: false as const, code: "invalid_assertion" as const };

    const challenge = parseClientDataJsonChallenge(input.assertion.response.clientDataJSON);
    if (!challenge) return { ok: false as const, code: "invalid_assertion" as const };

    const rl = await rateLimitLogin({ identifier: credentialId, ipAddress: input.ipAddress });
    if (!rl.allowed) return { ok: false as const, code: "rate_limited" as const, retryAfterSeconds: rl.retryAfterSeconds };

    const consumed = await consumeWebAuthnChallenge({
        challenge,
        kind: "authentication",
        ipAddress: input.ipAddress,
        userAgent: input.userAgent,
    });
    if (!consumed) return { ok: false as const, code: "invalid_or_expired_challenge" as const };

    const sql = getSql();
    const rows = await sql<DbPasskeyRow[]>`
        select
            p.id,
            p.user_id,
            p.credential_id,
            p.public_key,
            p.sign_count,
            u.status,
            u.email,
            u.role
        from user_passkeys p
        join users u on u.id = p.user_id
        where p.credential_id = ${credentialId}
        limit 1
    `;
    const passkey = rows[0] ?? null;
    if (!passkey || passkey.status !== "active") {
        emitPasskeyEvent({ name: "passkey_login_failed", credentialId, ipAddress: input.ipAddress, at: new Date().toISOString() });
        return { ok: false as const, code: "invalid_credentials" as const };
    }

    {
        const perUserPerMinute = parsePositiveInt(process.env.PASSKEY_LOGIN_LIMIT_PER_USER_PER_MINUTE, 10);
        const userLimit = await consumeAuthRateLimit({
            bucket: `passkeys:login:user:${passkey.user_id}`,
            limit: perUserPerMinute,
            windowSeconds: 60,
        });
        if (!userLimit.allowed) {
            return { ok: false as const, code: "rate_limited" as const, retryAfterSeconds: userLimit.retryAfterSeconds };
        }
    }

    const { rpId, expectedOrigins } = webAuthnConfigForRequest(input.request);

    const verification = await verifyAuthenticationResponse({
        response: input.assertion,
        expectedChallenge: challenge,
        expectedOrigin: expectedOrigins,
        expectedRPID: rpId,
        requireUserVerification: true,
        credential: {
            id: passkey.credential_id,
            publicKey: new Uint8Array(passkey.public_key),
            counter: Number(passkey.sign_count),
        },
    }).catch(() => null);

    if (!verification?.verified || !verification.authenticationInfo) {
        emitPasskeyEvent({ name: "passkey_login_failed", userId: passkey.user_id, credentialId, ipAddress: input.ipAddress, at: new Date().toISOString() });
        return { ok: false as const, code: "invalid_credentials" as const };
    }

    const prev = Number(passkey.sign_count);
    const next = verification.authenticationInfo.newCounter ?? prev;
    if (Number.isFinite(next) && next !== 0 && next <= prev) {
        emitPasskeyEvent({ name: "passkey_login_failed", userId: passkey.user_id, credentialId, ipAddress: input.ipAddress, at: new Date().toISOString() });
        return { ok: false as const, code: "invalid_credentials" as const };
    }

    if (Number.isFinite(next) && next > prev) {
        await sql`
            update user_passkeys
            set sign_count = ${Math.max(0, Math.floor(next))}::bigint, updated_at = now()
            where id = ${passkey.id}::uuid
        `;
    }

    const mfa = await verifyMfaForLogin({
        userId: passkey.user_id,
        ipAddress: input.ipAddress,
        totpCode: input.totpCode,
        recoveryCode: input.recoveryCode,
    });
    if (!mfa.ok) {
        emitPasskeyEvent({ name: "passkey_login_failed", userId: passkey.user_id, credentialId, ipAddress: input.ipAddress, at: new Date().toISOString() });
        return mfa.code === "rate_limited"
            ? ({ ok: false as const, code: "rate_limited" as const, retryAfterSeconds: mfa.retryAfterSeconds } as const)
            : ({ ok: false as const, code: mfa.code === "mfa_required" ? ("mfa_required" as const) : ("service_unavailable" as const) } as const);
    }

    emitPasskeyEvent({ name: "passkey_login_succeeded", userId: passkey.user_id, credentialId, ipAddress: input.ipAddress, at: new Date().toISOString() });
    return { ok: true as const, user: { id: passkey.user_id, email: passkey.email, role: passkey.role }, mfaVerified: mfa.required };
}
