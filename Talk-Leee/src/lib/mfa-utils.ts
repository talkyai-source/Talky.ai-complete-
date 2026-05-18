/**
 * MFA client utilities — wraps the backend's RFC 6238 TOTP MFA stack.
 *
 * Backend wiring (verified against deployed api.talkleeai.com):
 *   POST   /api/v1/auth/mfa/setup                 → start enrolment (returns QR + provisioning URI)
 *   POST   /api/v1/auth/mfa/confirm               → activate after first TOTP (returns recovery codes ONCE)
 *   POST   /api/v1/auth/mfa/verify                → step-2 of login (challenge token + code)
 *   GET    /api/v1/auth/mfa/status                → enabled flag + recovery codes remaining
 *   POST   /api/v1/auth/mfa/disable               → requires password
 *   POST   /api/v1/auth/mfa/recovery-codes/regenerate → requires current TOTP
 *
 * Security properties (already enforced server-side, listed so callers don't
 * have to re-implement them):
 *  • TOTP secrets encrypted at rest (Fernet, AES-128-CBC + HMAC-SHA256)
 *  • Replay prevention — same 30-second slot rejected
 *  • Time skew ±1 step
 *  • Recovery codes hashed at rest, single-use, returned ONLY at confirm time
 *  • Setup → Confirm pattern: enrolment doesn't activate until first TOTP is proven
 *  • Login challenge tokens expire in 5 minutes, single-use
 *  • Disable requires password reauth; regenerate codes requires current TOTP
 *  • Generic error messages on verify failures (no account enumeration)
 *
 * Prior versions of this file hit /api/auth/mfa/setup/start etc. — those
 * routes don't exist on this backend (404), which is why the Settings page
 * showed a never-ending spinner.
 */

import { apiBaseUrl } from "@/lib/env";
import { getBrowserAuthToken } from "@/lib/auth-token";

// ----- Response types --------------------------------------------------------

export interface MFASetupResponse {
    /** Embed directly in <img src=...> — backend already generated the PNG. */
    qrCode: string;
    /** Raw base32 secret for manual entry in the authenticator app. */
    manualEntryKey: string;
    /** The full otpauth:// URI in case the user wants to copy it manually. */
    provisioningUri: string;
    issuer: string;
    account: string;
}

export interface MFAConfirmResponse {
    enabled: true;
    /** Single-use recovery codes — backend will never return these again. */
    recoveryCodes: string[];
    recoveryCodesCount: number;
    message: string;
}

export interface MFAStatusResponse {
    enabled: boolean;
    verifiedAt: string | null;
    recoveryCodesRemaining: number;
}

// ----- Validation helpers ---------------------------------------------------

export function validateTotpCode(code: string): boolean {
    const cleanCode = code.trim().replace(/\D/g, "");
    return cleanCode.length >= 6 && cleanCode.length <= 8;
}

// ----- Display / clipboard helpers ------------------------------------------

export function formatRecoveryCodes(codes: string[]): string {
    return codes.join("\n");
}

export function downloadRecoveryCodes(
    codes: string[],
    filename: string = "talky-mfa-recovery-codes.txt",
): void {
    const content =
        `Talky.ai — MFA Recovery Codes\n` +
        `Generated: ${new Date().toISOString()}\n\n` +
        formatRecoveryCodes(codes) +
        `\n\nStore these codes in a safe place. Each code can only be used once.\n` +
        `If you lose access to your authenticator app, use one of these codes\n` +
        `to sign in and then re-enrol MFA.`;
    const blob = new Blob([content], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

export async function copyToClipboard(text: string): Promise<boolean> {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        return false;
    }
}

/** Seconds remaining in the current 30-second TOTP window. */
export function getTimeRemaining(): number {
    const timeStep = 30;
    return timeStep - (Math.floor(Date.now() / 1000) % timeStep);
}

// ----- HTTP plumbing --------------------------------------------------------

function authHeaders(token?: string): HeadersInit {
    const bearer = token || getBrowserAuthToken() || "";
    return bearer ? { Authorization: `Bearer ${bearer}` } : {};
}

async function postJson<T>(path: string, token: string, body?: unknown): Promise<T> {
    const base = apiBaseUrl();
    const res = await fetch(`${base}${path}`, {
        method: "POST",
        credentials: "include",
        headers: {
            "Content-Type": "application/json",
            ...authHeaders(token),
        },
        body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
        let detail = `Request failed (${res.status})`;
        try {
            const j = await res.json();
            detail = j?.detail || j?.error?.message || detail;
        } catch {
            // ignore
        }
        throw new Error(detail);
    }
    return (await res.json()) as T;
}

async function getJson<T>(path: string, token: string): Promise<T> {
    const base = apiBaseUrl();
    const res = await fetch(`${base}${path}`, {
        method: "GET",
        credentials: "include",
        headers: authHeaders(token),
    });
    if (!res.ok) {
        let detail = `Request failed (${res.status})`;
        try {
            const j = await res.json();
            detail = j?.detail || j?.error?.message || detail;
        } catch {
            // ignore
        }
        throw new Error(detail);
    }
    return (await res.json()) as T;
}

/**
 * Extracts the raw base32 secret from an otpauth:// URI.
 * The backend doesn't return the secret separately — by design (the secret
 * stays server-side until the user scans). We pull it out of the
 * provisioning URI for the manual-entry display.
 */
function extractSecretFromUri(uri: string): string {
    try {
        const u = new URL(uri.replace(/^otpauth:/, "https:"));
        return (u.searchParams.get("secret") || "").toUpperCase();
    } catch {
        return "";
    }
}

// ----- Public API -----------------------------------------------------------

export async function startMfaSetup(token: string): Promise<MFASetupResponse> {
    type Raw = {
        provisioning_uri: string;
        qr_code: string;
        issuer: string;
        account: string;
    };
    const raw = await postJson<Raw>("/auth/mfa/setup", token);
    return {
        qrCode: raw.qr_code,
        manualEntryKey: extractSecretFromUri(raw.provisioning_uri),
        provisioningUri: raw.provisioning_uri,
        issuer: raw.issuer,
        account: raw.account,
    };
}

export async function confirmMfaSetup(token: string, code: string): Promise<MFAConfirmResponse> {
    type Raw = {
        enabled: boolean;
        recovery_codes: string[];
        recovery_codes_count: number;
        message: string;
    };
    const raw = await postJson<Raw>("/auth/mfa/confirm", token, {
        code: code.trim().replace(/\D/g, ""),
    });
    return {
        enabled: true,
        recoveryCodes: raw.recovery_codes,
        recoveryCodesCount: raw.recovery_codes_count,
        message: raw.message,
    };
}

export async function getMfaStatus(token: string): Promise<MFAStatusResponse> {
    type Raw = {
        enabled: boolean;
        verified_at: string | null;
        recovery_codes_remaining: number;
    };
    const raw = await getJson<Raw>("/auth/mfa/status", token);
    return {
        enabled: raw.enabled,
        verifiedAt: raw.verified_at,
        recoveryCodesRemaining: raw.recovery_codes_remaining,
    };
}

export async function disableMfa(token: string, password: string): Promise<{ success: boolean }> {
    await postJson<unknown>("/auth/mfa/disable", token, { password });
    return { success: true };
}

export async function regenerateRecoveryCodes(
    token: string,
    code: string,
): Promise<{ recoveryCodes: string[] }> {
    type Raw = { recovery_codes: string[]; recovery_codes_count: number; message: string };
    const raw = await postJson<Raw>("/auth/mfa/recovery-codes/regenerate", token, {
        code: code.trim().replace(/\D/g, ""),
    });
    return { recoveryCodes: raw.recovery_codes };
}

/**
 * @deprecated Use `verifyMfaChallenge` — the real backend flow needs the
 * challenge_token from the login response, not the email. Kept as a stub
 * to keep `mfa-verification.tsx` compiling while the login flow is being
 * refactored to forward `challenge_token`.
 */
export async function verifyMfaLogin(
    _email: string,
    _totpCode: string,
): Promise<{ access_token: string; refresh_token: string }> {
    throw new Error(
        "MFA login challenge requires challenge_token. " +
        "Refactor login response to expose it, then call verifyMfaChallenge.",
    );
}

/**
 * Step-2 of the two-step login flow.
 * Caller supplies the challenge token from the login response, plus one of
 * `code` (TOTP) or `recoveryCode` (backup code).
 */
export async function verifyMfaChallenge(input: {
    challengeToken: string;
    code?: string;
    recoveryCode?: string;
}): Promise<{ accessToken: string; userId: string; email: string; role: string }> {
    const base = apiBaseUrl();
    const res = await fetch(`${base}/auth/mfa/verify`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            challenge_token: input.challengeToken,
            code: input.code ? input.code.trim().replace(/\D/g, "") : undefined,
            recovery_code: input.recoveryCode?.trim() || undefined,
        }),
    });
    if (!res.ok) {
        throw new Error("Invalid MFA code");
    }
    const raw = (await res.json()) as {
        access_token: string;
        user_id: string;
        email: string;
        role: string;
    };
    return {
        accessToken: raw.access_token,
        userId: raw.user_id,
        email: raw.email,
        role: raw.role,
    };
}
