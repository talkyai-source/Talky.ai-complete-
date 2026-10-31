"use client";

import { useCallback, useId, useLayoutEffect, useRef, useState } from "react";
import { useIsPresent } from "framer-motion";
import { Loader2, Key } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getRequestIdentityGeneration, subscribeRequestIdentityGeneration } from "@/lib/http-client";
import {
  isWebAuthnSupported,
  startPasskeyAuth,
  completePasskeyAuth,
  getWebAuthnCredential,
  extractCredentialAssertionData,
} from "@/lib/webauthn-utils";

interface PasskeyLoginProps {
  // AH-Phase-G: refresh_token dropped from the callback type — Phase 7
  // ended all frontend reads of it. The HttpOnly talky_rt cookie is
  // the canonical refresh store. The shape stays loose with
  // additional optional fields because completePasskeyAuth returns a
  // full LoginResponse.
  onSuccess: (tokens: { access_token: string }) => void;
  onError?: (error: string) => void;
  disabled?: boolean;
}

export default function PasskeyLogin({ onSuccess, onError, disabled = false }: PasskeyLoginProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const errorId = useId();
  const isPresent = useIsPresent();
  const mounted = useRef(false);
  const enabled = useRef(false);
  const callbacks = useRef({ onSuccess, onError });
  const active = useRef<{ controller: AbortController; identity: number } | null>(null);

  const retire = useCallback(() => {
    const previous = active.current;
    active.current = null;
    previous?.controller.abort();
  }, []);

  useLayoutEffect(() => {
    callbacks.current = { onSuccess, onError };
  }, [onSuccess, onError]);

  useLayoutEffect(() => {
    mounted.current = true;
    const unsubscribe = subscribeRequestIdentityGeneration(() => {
      retire();
      setLoading(false);
      setError("");
    });
    return () => {
      mounted.current = false;
      retire();
      unsubscribe();
    };
  }, [retire]);

  useLayoutEffect(() => {
    enabled.current = !disabled && isPresent;
    if (!enabled.current) {
      retire();
      // Back starts AnimatePresence's exit before unmount. Retire the prompt
      // at that committed transition, including its visible loading state.
      setLoading(false);
    }
  }, [disabled, isPresent, retire]);

  async function handlePasskeyLogin() {
    // A ref closes the double-activation window before React commits disabled.
    // Retained callbacks also consult the current committed availability.
    if (!mounted.current || !enabled.current || active.current) return;
    const operation = { controller: new AbortController(), identity: getRequestIdentityGeneration() };
    active.current = operation;
    const ownsOperation = () => mounted.current && enabled.current && active.current === operation
      && !operation.controller.signal.aborted && getRequestIdentityGeneration() === operation.identity;
    setLoading(true);
    setError("");

    try {
      if (!isWebAuthnSupported()) {
        throw new Error("Passkeys are not supported in your browser");
      }

      // Start authentication
      const startResponse = await startPasskeyAuth();
      if (!ownsOperation()) return;

      // Get credential from user
      const assertion = await getWebAuthnCredential(startResponse.options, operation.controller.signal);
      if (!ownsOperation()) return;

      // Extract assertion data
      const assertionData = extractCredentialAssertionData(assertion);

      // Complete authentication
      const tokens = await completePasskeyAuth(startResponse.ceremony_id, assertionData, {
        signal: operation.controller.signal,
        ownsOperation,
      });
      if (!ownsOperation()) return;

      callbacks.current.onSuccess(tokens);
    } catch (err) {
      if (!ownsOperation()) return;
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
      callbacks.current.onError?.(errorMsg);
    } finally {
      if (ownsOperation()) {
        active.current = null;
        setLoading(false);
      }
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
        disabled={loading || disabled || !isPresent}
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
