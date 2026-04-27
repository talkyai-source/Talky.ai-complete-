/**
 * WebAuthn/FIDO2 Utilities for Passkey Management
 * Handles passkey registration and authentication flows
 */

export interface PasskeyCredential {
  id: string;
  name: string;
  createdAt: string;
  lastUsedAt?: string;
  transports?: string[];
}

export interface WebAuthnStartResponse {
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
  const response = await fetch("/api/auth/passkey/register/start", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
  });

  if (!response.ok) {
    throw new Error("Failed to start passkey registration");
  }

  return response.json();
}

/**
 * Completes passkey registration
 */
export async function completePasskeyRegistration(
  token: string,
  credentialName: string,
  credentialData: Record<string, unknown>
): Promise<WebAuthnCompleteResponse> {
  const response = await fetch("/api/auth/passkey/register/complete", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      credentialName,
      credential: credentialData,
    }),
  });

  if (!response.ok) {
    throw new Error("Failed to complete passkey registration");
  }

  return response.json();
}

/**
 * Starts passkey authentication process
 */
export async function startPasskeyAuth(): Promise<WebAuthnStartResponse> {
  const response = await fetch("/api/auth/passkey/authenticate/start", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    throw new Error("Failed to start passkey authentication");
  }

  return response.json();
}

/**
 * Completes passkey authentication
 */
export async function completePasskeyAuth(credentialData: Record<string, unknown>): Promise<{
  access_token: string;
  refresh_token: string;
}> {
  const response = await fetch("/api/auth/passkey/authenticate/complete", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      credential: credentialData,
    }),
  });

  if (!response.ok) {
    throw new Error("Failed to authenticate with passkey");
  }

  return response.json();
}

/**
 * Gets list of registered passkeys
 */
export async function getPasskeys(token: string): Promise<PasskeyCredential[]> {
  const response = await fetch("/api/auth/passkeys", {
    method: "GET",
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });

  if (!response.ok) {
    throw new Error("Failed to fetch passkeys");
  }

  return response.json();
}

/**
 * Deletes a passkey
 */
export async function deletePasskey(token: string, credentialId: string): Promise<{ success: boolean }> {
  const response = await fetch(`/api/auth/passkeys/${credentialId}`, {
    method: "DELETE",
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });

  if (!response.ok) {
    throw new Error("Failed to delete passkey");
  }

  return response.json();
}

/**
 * Renames a passkey
 */
export async function renamePasskey(
  token: string,
  credentialId: string,
  newName: string
): Promise<{ success: boolean }> {
  const response = await fetch(`/api/auth/passkeys/${credentialId}/rename`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ name: newName }),
  });

  if (!response.ok) {
    throw new Error("Failed to rename passkey");
  }

  return response.json();
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
