import crypto from "node:crypto";
import { consumeAuthRateLimit, ensureAuthSchema, getUserPasswordHashById, hashPassword, verifyPassword } from "@/server/auth-core";
import { getSql, isDatabaseConfigured } from "@/server/db";

const BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

export type MfaEventName = "mfa_enabled" | "mfa_disabled" | "mfa_verification_succeeded" | "mfa_verification_failed" | "recovery_code_used";
export type MfaEvent = {
    name: MfaEventName;
    userId: string;
    ipAddress: string;
    method?: "totp" | "recovery";
    at: string;
};

type MfaEventHandler = (event: MfaEvent) => void | Promise<void>;

let mfaEventHandler: MfaEventHandler | null = null;

export function setMfaEventHandler(handler: MfaEventHandler | null) {
    mfaEventHandler = handler;
}

function emitMfaEvent(event: MfaEvent) {
    const h = mfaEventHandler;
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

function mfaIssuer() {
    const raw = process.env.MFA_TOTP_ISSUER;
    const v = raw ? String(raw).trim() : "";
    return v || "Talk-Lee";
}

function mfaEncryptionKeyBytes() {
    const raw = process.env.MFA_ENCRYPTION_KEY_BASE64;
    if (!raw || !String(raw).trim()) throw new Error("Missing environment variable: MFA_ENCRYPTION_KEY_BASE64");
    const buf = Buffer.from(String(raw).trim(), "base64");
    if (buf.length !== 32) throw new Error("MFA_ENCRYPTION_KEY_BASE64 must be 32 bytes (base64-encoded)");
    return buf;
}

function base32Encode(buf: Uint8Array) {
    let bits = 0;
    let value = 0;
    let output = "";
    for (const b of buf) {
        value = (value << 8) | b;
        bits += 8;
        while (bits >= 5) {
            const idx = (value >>> (bits - 5)) & 31;
            output += BASE32_ALPHABET[idx]!;
            bits -= 5;
        }
    }
    if (bits > 0) {
        const idx = (value << (5 - bits)) & 31;
        output += BASE32_ALPHABET[idx]!;
    }
    return output;
}

function base32Decode(input: string) {
    const cleaned = input
        .toUpperCase()
        .replace(/=+$/g, "")
        .replace(/[^A-Z2-7]/g, "");
    if (!cleaned) return new Uint8Array();
    let bits = 0;
    let value = 0;
    const bytes: number[] = [];
    for (const ch of cleaned) {
        const idx = BASE32_ALPHABET.indexOf(ch);
        if (idx < 0) continue;
        value = (value << 5) | idx;
        bits += 5;
        if (bits >= 8) {
            bytes.push((value >>> (bits - 8)) & 0xff);
            bits -= 8;
        }
    }
    return new Uint8Array(bytes);
}

function timingSafeEqualStrings(a: string, b: string) {
    const aBuf = Buffer.from(a);
    const bBuf = Buffer.from(b);
    if (aBuf.length !== bBuf.length) return false;
    return crypto.timingSafeEqual(aBuf, bBuf);
}

function encryptSecret(plaintext: string) {
    const key = mfaEncryptionKeyBytes();
    const iv = crypto.randomBytes(12);
    const cipher = crypto.createCipheriv("aes-256-gcm", key, iv);
    const ciphertext = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
    const tag = cipher.getAuthTag();
    return `v1.${iv.toString("base64")}.${tag.toString("base64")}.${ciphertext.toString("base64")}`;
}

function decryptSecret(payload: string) {
    const raw = String(payload ?? "").trim();
    const parts = raw.split(".");
    if (parts.length !== 4 || parts[0] !== "v1") throw new Error("Invalid MFA secret payload");
    const iv = Buffer.from(parts[1]!, "base64");
    const tag = Buffer.from(parts[2]!, "base64");
    const ciphertext = Buffer.from(parts[3]!, "base64");
    const key = mfaEncryptionKeyBytes();
    const decipher = crypto.createDecipheriv("aes-256-gcm", key, iv);
    decipher.setAuthTag(tag);
    const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
    return plaintext.toString("utf8");
}

function tryDecryptSecret(payload: string) {
    try {
        return decryptSecret(payload);
    } catch {
        return null;
    }
}

function totpParams() {
    return {
        digits: parsePositiveInt(process.env.MFA_TOTP_DIGITS, 6),
        periodSeconds: parsePositiveInt(process.env.MFA_TOTP_PERIOD_SECONDS, 30),
        algorithm: (process.env.MFA_TOTP_ALGORITHM || "SHA1").toUpperCase(),
    };
}

function hotp(input: { secret: Uint8Array; counter: bigint; digits: number; algorithm: string }) {
    const counterBuf = Buffer.alloc(8);
    counterBuf.writeBigUInt64BE(input.counter);
    const algo = input.algorithm.toLowerCase();
    const hmac = crypto.createHmac(algo, Buffer.from(input.secret));
    hmac.update(counterBuf);
    const digest = hmac.digest();
    const offset = digest[digest.length - 1]! & 0x0f;
    const code =
        ((digest[offset]! & 0x7f) << 24) |
        ((digest[offset + 1]! & 0xff) << 16) |
        ((digest[offset + 2]! & 0xff) << 8) |
        (digest[offset + 3]! & 0xff);
    const mod = 10 ** input.digits;
    const out = String(code % mod).padStart(input.digits, "0");
    return out;
}

export function totpCodeAtTime(input: { secretBase32: string; nowMs: number }) {
    const params = totpParams();
    const digits = params.digits;
    const periodSeconds = params.periodSeconds;
    const secret = base32Decode(input.secretBase32);
    const step = BigInt(Math.floor(input.nowMs / 1000 / periodSeconds));
    return hotp({ secret, counter: step, digits, algorithm: params.algorithm });
}

export function verifyTotpCode(input: { secretBase32: string; code: string; nowMs: number; window: number }) {
    const params = totpParams();
    const digits = params.digits;
    const periodSeconds = params.periodSeconds;

    const normalizedCode = String(input.code ?? "").trim().replace(/\s+/g, "");
    if (!/^\d+$/.test(normalizedCode) || normalizedCode.length !== digits) return { ok: false as const };

    const secret = base32Decode(input.secretBase32);
    if (secret.length === 0) return { ok: false as const };

    const step = BigInt(Math.floor(input.nowMs / 1000 / periodSeconds));
    for (let w = -input.window; w <= input.window; w++) {
        const counter = step + BigInt(w);
        if (counter < BigInt(0)) continue;
        const expected = hotp({ secret, counter, digits, algorithm: params.algorithm });
        if (timingSafeEqualStrings(expected, normalizedCode)) {
            return { ok: true as const, counter };
        }
    }
    return { ok: false as const };
}

function buildOtpAuthUri(input: { accountName: string; secretBase32: string }) {
    const params = totpParams();
    const issuer = mfaIssuer();
    const label = `${issuer}:${input.accountName}`;
    const q = new URLSearchParams({
        secret: input.secretBase32,
        issuer,
        algorithm: params.algorithm,
        digits: String(params.digits),
        period: String(params.periodSeconds),
    });
    return `otpauth://totp/${encodeURIComponent(label)}?${q.toString()}`;
}

function generateSecretBase32() {
    const bytes = crypto.randomBytes(20);
    return base32Encode(bytes);
}

function normalizeRecoveryCode(input: string) {
    return String(input ?? "")
        .trim()
        .toUpperCase()
        .replace(/[^A-Z0-9]/g, "");
}

function formatRecoveryCode(input: string) {
    const s = normalizeRecoveryCode(input);
    const groups: string[] = [];
    for (let i = 0; i < s.length; i += 4) groups.push(s.slice(i, i + 4));
    return groups.join("-");
}

async function generateRecoveryCodes(input: { count: number }) {
    const codes: string[] = [];
    for (let i = 0; i < input.count; i++) {
        const raw = base32Encode(crypto.randomBytes(10));
        codes.push(formatRecoveryCode(raw));
    }
    const hashes = await Promise.all(codes.map((c) => hashPassword(normalizeRecoveryCode(c))));
    return { codes, hashes };
}

function mfaVerifyLimits() {
    return {
        perUserPerMinute: parsePositiveInt(process.env.MFA_VERIFY_LIMIT_PER_USER_PER_MINUTE, 10),
        perIpPerMinute: parsePositiveInt(process.env.MFA_VERIFY_LIMIT_PER_IP_PER_MINUTE, 30),
        recoveryPerUserPerMinute: parsePositiveInt(process.env.MFA_RECOVERY_LIMIT_PER_USER_PER_MINUTE, 10),
        recoveryPerIpPerMinute: parsePositiveInt(process.env.MFA_RECOVERY_LIMIT_PER_IP_PER_MINUTE, 30),
    };
}

async function rateLimitMfaVerify(input: { userId: string; ipAddress: string; kind: "totp" | "recovery" }) {
    const limits = mfaVerifyLimits();
    const perUser = input.kind === "totp" ? limits.perUserPerMinute : limits.recoveryPerUserPerMinute;
    const perIp = input.kind === "totp" ? limits.perIpPerMinute : limits.recoveryPerIpPerMinute;

    const ip = input.ipAddress || "unknown";
    const [ipLimit, userLimit] = await Promise.all([
        consumeAuthRateLimit({ bucket: `mfa:${input.kind}:ip:${ip}`, limit: perIp, windowSeconds: 60 }),
        consumeAuthRateLimit({ bucket: `mfa:${input.kind}:user:${input.userId}`, limit: perUser, windowSeconds: 60 }),
    ]);
    return {
        allowed: ipLimit.allowed && userLimit.allowed,
        retryAfterSeconds: Math.max(ipLimit.retryAfterSeconds, userLimit.retryAfterSeconds),
    };
}

async function consumeTotpReplayBucket(input: { userId: string; counter: bigint; periodSeconds: number }) {
    const windowSeconds = Math.max(60, input.periodSeconds * 2);
    const r = await consumeAuthRateLimit({ bucket: `mfa:totp:replay:${input.userId}:${input.counter.toString()}`, limit: 1, windowSeconds });
    return r.allowed;
}

export async function startTotpEnrollment(input: { userId: string; email: string }) {
    if (!isDatabaseConfigured()) return { ok: false as const, code: "db_unavailable" as const };
    await ensureAuthSchema();
    const sql = getSql();

    const rows = await sql<{ is_enabled: boolean }[]>`
        select is_enabled
        from user_mfa
        where user_id = ${input.userId}::uuid
        limit 1
    `;
    if (rows[0]?.is_enabled) return { ok: false as const, code: "already_enabled" as const };

    const secretBase32 = generateSecretBase32();
    let secretEncrypted = "";
    try {
        secretEncrypted = encryptSecret(secretBase32);
    } catch {
        return { ok: false as const, code: "service_unavailable" as const };
    }

    await sql`
        insert into user_mfa (user_id, secret_encrypted, is_enabled, created_at, updated_at)
        values (${input.userId}::uuid, ${secretEncrypted}, false, now(), now())
        on conflict (user_id)
        do update set secret_encrypted = ${secretEncrypted}, is_enabled = false, updated_at = now()
    `;

    return { ok: true as const, otpauthUri: buildOtpAuthUri({ accountName: input.email, secretBase32 }), secretBase32 };
}

export async function verifyTotpEnrollment(input: { userId: string; ipAddress: string; code: string }) {
    if (!isDatabaseConfigured()) return { ok: false as const, code: "db_unavailable" as const };
    await ensureAuthSchema();
    const sql = getSql();

    const rows = await sql<{ secret_encrypted: string; is_enabled: boolean }[]>`
        select secret_encrypted, is_enabled
        from user_mfa
        where user_id = ${input.userId}::uuid
        limit 1
    `;
    const rec = rows[0] ?? null;
    if (!rec) return { ok: false as const, code: "enrollment_not_started" as const };
    if (rec.is_enabled) return { ok: true as const, alreadyEnabled: true as const };

    const rl = await rateLimitMfaVerify({ userId: input.userId, ipAddress: input.ipAddress, kind: "totp" });
    if (!rl.allowed) return { ok: false as const, code: "rate_limited" as const, retryAfterSeconds: rl.retryAfterSeconds };

    const secretBase32 = tryDecryptSecret(rec.secret_encrypted);
    if (!secretBase32) return { ok: false as const, code: "service_unavailable" as const };
    const nowMs = Date.now();
    const v = verifyTotpCode({ secretBase32, code: input.code, nowMs, window: 1 });
    if (!v.ok) {
        emitMfaEvent({ name: "mfa_verification_failed", userId: input.userId, ipAddress: input.ipAddress, method: "totp", at: new Date().toISOString() });
        return { ok: false as const, code: "invalid_code" as const };
    }

    const replayOk = await consumeTotpReplayBucket({ userId: input.userId, counter: v.counter, periodSeconds: totpParams().periodSeconds });
    if (!replayOk) {
        emitMfaEvent({ name: "mfa_verification_failed", userId: input.userId, ipAddress: input.ipAddress, method: "totp", at: new Date().toISOString() });
        return { ok: false as const, code: "invalid_code" as const };
    }

    const { codes, hashes } = await generateRecoveryCodes({ count: parsePositiveInt(process.env.MFA_RECOVERY_CODE_COUNT, 10) });

    await sql`
        update user_mfa
        set is_enabled = true, updated_at = now()
        where user_id = ${input.userId}::uuid
    `;
    await sql`delete from recovery_codes where user_id = ${input.userId}::uuid`;
    for (let i = 0; i < hashes.length; i++) {
        await sql`
            insert into recovery_codes (id, user_id, code_hash, used, created_at)
            values (${crypto.randomUUID()}::uuid, ${input.userId}::uuid, ${hashes[i]!}, false, now())
        `;
    }

    emitMfaEvent({ name: "mfa_enabled", userId: input.userId, ipAddress: input.ipAddress, at: new Date().toISOString() });
    return { ok: true as const, recoveryCodes: codes };
}

export async function disableTotpMfa(input: { userId: string; ipAddress: string; password?: string; totpCode?: string }) {
    if (!isDatabaseConfigured()) return { ok: false as const, code: "db_unavailable" as const };
    await ensureAuthSchema();
    const sql = getSql();

    const hasPassword = Boolean(input.password && String(input.password).trim());
    const hasTotp = Boolean(input.totpCode && String(input.totpCode).trim());
    if (!hasPassword && !hasTotp) return { ok: false as const, code: "verification_required" as const };

    if (hasPassword) {
        const hash = await getUserPasswordHashById(input.userId);
        if (!hash) return { ok: false as const, code: "invalid_verification" as const };
        const ok = await verifyPassword(hash, String(input.password));
        if (!ok) return { ok: false as const, code: "invalid_verification" as const };
    } else {
        const rl = await rateLimitMfaVerify({ userId: input.userId, ipAddress: input.ipAddress, kind: "totp" });
        if (!rl.allowed) return { ok: false as const, code: "rate_limited" as const, retryAfterSeconds: rl.retryAfterSeconds };

        const rows = await sql<{ secret_encrypted: string; is_enabled: boolean }[]>`
            select secret_encrypted, is_enabled
            from user_mfa
            where user_id = ${input.userId}::uuid
            limit 1
        `;
        const rec = rows[0] ?? null;
        if (!rec?.is_enabled) return { ok: false as const, code: "invalid_verification" as const };

        const secretBase32 = tryDecryptSecret(rec.secret_encrypted);
        if (!secretBase32) return { ok: false as const, code: "service_unavailable" as const };
        const nowMs = Date.now();
        const v = verifyTotpCode({ secretBase32, code: String(input.totpCode), nowMs, window: 1 });
        if (!v.ok) return { ok: false as const, code: "invalid_verification" as const };

        const replayOk = await consumeTotpReplayBucket({ userId: input.userId, counter: v.counter, periodSeconds: totpParams().periodSeconds });
        if (!replayOk) return { ok: false as const, code: "invalid_verification" as const };
    }

    await sql`delete from recovery_codes where user_id = ${input.userId}::uuid`;
    await sql`delete from user_mfa where user_id = ${input.userId}::uuid`;

    emitMfaEvent({ name: "mfa_disabled", userId: input.userId, ipAddress: input.ipAddress, at: new Date().toISOString() });
    return { ok: true as const };
}

export async function isMfaEnabledForUser(userId: string) {
    if (!isDatabaseConfigured()) return false;
    await ensureAuthSchema();
    const sql = getSql();
    const rows = await sql<{ is_enabled: boolean }[]>`
        select is_enabled
        from user_mfa
        where user_id = ${userId}::uuid
        limit 1
    `;
    return Boolean(rows[0]?.is_enabled);
}

export async function verifyMfaForLogin(input: { userId: string; ipAddress: string; totpCode?: string; recoveryCode?: string }) {
    if (!isDatabaseConfigured()) return { ok: false as const, code: "db_unavailable" as const };
    await ensureAuthSchema();
    const sql = getSql();

    const rows = await sql<{ secret_encrypted: string; is_enabled: boolean }[]>`
        select secret_encrypted, is_enabled
        from user_mfa
        where user_id = ${input.userId}::uuid
        limit 1
    `;
    const rec = rows[0] ?? null;
    if (!rec?.is_enabled) return { ok: true as const, required: false as const };

    const totpCode = String(input.totpCode ?? "").trim();
    const recoveryCode = String(input.recoveryCode ?? "").trim();
    if (!totpCode && !recoveryCode) {
        emitMfaEvent({ name: "mfa_verification_failed", userId: input.userId, ipAddress: input.ipAddress, at: new Date().toISOString() });
        return { ok: false as const, code: "mfa_required" as const };
    }

    if (totpCode) {
        const rl = await rateLimitMfaVerify({ userId: input.userId, ipAddress: input.ipAddress, kind: "totp" });
        if (!rl.allowed) return { ok: false as const, code: "rate_limited" as const, retryAfterSeconds: rl.retryAfterSeconds };

        const secretBase32 = tryDecryptSecret(rec.secret_encrypted);
        if (!secretBase32) return { ok: false as const, code: "service_unavailable" as const };
        const nowMs = Date.now();
        const v = verifyTotpCode({ secretBase32, code: totpCode, nowMs, window: 1 });
        if (!v.ok) {
            emitMfaEvent({ name: "mfa_verification_failed", userId: input.userId, ipAddress: input.ipAddress, method: "totp", at: new Date().toISOString() });
            return { ok: false as const, code: "invalid_mfa" as const };
        }

        const replayOk = await consumeTotpReplayBucket({ userId: input.userId, counter: v.counter, periodSeconds: totpParams().periodSeconds });
        if (!replayOk) {
            emitMfaEvent({ name: "mfa_verification_failed", userId: input.userId, ipAddress: input.ipAddress, method: "totp", at: new Date().toISOString() });
            return { ok: false as const, code: "invalid_mfa" as const };
        }

        emitMfaEvent({ name: "mfa_verification_succeeded", userId: input.userId, ipAddress: input.ipAddress, method: "totp", at: new Date().toISOString() });
        return { ok: true as const, required: true as const, method: "totp" as const };
    }

    const rl = await rateLimitMfaVerify({ userId: input.userId, ipAddress: input.ipAddress, kind: "recovery" });
    if (!rl.allowed) return { ok: false as const, code: "rate_limited" as const, retryAfterSeconds: rl.retryAfterSeconds };

    const normalized = normalizeRecoveryCode(recoveryCode);
    if (!normalized) {
        emitMfaEvent({ name: "mfa_verification_failed", userId: input.userId, ipAddress: input.ipAddress, method: "recovery", at: new Date().toISOString() });
        return { ok: false as const, code: "invalid_mfa" as const };
    }

    const candidates = await sql<{ id: string; code_hash: string }[]>`
        select id, code_hash
        from recovery_codes
        where user_id = ${input.userId}::uuid and used = false
        order by created_at asc
        limit 20
    `;
    for (const c of candidates) {
        const match = await verifyPassword(c.code_hash, normalized).catch(() => false);
        if (!match) continue;
        const updated = await sql<{ id: string }[]>`
            update recovery_codes
            set used = true
            where id = ${c.id}::uuid and used = false
            returning id
        `;
        if (updated.length === 1) {
            emitMfaEvent({ name: "recovery_code_used", userId: input.userId, ipAddress: input.ipAddress, method: "recovery", at: new Date().toISOString() });
            emitMfaEvent({ name: "mfa_verification_succeeded", userId: input.userId, ipAddress: input.ipAddress, method: "recovery", at: new Date().toISOString() });
            return { ok: true as const, required: true as const, method: "recovery" as const };
        }
        emitMfaEvent({ name: "mfa_verification_failed", userId: input.userId, ipAddress: input.ipAddress, method: "recovery", at: new Date().toISOString() });
        return { ok: false as const, code: "invalid_mfa" as const };
    }

    emitMfaEvent({ name: "mfa_verification_failed", userId: input.userId, ipAddress: input.ipAddress, method: "recovery", at: new Date().toISOString() });
    return { ok: false as const, code: "invalid_mfa" as const };
}
