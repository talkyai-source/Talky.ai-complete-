/**
 * WebAuthn / Passkey Utilities for Frontend
 *
 * Official References (verified March 2026):
 *   W3C WebAuthn Level 3 Candidate Recommendation:
 *     https://www.w3.org/TR/webauthn-3/
 *   MDN Web Authentication API:
 *     https://developer.mozilla.org/en-US/docs/Web/API/Web_Authentication_API
 *
 * This module provides:
 *   1. Browser capability detection
 *   2. Base64url encoding/decoding utilities
 *   3. Passkey registration flow
 *   4. Passkey authentication flow
 *   5. API integration with backend endpoints
 */

import { API_BASE_URL } from './api';

// =============================================================================
// Types
// =============================================================================

export interface PasskeyCredential {
  id: string;
  credential_id: string;
  display_name: string;
  device_type: 'singleDevice' | 'multiDevice';
  backed_up: boolean;
  transports: string[];
  created_at: string;
  last_used_at?: string;
}

export interface RegistrationOptions {
  ceremony_id: string;
  options: PublicKeyCredentialCreationOptionsJSON;
}

export interface AuthenticationOptions {
  ceremony_id: string;
  options: PublicKeyCredentialRequestOptionsJSON;
  has_passkeys: boolean;
}

export interface AuthResponse {
  access_token: string;
  user_id: string;
  email: string;
  role: string;
  business_name?: string;
  minutes_remaining: number;
  message: string;
}

// =============================================================================
// Browser Capability Detection
// =============================================================================

/**
 * Check if the browser supports WebAuthn / Passkeys
 */
export function isWebAuthnSupported(): boolean {
  return typeof window !== 'undefined' &&
         window.PublicKeyCredential !== undefined;
}

/**
 * Check if the browser supports conditional UI (autofill passkeys)
 * This allows passkeys to appear in password autofill suggestions
 */
export async function isConditionalUISupported(): Promise<boolean> {
  if (!isWebAuthnSupported()) return false;

  try {
    // @ts-ignore - isConditionalMediationAvailable is newer
    return await PublicKeyCredential.isConditionalMediationAvailable();
  } catch {
    return false;
  }
}

/**
 * Check if the browser supports platform authenticator (TouchID, Windows Hello)
 */
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

/**
 * Convert ArrayBuffer to base64url string
 */
function bufferToBase64url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  const base64 = btoa(binary);
  return base64
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '');
}

/**
 * Convert base64url string to ArrayBuffer
 */
function base64urlToBuffer(base64url: string): ArrayBuffer {
  // Add padding if needed
  const padding = '='.repeat((4 - (base64url.length % 4)) % 4);
  const base64 = base64url
    .replace(/-/g, '+')
    .replace(/_/g, '/')
    + padding;

  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

/**
 * Decode base64url string to regular string
 */
function base64urlToString(base64url: string): string {
  const buffer = base64urlToBuffer(base64url);
  const bytes = new Uint8Array(buffer);
  let result = '';
  for (let i = 0; i < bytes.length; i++) {
    result += String.fromCharCode(bytes[i]);
  }
  return result;
}

// =============================================================================
// API Client Functions
// =============================================================================

async function apiRequest<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`;

  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    credentials: 'include', // Include cookies for session
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

// =============================================================================
// Passkey Registration
// =============================================================================

/**
 * Start passkey registration
 * @param authenticatorType 'platform' | 'cross-platform' | 'any'
 * @param displayName User-friendly name for this passkey
 */
export async function beginPasskeyRegistration(
  authenticatorType: 'platform' | 'cross-platform' | 'any' = 'any',
  displayName?: string
): Promise<RegistrationOptions> {
  const token = localStorage.getItem('access_token');

  return apiRequest<RegistrationOptions>('/auth/passkeys/register/begin', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
    },
    body: JSON.stringify({
      authenticator_type: authenticatorType,
      display_name: displayName,
    }),
  });
}

/**
 * Complete passkey registration after user interacts with authenticator
 */
export async function completePasskeyRegistration(
  ceremonyId: string,
  credential: PublicKeyCredential,
  displayName?: string
): Promise<{ passkey_id: string; message: string }> {
  const token = localStorage.getItem('access_token');

  // Convert the credential to JSON-serializable format
  const response = credential.response as AuthenticatorAttestationResponse;

  const credentialResponse = {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bufferToBase64url(response.clientDataJSON),
      attestationObject: bufferToBase64url(response.attestationObject),
      // Optional fields that may be present
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
    // Include transports if available
    ...(response.getTransports && {
      transports: response.getTransports(),
    }),
  };

  return apiRequest('/auth/passkeys/register/complete', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
    },
    body: JSON.stringify({
      ceremony_id: ceremonyId,
      credential_response: credentialResponse,
      display_name: displayName,
    }),
  });
}

/**
 * Full registration flow helper
 */
export async function registerPasskey(
  authenticatorType: 'platform' | 'cross-platform' | 'any' = 'any',
  displayName?: string
): Promise<{ passkey_id: string; message: string }> {
  // Step 1: Get registration options from server
  const { ceremony_id, options } = await beginPasskeyRegistration(
    authenticatorType,
    displayName
  );

  // Step 2: Convert base64url challenge to ArrayBuffer
  const challenge = base64urlToBuffer(options.challenge as unknown as string);

  // Step 3: Convert user.id from base64url to ArrayBuffer
  const userId = base64urlToBuffer(options.user.id as unknown as string);

  // Step 4: Call WebAuthn API
  const credentialCreationOptions: PublicKeyCredentialCreationOptions = {
    ...options,
    challenge,
    user: {
      ...options.user,
      id: userId,
    },
    // Convert excludeCredentials if present
    excludeCredentials: options.excludeCredentials?.map(cred => ({
      ...cred,
      id: base64urlToBuffer(cred.id as unknown as string),
    })),
  };

  const credential = await navigator.credentials.create({
    publicKey: credentialCreationOptions,
  }) as PublicKeyCredential;

  if (!credential) {
    throw new Error('Passkey creation was cancelled or failed');
  }

  // Step 5: Complete registration with server
  return completePasskeyRegistration(ceremony_id, credential, displayName);
}

// =============================================================================
// Passkey Authentication (Login)
// =============================================================================

/**
 * Check if user has passkeys (for login UI)
 */
export async function checkUserHasPasskeys(email: string): Promise<boolean> {
  try {
    const result = await apiRequest<{ has_passkeys: boolean }>('/auth/passkey-check', {
      method: 'POST',
      body: JSON.stringify({ email }),
    });
    return result.has_passkeys;
  } catch {
    return false;
  }
}

/**
 * Start passkey authentication (login)
 * @param email Optional - if provided, restricts to user's credentials
 */
export async function beginPasskeyLogin(
  email?: string
): Promise<AuthenticationOptions> {
  return apiRequest<AuthenticationOptions>('/auth/passkeys/login/begin', {
    method: 'POST',
    body: JSON.stringify({ email }),
  });
}

/**
 * Complete passkey authentication
 */
export async function completePasskeyLogin(
  ceremonyId: string,
  credential: PublicKeyCredential
): Promise<AuthResponse> {
  const response = credential.response as AuthenticatorAssertionResponse;

  const credentialResponse = {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bufferToBase64url(response.clientDataJSON),
      authenticatorData: bufferToBase64url(response.authenticatorData),
      signature: bufferToBase64url(response.signature),
      userHandle: response.userHandle
        ? bufferToBase64url(response.userHandle)
        : null,
    },
  };

  const result = await apiRequest<AuthResponse>('/auth/passkeys/login/complete', {
    method: 'POST',
    body: JSON.stringify({
      ceremony_id: ceremonyId,
      credential_response: credentialResponse,
    }),
  });

  // Store the token
  localStorage.setItem('access_token', result.access_token);

  return result;
}

/**
 * Full login flow helper
 * @param email Optional - enables non-discoverable credential flow
 */
export async function loginWithPasskey(email?: string): Promise<AuthResponse> {
  // Step 1: Get authentication options from server
  const { ceremony_id, options } = await beginPasskeyLogin(email);

  // Step 2: Convert base64url challenge to ArrayBuffer
  const challenge = base64urlToBuffer(options.challenge as unknown as string);

  // Step 3: Convert allowCredentials if present
  const allowCredentials = options.allowCredentials?.map(cred => ({
    ...cred,
    id: base64urlToBuffer(cred.id as unknown as string),
  }));

  const credentialRequestOptions: PublicKeyCredentialRequestOptions = {
    ...options,
    challenge,
    allowCredentials,
  };

  // Step 4: Call WebAuthn API
  const credential = await navigator.credentials.get({
    publicKey: credentialRequestOptions,
  }) as PublicKeyCredential;

  if (!credential) {
    throw new Error('Passkey authentication was cancelled');
  }

  // Step 5: Complete authentication with server
  return completePasskeyLogin(ceremony_id, credential);
}

/**
 * Login with conditional mediation (autofill/autocomplete)
 * This allows passkeys to appear in the username/password autofill dropdown
 */
export async function loginWithConditionalMediation(): Promise<AuthResponse | null> {
  if (!await isConditionalUISupported()) {
    return null;
  }

  try {
    const { ceremony_id, options } = await beginPasskeyLogin();

    const challenge = base64urlToBuffer(options.challenge as unknown as string);
    const allowCredentials = options.allowCredentials?.map(cred => ({
      ...cred,
      id: base64urlToBuffer(cred.id as unknown as string),
    }));

    const credential = await navigator.credentials.get({
      publicKey: {
        ...options,
        challenge,
        allowCredentials,
      },
      // @ts-ignore - mediation is part of the spec but TypeScript types lag
      mediation: 'conditional',
    }) as PublicKeyCredential;

    if (!credential) {
      return null;
    }

    return completePasskeyLogin(ceremony_id, credential);
  } catch (error) {
    // Conditional mediation may fail silently - this is normal
    console.log('Conditional mediation not available or failed:', error);
    return null;
  }
}

// =============================================================================
// Passkey Management
// =============================================================================

/**
 * List user's passkeys
 */
export async function listPasskeys(): Promise<PasskeyCredential[]> {
  const token = localStorage.getItem('access_token');

  const result = await apiRequest<{ passkeys: PasskeyCredential[] }>('/auth/passkeys', {
    headers: {
      'Authorization': `Bearer ${token}`,
    },
  });

  return result.passkeys;
}

/**
 * Update passkey display name
 */
export async function updatePasskey(
  passkeyId: string,
  displayName: string
): Promise<void> {
  const token = localStorage.getItem('access_token');

  await apiRequest(`/auth/passkeys/${passkeyId}`, {
    method: 'PATCH',
    headers: {
      'Authorization': `Bearer ${token}`,
    },
    body: JSON.stringify({ display_name: displayName }),
  });
}

/**
 * Delete a passkey
 */
export async function deletePasskey(passkeyId: string): Promise<void> {
  const token = localStorage.getItem('access_token');

  await apiRequest(`/auth/passkeys/${passkeyId}`, {
    method: 'DELETE',
    headers: {
      'Authorization': `Bearer ${token}`,
    },
  });
}

// =============================================================================
// Error Handling
// =============================================================================

export class PasskeyError extends Error {
  constructor(
    message: string,
    public code: 'NOT_SUPPORTED' | 'CANCELLED' | 'VERIFICATION_FAILED' | 'NETWORK_ERROR' | 'UNKNOWN'
  ) {
    super(message);
    this.name = 'PasskeyError';
  }
}

export function handlePasskeyError(error: unknown): PasskeyError {
  if (error instanceof PasskeyError) {
    return error;
  }

  if (error instanceof Error) {
    // Check for specific error messages
    if (error.message.includes('cancelled') || error.message.includes('abort')) {
      return new PasskeyError('User cancelled the operation', 'CANCELLED');
    }
    if (error.message.includes('not supported') || error.message.includes('NotAllowedError')) {
      return new PasskeyError('Passkeys are not supported on this device', 'NOT_SUPPORTED');
    }
    if (error.message.includes('verification failed') || error.message.includes('Invalid')) {
      return new PasskeyError('Verification failed', 'VERIFICATION_FAILED');
    }
    if (error.message.includes('network') || error.message.includes('fetch')) {
      return new PasskeyError('Network error', 'NETWORK_ERROR');
    }
    return new PasskeyError(error.message, 'UNKNOWN');
  }

  return new PasskeyError('Unknown error', 'UNKNOWN');
}
