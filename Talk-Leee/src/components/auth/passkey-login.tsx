"use client";

import { useId, useState } from "react";
import { Loader2, Key } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  isWebAuthnSupported,
  startPasskeyAuth,
  completePasskeyAuth,
  getWebAuthnCredential,
  extractCredentialAssertionData,
} from "@/lib/webauthn-utils";

interface PasskeyLoginProps {
  onSuccess: (tokens: { access_token: string; refresh_token: string }) => void;
  onError?: (error: string) => void;
  disabled?: boolean;
}

export default function PasskeyLogin({ onSuccess, onError, disabled = false }: PasskeyLoginProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const errorId = useId();

  async function handlePasskeyLogin() {
    setLoading(true);
    setError("");

    try {
      if (!isWebAuthnSupported()) {
        throw new Error("Passkeys are not supported in your browser");
      }

      // Start authentication
      const startResponse = await startPasskeyAuth();

      // Get credential from user
      const assertion = await getWebAuthnCredential({
        challenge: startResponse.challenge as unknown as BufferSource,
        rp: startResponse.rp,
        timeout: 60000,
        userVerification: "preferred",
      } as PublicKeyCredentialRequestOptions);

      // Extract assertion data
      const assertionData = extractCredentialAssertionData(assertion);

      // Complete authentication
      const tokens = await completePasskeyAuth(assertionData);

      onSuccess(tokens);
    } catch (err) {
      let errorMsg = "Passkey login failed";

      if (err instanceof Error) {
        if (err.name === "NotAllowedError") {
          errorMsg = "Passkey authentication was cancelled";
        } else if (err.name === "NotSupportedError") {
          errorMsg = "Your device doesn't support passkeys";
        } else if (err.name === "InvalidStateError") {
          errorMsg = "No passkey registered for this account";
        } else {
          errorMsg = err.message;
        }
      }

      setError(errorMsg);
      onError?.(errorMsg);
    } finally {
      setLoading(false);
    }
  }

  if (!isWebAuthnSupported()) {
    return null;
  }

  return (
    <div className="space-y-3">
      <Button
        type="button"
        onClick={handlePasskeyLogin}
        disabled={loading || disabled}
        className="w-full"
        variant="outline"
      >
        {loading ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin mr-2" aria-hidden />
            Signing in...
          </>
        ) : (
          <>
            <Key className="h-4 w-4 mr-2" aria-hidden />
            Sign in with Passkey
          </>
        )}
      </Button>

      {error && (
        <div
          id={errorId}
          role="alert"
          aria-live="assertive"
          className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 rounded-md p-2"
        >
          {error}
        </div>
      )}
    </div>
  );
}
