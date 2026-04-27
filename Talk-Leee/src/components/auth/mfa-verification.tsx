"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ArrowRight, Loader2, KeyRound, ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { validateTotpCode, verifyMfaLogin } from "@/lib/mfa-utils";

interface MFAVerificationProps {
  email: string;
  onSuccess: (tokens: { access_token: string; refresh_token: string }) => void;
  onBackClick: () => void;
  onError?: (error: string) => void;
}

export default function MFAVerification({ email, onSuccess, onBackClick, onError }: MFAVerificationProps) {
  const [totpCode, setTotpCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [timeRemaining, setTimeRemaining] = useState(30);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const errorId = useId();

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
    setError("");

    if (!validateTotpCode(totpCode)) {
      const errorMsg = "Please enter a valid 6-8 digit code";
      setError(errorMsg);
      onError?.(errorMsg);
      return;
    }

    setLoading(true);

    try {
      const response = await verifyMfaLogin(email, totpCode);
      onSuccess(response);
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "MFA verification failed. Please try again.";
      setError(errorMsg);
      onError?.(errorMsg);
    } finally {
      setLoading(false);
    }
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
                disabled={loading}
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
          <Button type="submit" className="w-full" disabled={loading || totpCode.length < 6}>
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
            onClick={onBackClick}
            className="text-sm text-muted-foreground hover:text-foreground flex items-center justify-center gap-1 w-full py-2"
            disabled={loading}
          >
            <ArrowLeft className="h-3 w-3" aria-hidden />
            Back to login
          </button>
        </CardFooter>
      </form>
    </Card>
  );
}
