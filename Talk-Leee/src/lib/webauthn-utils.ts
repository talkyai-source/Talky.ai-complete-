/**
 * WebAuthn / Passkey utilities — talks directly to the backend's
 * /api/v1/auth/passkeys/* routes (the previous version called
 * /api/auth/passkey/* on the Next.js host, which doesn't exist and was
 * returning 404).
 *
 * Backend route map:
 *   POST  /api/v1/auth/passkeys/register/begin     (authenticated)
 *   POST  /api/v1/auth/passkeys/register/complete  (authenticated)
 *   POST  /api/v1/auth/passkeys/login/begin
 *   POST  /api/v1/auth/passkeys/login/complete
 *   GET   /api/v1/auth/passkeys                    (authenticated)
 *   PATCH /api/v1/auth/passkeys/{id}               (authenticated, rename)
 *   DELETE /api/v1/auth/passkeys/{id}              (authenticated)
 *
 * Backend register/login `begin` returns `{ ceremony_id, options[, has_passkeys] }`.
 * The frontend needs that `ceremony_id` to call `complete`, so we flatten
 * `options` onto the return value alongside `ceremony_id` so callers can
 * read `.challenge` / `.rp` / etc. directly.
 */

import { api } from "@/lib/api";

export interface PasskeyCredential {
  id: string;
  name: string;
  createdAt: string;
  lastUsedAt?: string;
  transports?: string[];
}

export interface WebAuthnStartResponse {
  ceremony_id: string;
  challenge: string;
  rp: {
    name: string;
    id: string;
  };
  user?: {
    id: string;
    name: string;
    displayName: string;
  };
  pubKeyCredParams?: Array<{
    type: string;
    alg: number;
  }>;
  authenticatorSelection?: {
    authenticatorAttachment?: string;
    residentKey?: string;
    userVerification?: string;
  };
  allowCredentials?: Array<{
    type: string;
    id: string;
    transports?: string[];
  }>;
  has_passkeys?: boolean;
}

export interface WebAuthnCompleteResponse {
  success: boolean;
  credentialId?: string;
  message?: string;
}

/**
 * Checks if WebAuthn is supported in the browser
 */
export function isWebAuthnSupported(): boolean {
  return (
    window.PublicKeyCredential !== undefined &&
    navigator.credentials !== undefined &&
    navigator.credentials.create !== undefined &&
    navigator.credentials.get !== undefined
  );
}

/**
 * Checks if platform authenticator (biometric/Windows Hello) is available
 */
export async function isPlatformAuthenticatorAvailable(): Promise<boolean> {
  if (!isWebAuthnSupported()) {
    return false;
  }
  try {
    return await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
  } catch {
    return false;
  }
}

/**
 * Converts a base64 OR base64url string to ArrayBuffer.
 *
 * WebAuthn (and py_webauthn specifically) emits challenges, credential
 * IDs, and user IDs as base64url (RFC 4648 §5) — `-` and `_` instead of
 * `+` and `/`, padding usually omitted. window.atob() only accepts
 * standard base64, so we normalise first.
 */
export function base64toArrayBuffer(base64: string): ArrayBuffer {
  // Defensive: surface the real type so future bad callers get a useful
  // message instead of the cryptic `base64.replace is not a function`.
  if (typeof base64 !== "string") {
    throw new TypeError(
      `base64toArrayBuffer expected a string, got ${
        base64 === null ? "null" :
        base64 === undefined ? "undefined" :
        (base64 as object).constructor?.name ?? typeof base64
      }`,
    );
  }
  // Normalise base64url -> standard base64 + restore padding
  const padded = base64.replace(/-/g, "+").replace(/_/g, "/");
  const pad = padded.length % 4 === 0 ? "" : "=".repeat(4 - (padded.length % 4));
  const binary_string = window.atob(padded + pad);
  const len = binary_string.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binary_string.charCodeAt(i);
  }
  return bytes.buffer;
}

/**
 * Converts ArrayBuffer to base64url string (the WebAuthn wire format).
 * Backend's verify_registration / verify_authentication expect base64url
 * for clientDataJSON / attestationObject / authenticatorData / signature.
 */
export function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  // btoa produces standard base64; convert to base64url (no padding).
  return window
    .btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

// Phase 4 universal-auth-state: each function below delegates to the
// shared `api` client (lib/api.ts). The shared client handles auth header
// injection from AuthContext, refresh-on-401, single-flight refresh
// dedup, fresh-login grace, and the unified session-expired redirect.
// The `token` parameter on the authenticated functions is kept on the
// public signature for backward compatibility with existing callers but
// is no longer consulted — the shared client owns the token source.

/**
 * Starts passkey registration process
 */
export async function startPasskeyRegistration(_token?: string): Promise<WebAuthnStartResponse> {
  const data = await api.beginPasskeyRegistration("any");
  // Flatten options onto the return so callers can read .challenge directly,
  // and keep ceremony_id so the caller can pass it to complete().
  return {
    ceremony_id: data.ceremony_id,
    ...(data.options as Omit<WebAuthnStartResponse, "ceremony_id">),
  };
}

/**
 * Completes passkey registration
 */
export async function completePasskeyRegistration(
  _token: string | undefined,
  ceremonyId: string,
  credentialName: string,
  credentialData: Record<string, unknown>
): Promise<WebAuthnCompleteResponse> {
  const data = await api.completePasskeyRegistration(ceremonyId, credentialData, credentialName);
  return {
    success: true,
    credentialId: data.passkey_id,
    message: data.message,
  };
}

/**
 * Starts passkey authentication process
 */
export async function startPasskeyAuth(email?: string): Promise<WebAuthnStartResponse> {
  const data = await api.beginPasskeyLogin(email);
  return {
    ceremony_id: data.ceremony_id,
    has_passkeys: data.has_passkeys,
    ...(data.options as Omit<WebAuthnStartResponse, "ceremony_id" | "has_passkeys">),
  };
}

/**
 * Completes passkey authentication
 */
export async function completePasskeyAuth(
  ceremonyId: string,
  credentialData: Record<string, unknown>
): Promise<{
  access_token: string;
  role?: string;
  user_id?: string;
  email?: string;
  business_name?: string | null;
  minutes_remaining?: number;
}> {
  // AH-Phase-G hygiene: refresh_token dropped from the return type.
  // The backend still emits the field in the body (Zod schema in
  // lib/api.ts keeps parsing it) but nothing on the frontend reads
  // it after Phase 7 — the HttpOnly talky_rt cookie is the canonical
  // refresh-token store. The vestigial type field was misleading.
  const data = await api.completePasskeyLogin(ceremonyId, credentialData);
  return data as unknown as {
    access_token: string;
    role?: string;
    user_id?: string;
    email?: string;
    business_name?: string | null;
    minutes_remaining?: number;
  };
}

/**
 * Gets list of registered passkeys
 */
export async function getPasskeys(_token?: string): Promise<PasskeyCredential[]> {
  const passkeys = await api.listPasskeys();
  return passkeys.map((p) => ({
    id: p.id,
    name: p.display_name ?? "Passkey",
    createdAt: p.created_at,
    lastUsedAt: p.last_used_at,
    transports: p.transports,
  }));
}

/**
 * Deletes a passkey
 */
export async function deletePasskey(_token: string | undefined, credentialId: string): Promise<{ success: boolean }> {
  await api.deletePasskey(credentialId);
  return { success: true };
}

/**
 * Renames a passkey
 */
export async function renamePasskey(
  _token: string | undefined,
  credentialId: string,
  newName: string
): Promise<{ success: boolean }> {
  await api.updatePasskey(credentialId, newName);
  return { success: true };
}

/**
 * Performs WebAuthn credential creation (registration)
 */
export async function createWebAuthnCredential(options: PublicKeyCredentialCreationOptions): Promise<PublicKeyCredential> {
  // Convert challenge and user ID from base64 to ArrayBuffer
  const modifiedOptions = {
    ...options,
    challenge: base64toArrayBuffer(options.challenge as unknown as string),
    user: {
      ...options.user!,
      id: base64toArrayBuffer((options.user?.id as unknown as string) || ""),
    },
  };

  const credential = await navigator.credentials.create({
    publicKey: modifiedOptions as PublicKeyCredentialCreationOptions,
  });

  if (!credential || !(credential instanceof PublicKeyCredential)) {
    throw new Error("Failed to create credential");
  }

  return credential;
}

/**
 * Performs WebAuthn credential assertion (authentication)
 */
export async function getWebAuthnCredential(options: PublicKeyCredentialRequestOptions): Promise<PublicKeyCredential> {
  // Convert challenge from base64 to ArrayBuffer
  const modifiedOptions = {
    ...options,
    challenge: base64toArrayBuffer(options.challenge as unknown as string),
  };

  const assertion = await navigator.credentials.get({
    publicKey: modifiedOptions as PublicKeyCredentialRequestOptions,
  });

  if (!assertion || !(assertion instanceof PublicKeyCredential)) {
    throw new Error("Failed to get credential");
  }

  return assertion;
}

/**
 * Extracts credential creation response data
 */
export function extractCredentialCreateData(credential: PublicKeyCredential): {
  id: string;
  rawId: string;
  type: string;
  response: {
    clientDataJSON: string;
    attestationObject: string;
  };
} {
  const response = credential.response as AuthenticatorAttestationResponse;
  return {
    id: credential.id,
    rawId: arrayBufferToBase64(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: arrayBufferToBase64(response.clientDataJSON),
      attestationObject: arrayBufferToBase64(response.attestationObject),
    },
  };
}

/**
 * Extracts credential assertion response data
 */
export function extractCredentialAssertionData(credential: PublicKeyCredential): {
  id: string;
  rawId: string;
  type: string;
  response: {
    clientDataJSON: string;
    authenticatorData: string;
    signature: string;
    userHandle: string | null;
  };
} {
  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    id: credential.id,
    rawId: arrayBufferToBase64(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: arrayBufferToBase64(response.clientDataJSON),
      authenticatorData: arrayBufferToBase64(response.authenticatorData),
      signature: arrayBufferToBase64(response.signature),
      userHandle: response.userHandle ? arrayBufferToBase64(response.userHandle) : null,
    },
  };
}
