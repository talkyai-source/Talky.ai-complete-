"use client";

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { useIsPresent } from "framer-motion";
import { ArrowRight, Loader2, KeyRound, ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, type LoginResponse } from "@/lib/api";
import { validateTotpCode } from "@/lib/mfa-utils";
import { ApiClientError, getRequestIdentityGeneration, subscribeRequestIdentityGeneration } from "@/lib/http-client";

interface MFAVerificationProps {
  // Issued by POST /auth/login when mfa_required=true. The verify call
  // /auth/mfa/verify needs this exact token — email is no longer enough.
  challengeToken: string | null;
  onSuccess: (response: LoginResponse) => void;
  onBackClick: () => void;
  onError?: (error: string) => void;
}

export default function MFAVerification({ challengeToken, onSuccess, onBackClick, onError }: MFAVerificationProps) {
  const [totpCode, setTotpCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [timeRemaining, setTimeRemaining] = useState(30);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const errorId = useId();
  const isPresent = useIsPresent();
  const mounted = useRef(false);
  const available = useRef(false);
  const currentChallenge = useRef(challengeToken);
  const currentCode = useRef(totpCode);
  const callbacks = useRef({ onSuccess, onBackClick, onError });
  const active = useRef<{ controller: AbortController; identity: number; challenge: string } | null>(null);
  const restart = useRef(false);
  const completed = useRef(false);
  const [restartRequired, setRestartRequired] = useState(false);
  const [finished, setFinished] = useState(false);

  const retire = useCallback(() => {
    const previous = active.current;
    active.current = null;
    previous?.controller.abort();
  }, []);

  useLayoutEffect(() => { callbacks.current = { onSuccess, onBackClick, onError }; }, [onSuccess, onBackClick, onError]);
  useLayoutEffect(() => { currentCode.current = totpCode; }, [totpCode]);
  useLayoutEffect(() => {
    mounted.current = true;
    const unsubscribe = subscribeRequestIdentityGeneration(() => {
      retire();
      setLoading(false);
      // Our accepted callback intentionally replaces identity. Its completed
      // challenge is already unavailable; do not turn success into an error.
      if (completed.current) return;
      restart.current = true;
      setRestartRequired(true);
      setTotpCode("");
      setError("Your sign-in changed. Return to login to start again.");
    });
    return () => { mounted.current = false; retire(); unsubscribe(); };
  }, [retire]);
  useLayoutEffect(() => {
    available.current = isPresent;
    if (currentChallenge.current !== challengeToken) {
      retire();
      currentChallenge.current = challengeToken;
      restart.current = false;
      completed.current = false;
      setRestartRequired(false);
      setFinished(false);
      setTotpCode("");
      setError("");
      setLoading(false);
    }
    if (!isPresent) {
      retire();
      // Keep React's loading display aligned with the canceled external
      // ceremony, including an exit animation that is later reversed.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLoading(false);
    }
  }, [challengeToken, isPresent, retire]);

  // Auto-focus input
  useEffect(() => {
    const t = window.setTimeout(() => {
      inputRef.current?.focus();
    }, 0);
    return () => window.clearTimeout(t);
  }, []);

  // Update time remaining for TOTP
  useEffect(() => {
    const interval = setInterval(() => {
      const timeStep = 30;
      const remaining = timeStep - (Math.floor(Date.now() / 1000) % timeStep);
      setTimeRemaining(remaining);
    }, 1000);

    return () => clearInterval(interval);
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!mounted.current || !available.current || active.current || restart.current || completed.current
        || challengeToken !== currentChallenge.current || totpCode !== currentCode.current) return;
    setError("");

    if (!challengeToken) {
      // Defensive: if the login response didn't surface a challenge_token
      // we'd reach this screen with nothing to verify against. Bounce the
      // user back to the credential step rather than silently failing.
      const errorMsg = "MFA session expired. Please sign in again.";
      setError(errorMsg);
      onError?.(errorMsg);
      return;
    }

    if (!validateTotpCode(totpCode)) {
      const errorMsg = "Please enter a valid 6-8 digit code";
      setError(errorMsg);
      onError?.(errorMsg);
      return;
    }

    setLoading(true);
    const operation = { controller: new AbortController(), identity: getRequestIdentityGeneration(), challenge: challengeToken };
    active.current = operation;
    const ownsOperation = () => mounted.current && available.current && active.current === operation
      && !operation.controller.signal.aborted && getRequestIdentityGeneration() === operation.identity
      && currentChallenge.current === operation.challenge;

    try {
      const response = await api.verifyMfaChallenge(challengeToken, totpCode, undefined, {
        signal: operation.controller.signal, ownsOperation,
      });
      if (!ownsOperation()) return;
      if (!response.access_token.trim() || response.mfa_required) {
        throw new Error("Verification response did not confirm sign-in.");
      }
      active.current = null;
      completed.current = true;
      setFinished(true);
      setLoading(false);
      callbacks.current.onSuccess(response);
    } catch (err) {
      if (!ownsOperation()) return;
      // Bad-code/expired challenges intentionally share a generic 401. A
      // confirmed refusal can be retried; an admitted timeout, 5xx or malformed
      // success may have consumed this one-use challenge and must not replay.
      const refused = err instanceof ApiClientError && typeof err.status === "number"
        && err.status >= 400 && err.status < 500 && err.status !== 408;
      const notSent = err instanceof ApiClientError && err.sessionRequestNotSent === true;
      const unknown = !refused && !notSent;
      if (unknown) {
        restart.current = true;
        setRestartRequired(true);
        setTotpCode("");
      }
      const errorMsg = unknown
        ? "We could not confirm whether verification completed. This code request will not be sent again. Return to login to start a new sign-in."
        : err instanceof Error ? err.message : "MFA verification was refused. Check your code or return to login.";
      setError(errorMsg);
      callbacks.current.onError?.(errorMsg);
    } finally {
      if (ownsOperation()) { active.current = null; setLoading(false); }
    }
  }

  function handleBack() {
    if (!mounted.current || !available.current || active.current || completed.current) return;
    available.current = false;
    retire();
    callbacks.current.onBackClick();
  }

  return (
    <Card>
      <CardHeader className="text-center">
        <CardTitle asChild>
          <h2>Two-Factor Authentication</h2>
        </CardTitle>
        <CardDescription>Enter the code from your authenticator app</CardDescription>
      </CardHeader>

      <form onSubmit={handleSubmit} aria-busy={loading} aria-describedby={error ? errorId : undefined}>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="totp">Authentication Code</Label>
              <span className="text-xs text-muted-foreground">Expires in {timeRemaining}s</span>
            </div>
            <div className="relative">
              <KeyRound className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" aria-hidden />
              <Input
                id="totp"
                type="text"
                placeholder="000000"
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, "").slice(0, 8))}
                className="pl-10 text-center text-lg tracking-widest font-mono"
                maxLength={8}
                inputMode="numeric"
                pattern="[0-9]*"
                autoComplete="one-time-code"
                required
                disabled={loading || restartRequired || finished || !isPresent}
                ref={inputRef}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? errorId : undefined}
              />
            </div>
            <p className="text-xs text-muted-foreground text-center">
              Open your authenticator app and enter the 6-digit code
            </p>
          </div>

          {error && (
            <div
              id={errorId}
              role="alert"
              aria-live="assertive"
              className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 rounded-md p-3"
            >
              {error}
            </div>
          )}

          <div className="bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 rounded-md p-3">
            <p className="text-xs text-blue-700 dark:text-blue-300">
              Don&apos;t have access to your authenticator? Use a recovery code instead.
            </p>
          </div>
        </CardContent>

        <CardFooter className="flex flex-col gap-3">
          <Button type="submit" className="w-full" disabled={loading || restartRequired || finished || !isPresent || totpCode.length < 6}>
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin mr-2" aria-hidden />
                Verifying...
              </>
            ) : (
              <>
                Verify
                <ArrowRight className="h-4 w-4 ml-2" aria-hidden />
              </>
            )}
          </Button>

          <button
            type="button"
            onClick={handleBack}
            className="text-sm text-muted-foreground hover:text-foreground flex items-center justify-center gap-1 w-full py-2"
            disabled={loading || finished || !isPresent}
          >
            <ArrowLeft className="h-3 w-3" aria-hidden />
            Back to login
          </button>
        </CardFooter>
      </form>
    </Card>
  );
}
