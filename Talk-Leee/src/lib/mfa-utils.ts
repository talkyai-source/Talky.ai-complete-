/**
 * MFA Utilities for TOTP-based Multi-Factor Authentication
 * Handles TOTP code generation, validation, and recovery code management
 */

export interface MFASetupResponse {
  qrCode: string;
  secret: string;
  recoveryCodes: string[];
  manualEntryKey: string;
}

export interface MFAVerifyResponse {
  success: boolean;
  message?: string;
}

export interface RecoveryCode {
  code: string;
  used: boolean;
  usedAt?: string;
}

/**
 * Validates a TOTP code format (6-8 digits)
 */
export function validateTotpCode(code: string): boolean {
  const cleanCode = code.trim().replace(/\D/g, "");
  return cleanCode.length >= 6 && cleanCode.length <= 8;
}

/**
 * Generates recovery codes (backup codes for MFA)
 * Format: XXXX-XXXX-XXXX-XXXX (16 alphanumeric characters per code)
 */
export function generateRecoveryCodes(count: number = 8): string[] {
  const codes: string[] = [];
  for (let i = 0; i < count; i++) {
    const code = generateRandomCode();
    codes.push(code);
  }
  return codes;
}

/**
 * Generates a single recovery code
 */
function generateRandomCode(): string {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
  let code = "";
  for (let i = 0; i < 16; i++) {
    if (i > 0 && i % 4 === 0) code += "-";
    code += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return code;
}

/**
 * Formats recovery codes for display
 */
export function formatRecoveryCodes(codes: string[]): string {
  return codes.join("\n");
}

/**
 * Downloads recovery codes as text file
 */
export function downloadRecoveryCodes(codes: string[], filename: string = "mfa-recovery-codes.txt"): void {
  const content = `MFA Recovery Codes\nGenerated: ${new Date().toISOString()}\n\n${formatRecoveryCodes(codes)}\n\nStore these codes in a safe place. Each code can only be used once.`;
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

/**
 * Copies text to clipboard
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/**
 * Gets time remaining for current TOTP code (in seconds)
 */
export function getTimeRemaining(): number {
  const timeStep = 30; // Standard TOTP time step is 30 seconds
  return timeStep - (Math.floor(Date.now() / 1000) % timeStep);
}

/**
 * API call to start MFA setup
 */
export async function startMfaSetup(token: string): Promise<MFASetupResponse> {
  const response = await fetch("/api/auth/mfa/setup/start", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
  });

  if (!response.ok) {
    throw new Error("Failed to start MFA setup");
  }

  return response.json();
}

/**
 * API call to verify and complete MFA setup
 */
export async function verifyMfaSetup(
  token: string,
  totpCode: string,
  secret: string
): Promise<MFAVerifyResponse> {
  const response = await fetch("/api/auth/mfa/setup/verify", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      totpCode: totpCode.trim().replace(/\D/g, ""),
      secret,
    }),
  });

  if (!response.ok) {
    throw new Error("Failed to verify MFA setup");
  }

  return response.json();
}

/**
 * API call to verify MFA code during login
 */
export async function verifyMfaLogin(email: string, totpCode: string): Promise<{
  access_token: string;
  refresh_token: string;
}> {
  const response = await fetch("/api/auth/mfa/verify", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      email,
      totpCode: totpCode.trim().replace(/\D/g, ""),
    }),
  });

  if (!response.ok) {
    throw new Error("Invalid MFA code");
  }

  return response.json();
}

/**
 * API call to disable MFA
 */
export async function disableMfa(token: string): Promise<{ success: boolean }> {
  const response = await fetch("/api/auth/mfa/disable", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
  });

  if (!response.ok) {
    throw new Error("Failed to disable MFA");
  }

  return response.json();
}

/**
 * API call to regenerate recovery codes
 */
export async function regenerateRecoveryCodes(token: string): Promise<{
  recoveryCodes: string[];
}> {
  const response = await fetch("/api/auth/mfa/recovery-codes/regenerate", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
  });

  if (!response.ok) {
    throw new Error("Failed to regenerate recovery codes");
  }

  return response.json();
}
