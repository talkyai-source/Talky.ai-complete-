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

import { apiBaseUrl } from "@/lib/env";

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
 * Converts base64 string to ArrayBuffer
 */
export function base64toArrayBuffer(base64: string): ArrayBuffer {
  const binary_string = window.atob(base64);
  const len = binary_string.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binary_string.charCodeAt(i);
  }
  return bytes.buffer;
}

/**
 * Converts ArrayBuffer to base64 string
 */
export function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return window.btoa(binary);
}

/**
 * Starts passkey registration process
 */
export async function startPasskeyRegistration(token: string): Promise<WebAuthnStartResponse> {
  const response = await fetch(`${apiBaseUrl()}/auth/passkeys/register/begin`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ authenticator_type: "any" }),
  });

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail || "Failed to start passkey registration");
  }

  const data = await response.json();
  // Flatten options onto the return so callers can read .challenge directly,
  // and keep ceremony_id so the caller can pass it to complete().
  return { ceremony_id: data.ceremony_id, ...data.options };
}

/**
 * Completes passkey registration
 */
export async function completePasskeyRegistration(
  token: string,
  ceremonyId: string,
  credentialName: string,
  credentialData: Record<string, unknown>
): Promise<WebAuthnCompleteResponse> {
  const response = await fetch(`${apiBaseUrl()}/auth/passkeys/register/complete`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      ceremony_id: ceremonyId,
      credential_response: credentialData,
      display_name: credentialName,
    }),
  });

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail || "Failed to complete passkey registration");
  }

  const data = await response.json();
  return {
    success: true,
    credentialId: data.credential_id ?? data.passkey_id,
    message: data.message,
  };
}

/**
 * Starts passkey authentication process
 */
export async function startPasskeyAuth(email?: string): Promise<WebAuthnStartResponse> {
  const response = await fetch(`${apiBaseUrl()}/auth/passkeys/login/begin`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(email ? { email } : {}),
  });

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail || "Failed to start passkey authentication");
  }

  const data = await response.json();
  return {
    ceremony_id: data.ceremony_id,
    has_passkeys: data.has_passkeys,
    ...data.options,
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
  refresh_token: string;
  role?: string;
  user_id?: string;
  email?: string;
  business_name?: string | null;
  minutes_remaining?: number;
}> {
  const response = await fetch(`${apiBaseUrl()}/auth/passkeys/login/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ceremony_id: ceremonyId,
      credential_response: credentialData,
    }),
  });

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail || "Failed to authenticate with passkey");
  }

  return response.json();
}

/**
 * Gets list of registered passkeys
 */
export async function getPasskeys(token: string): Promise<PasskeyCredential[]> {
  const response = await fetch(`${apiBaseUrl()}/auth/passkeys`, {
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
  });

  if (!response.ok) {
    throw new Error("Failed to fetch passkeys");
  }

  const data = await response.json();
  // Backend returns {passkeys: [...], count: N}; surface just the list with
  // the fields the existing UI expects.
  return (data.passkeys ?? []).map((p: {
    id: string;
    display_name?: string;
    created_at: string;
    last_used_at?: string;
    transports?: string[];
  }) => ({
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
export async function deletePasskey(token: string, credentialId: string): Promise<{ success: boolean }> {
  const response = await fetch(`${apiBaseUrl()}/auth/passkeys/${credentialId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });

  if (!response.ok) {
    throw new Error("Failed to delete passkey");
  }

  return { success: true };
}

/**
 * Renames a passkey
 */
export async function renamePasskey(
  token: string,
  credentialId: string,
  newName: string
): Promise<{ success: boolean }> {
  // Backend uses PATCH (not PUT) and accepts {display_name} (not {name}).
  const response = await fetch(`${apiBaseUrl()}/auth/passkeys/${credentialId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ display_name: newName }),
  });

  if (!response.ok) {
    throw new Error("Failed to rename passkey");
  }

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
