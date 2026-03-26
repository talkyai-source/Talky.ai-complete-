/**
 * WebAuthn / Passkey Utilities for Talk-Leee Frontend
 *
 * Official References (verified March 2026):
 *   W3C WebAuthn Level 3 Candidate Recommendation:
 *     https://www.w3.org/TR/webauthn-3/
 *   MDN Web Authentication API:
 *     https://developer.mozilla.org/en-US/docs/Web/API/Web_Authentication_API
 */

import { api } from "@/lib/api";

export interface PasskeyCredential {
  id: string;
  credential_id: string;
  display_name: string;
  device_type: "singleDevice" | "multiDevice";
  backed_up: boolean;
  transports: string[];
  created_at: string;
  last_used_at?: string;
}

// =============================================================================
// Browser Capability Detection
// =============================================================================

export function isWebAuthnSupported(): boolean {
  return typeof window !== "undefined" && window.PublicKeyCredential !== undefined;
}

export async function isConditionalUISupported(): Promise<boolean> {
  if (!isWebAuthnSupported()) return false;
  try {
    // @ts-ignore - isConditionalMediationAvailable is newer
    return await PublicKeyCredential.isConditionalMediationAvailable();
  } catch {
    return false;
  }
}

export async function isPlatformAuthenticatorAvailable(): Promise<boolean> {
  if (!isWebAuthnSupported()) return false;
  try {
    return await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
  } catch {
    return false;
  }
}

// =============================================================================
// Base64url Utilities
// =============================================================================

function bufferToBase64url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  const base64 = btoa(binary);
  return base64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

function base64urlToBuffer(base64url: string): ArrayBuffer {
  const padding = "=".repeat((4 - (base64url.length % 4)) % 4);
  const base64 = base64url.replace(/-/g, "+").replace(/_/g, "/") + padding;
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

// =============================================================================
// Passkey Login
// =============================================================================

export async function loginWithPasskey(email?: string) {
  const { ceremony_id, options } = await api.beginPasskeyLogin(email);

  const challenge = base64urlToBuffer(options.challenge as string);
  const allowCredentials = (options.allowCredentials as Array<{ id: string }> | undefined)?.map(
    (cred) => ({
      ...cred,
      id: base64urlToBuffer(cred.id),
    })
  );

  const credential = (await navigator.credentials.get({
    publicKey: {
      ...(options as PublicKeyCredentialRequestOptions),
      challenge,
      allowCredentials,
    },
  })) as PublicKeyCredential | null;

  if (!credential) {
    throw new Error("Passkey authentication was cancelled");
  }

  const response = credential.response as AuthenticatorAssertionResponse;
  const credentialResponse = {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bufferToBase64url(response.clientDataJSON),
      authenticatorData: bufferToBase64url(response.authenticatorData),
      signature: bufferToBase64url(response.signature),
      userHandle: response.userHandle ? bufferToBase64url(response.userHandle) : null,
    },
  };

  const result = await api.completePasskeyLogin(ceremony_id, credentialResponse);
  api.setToken(result.access_token);
  return result;
}

export async function checkUserHasPasskeys(email: string): Promise<boolean> {
  return api.checkUserHasPasskeys(email);
}

// =============================================================================
// Passkey Registration
// =============================================================================

export async function registerPasskey(
  authenticatorType: "platform" | "cross-platform" | "any" = "any",
  displayName?: string
) {
  const { ceremony_id, options } = await api.beginPasskeyRegistration(
    authenticatorType,
    displayName
  );

  const challenge = base64urlToBuffer(options.challenge as string);
  const userId = base64urlToBuffer(options.user.id as string);

  const credential = (await navigator.credentials.create({
    publicKey: {
      ...(options as PublicKeyCredentialCreationOptions),
      challenge,
      user: {
        ...(options.user as PublicKeyCredentialUserEntity),
        id: userId,
      },
    },
  })) as PublicKeyCredential | null;

  if (!credential) {
    throw new Error("Passkey creation was cancelled");
  }

  const response = credential.response as AuthenticatorAttestationResponse;
  const credentialResponse = {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bufferToBase64url(response.clientDataJSON),
      attestationObject: bufferToBase64url(response.attestationObject),
      ...(response.authenticatorData && {
        authenticatorData: bufferToBase64url(response.authenticatorData),
      }),
      ...(response.publicKey && {
        publicKey: bufferToBase64url(response.publicKey),
      }),
      ...(response.publicKeyAlgorithm && {
        publicKeyAlgorithm: response.publicKeyAlgorithm,
      }),
    },
    ...(response.getTransports && { transports: response.getTransports() }),
  };

  return api.completePasskeyRegistration(ceremony_id, credentialResponse, displayName);
}

// =============================================================================
// Passkey Management
// =============================================================================

export async function listPasskeys(): Promise<PasskeyCredential[]> {
  return api.listPasskeys();
}

export async function updatePasskey(passkeyId: string, displayName: string): Promise<void> {
  return api.updatePasskey(passkeyId, displayName);
}

export async function deletePasskey(passkeyId: string): Promise<void> {
  return api.deletePasskey(passkeyId);
}

// =============================================================================
// Error Handling
// =============================================================================

export class PasskeyError extends Error {
  constructor(
    message: string,
    public code: "NOT_SUPPORTED" | "CANCELLED" | "VERIFICATION_FAILED" | "NETWORK_ERROR" | "UNKNOWN"
  ) {
    super(message);
    this.name = "PasskeyError";
  }
}

export function handlePasskeyError(error: unknown): PasskeyError {
  if (error instanceof PasskeyError) return error;
  if (error instanceof Error) {
    if (error.message.includes("cancelled") || error.message.includes("abort")) {
      return new PasskeyError("User cancelled the operation", "CANCELLED");
    }
    if (error.message.includes("not supported") || error.message.includes("NotAllowedError")) {
      return new PasskeyError("Passkeys are not supported on this device", "NOT_SUPPORTED");
    }
    if (error.message.includes("verification failed") || error.message.includes("Invalid")) {
      return new PasskeyError("Verification failed", "VERIFICATION_FAILED");
    }
    return new PasskeyError(error.message, "UNKNOWN");
  }
  return new PasskeyError("Unknown error", "UNKNOWN");
}
