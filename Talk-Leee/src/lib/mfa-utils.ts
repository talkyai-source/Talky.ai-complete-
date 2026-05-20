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

import { api } from "@/lib/api";

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
//
// Phase 4 of the universal-auth-state plan removed the bare-fetch helpers
// (postJson / getJson / authHeaders) that used to live here. The MFA
// endpoints all have corresponding methods on the shared `api` client
// (lib/api.ts) which handle: auth header injection from AuthContext via
// the deferred token provider, automatic refresh-on-401, the fresh-login
// grace window, single-flight refresh dedup, and the unified
// session-expired redirect. Each public function below now delegates to
// the shared client and adapts the response shape; the `token` parameter
// is kept on the public signature for backward compatibility with the
// existing callers but is no longer consulted.

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

export async function startMfaSetup(_token?: string): Promise<MFASetupResponse> {
    const raw = await api.setupMfa();
    return {
        qrCode: raw.qr_code,
        manualEntryKey: extractSecretFromUri(raw.provisioning_uri),
        provisioningUri: raw.provisioning_uri,
        issuer: raw.issuer,
        account: raw.account,
    };
}

export async function confirmMfaSetup(_token: string | undefined, code: string): Promise<MFAConfirmResponse> {
    const raw = await api.confirmMfa(code.trim().replace(/\D/g, ""));
    return {
        enabled: true,
        recoveryCodes: raw.recovery_codes,
        recoveryCodesCount: raw.recovery_codes_count,
        message: raw.message,
    };
}

export async function getMfaStatus(_token?: string): Promise<MFAStatusResponse> {
    const raw = await api.getMfaStatus();
    return {
        enabled: raw.enabled,
        verifiedAt: raw.verified_at,
        recoveryCodesRemaining: raw.recovery_codes_remaining,
    };
}

export async function disableMfa(_token: string | undefined, password: string): Promise<{ success: boolean }> {
    await api.disableMfa(password);
    return { success: true };
}

export async function regenerateRecoveryCodes(
    _token: string | undefined,
    code: string,
): Promise<{ recoveryCodes: string[] }> {
    const raw = await api.regenerateRecoveryCodes(code.trim().replace(/\D/g, ""));
    return { recoveryCodes: raw.recovery_codes };
}
