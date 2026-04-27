"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Loader2, Key, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  isWebAuthnSupported,
  isPlatformAuthenticatorAvailable,
  startPasskeyRegistration,
  completePasskeyRegistration,
  createWebAuthnCredential,
  extractCredentialCreateData,
} from "@/lib/webauthn-utils";

interface PasskeyRegistrationProps {
  token: string;
  onSuccess: () => void;
  onError?: (error: string) => void;
  onCancel?: () => void;
}

export default function PasskeyRegistration({
  token,
  onSuccess,
  onError,
  onCancel,
}: PasskeyRegistrationProps) {
  const [credentialName, setCredentialName] = useState("");
  const [loading, setLoading] = useState(true);
  const [registering, setRegistering] = useState(false);
  const [error, setError] = useState("");
  const [supported, setSupported] = useState(false);
  const [platformAvailable, setPlatformAvailable] = useState(false);
  const [step, setStep] = useState<"intro" | "name" | "register">("intro");
  const nameInputRef = useRef<HTMLInputElement | null>(null);
  const errorId = useId();

  // Check WebAuthn support
  useEffect(() => {
    async function checkSupport() {
      try {
        setLoading(true);
        const supported = isWebAuthnSupported();
        const platformAvailable = supported && (await isPlatformAuthenticatorAvailable());

        setSupported(supported);
        setPlatformAvailable(platformAvailable);

        if (!supported) {
          setError("WebAuthn is not supported in your browser");
          onError?.("WebAuthn is not supported in your browser");
        }
      } catch {
        const errorMsg = "Failed to check WebAuthn support";
        setError(errorMsg);
        onError?.(errorMsg);
      } finally {
        setLoading(false);
      }
    }

    checkSupport();
  }, [onError]);

  // Auto-focus name input
  useEffect(() => {
    if (step === "name") {
      const t = window.setTimeout(() => {
        nameInputRef.current?.focus();
      }, 0);
      return () => window.clearTimeout(t);
    }
  }, [step]);

  async function handleRegisterPasskey(e?: React.FormEvent) {
    if (e) e.preventDefault();

    if (!credentialName.trim()) {
      setError("Please enter a name for this passkey");
      return;
    }

    setRegistering(true);
    setError("");

    try {
      // Start registration
      const startResponse = await startPasskeyRegistration(token);

      // Create credential
      const options = {
        challenge: startResponse.challenge as unknown as BufferSource,
        rp: startResponse.rp,
        user: {
          id: new TextEncoder().encode("user-id") as BufferSource,
          name: "user@example.com",
          displayName: "User",
        },
        pubKeyCredParams: startResponse.pubKeyCredParams || [
          { type: "public-key", alg: -7 },
          { type: "public-key", alg: -257 },
        ],
        authenticatorSelection: startResponse.authenticatorSelection || {
          authenticatorAttachment: platformAvailable ? "platform" : undefined,
          residentKey: "preferred",
          userVerification: "preferred",
        },
        timeout: 60000,
        attestation: "direct",
      } as unknown as PublicKeyCredentialCreationOptions;

      const credential = await createWebAuthnCredential(options);

      // Extract credential data
      const credentialData = extractCredentialCreateData(credential);

      // Complete registration
      const completeResponse = await completePasskeyRegistration(token, credentialName, credentialData);

      if (completeResponse.success) {
        onSuccess();
      } else {
        throw new Error(completeResponse.message || "Registration failed");
      }
    } catch (err) {
      let errorMsg = "Failed to register passkey";

      if (err instanceof Error) {
        if (err.name === "NotAllowedError") {
          errorMsg = "Passkey registration was cancelled";
        } else if (err.name === "NotSupportedError") {
          errorMsg = "Your device doesn't support passkeys";
        } else if (err.name === "InvalidStateError") {
          errorMsg = "This passkey has already been registered";
        } else {
          errorMsg = err.message;
        }
      }

      setError(errorMsg);
      onError?.(errorMsg);
    } finally {
      setRegistering(false);
    }
  }

  if (loading) {
    return (
      <Card>
        <CardHeader className="text-center">
          <CardTitle>Setting up Passkey</CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-hidden />
        </CardContent>
      </Card>
    );
  }

  if (!supported) {
    return (
      <Card>
        <CardHeader className="text-center">
          <CardTitle>Passkey Not Supported</CardTitle>
          <CardDescription>Your browser doesn&apos;t support passkeys</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded-md p-3">
            <p className="text-sm text-amber-700 dark:text-amber-300">
              Please use a modern browser that supports WebAuthn (Chrome, Safari, Edge, or Firefox).
            </p>
          </div>
        </CardContent>
        <CardFooter>
          <Button type="button" variant="outline" className="w-full" onClick={onCancel}>
            Cancel
          </Button>
        </CardFooter>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="text-center">
        <CardTitle>Add a Passkey</CardTitle>
        <CardDescription>Sign in faster and more securely without a password</CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        {step === "intro" && (
          <div className="space-y-4">
            <div className="bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 rounded-md p-4">
              <div className="flex gap-3">
                <Key className="h-5 w-5 text-blue-600 dark:text-blue-400 flex-shrink-0 mt-0.5" aria-hidden />
                <div className="space-y-1">
                  <p className="text-sm font-medium text-blue-900 dark:text-blue-200">What is a passkey?</p>
                  <p className="text-xs text-blue-700 dark:text-blue-300">
                    A passkey is a cryptographic key stored on your device. It&apos;s more secure than passwords and works with
                    fingerprints or face recognition.
                  </p>
                </div>
              </div>
            </div>

            {platformAvailable && (
              <div className="bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 rounded-md p-3">
                <p className="text-xs text-green-700 dark:text-green-300">
                  ✓ Your device supports platform passkeys (biometric/PIN)
                </p>
              </div>
            )}

            <ul className="space-y-2 text-sm text-muted-foreground">
              <li className="flex gap-2">
                <span className="text-green-600 dark:text-green-400">✓</span>
                <span>Faster login with biometric or PIN</span>
              </li>
              <li className="flex gap-2">
                <span className="text-green-600 dark:text-green-400">✓</span>
                <span>More secure than passwords</span>
              </li>
              <li className="flex gap-2">
                <span className="text-green-600 dark:text-green-400">✓</span>
                <span>Works across devices and platforms</span>
              </li>
            </ul>
          </div>
        )}

        {step === "name" && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleRegisterPasskey();
            }}
            className="space-y-4"
          >
            <div className="space-y-2">
              <Label htmlFor="passkey-name">Name this passkey (optional)</Label>
              <Input
                id="passkey-name"
                type="text"
                placeholder="e.g., MacBook Pro, iPhone"
                value={credentialName}
                onChange={(e) => setCredentialName(e.target.value)}
                disabled={registering}
                ref={nameInputRef}
              />
              <p className="text-xs text-muted-foreground">
                Give this passkey a memorable name if you plan to add multiple passkeys
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

            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                className="flex-1"
                onClick={onCancel}
                disabled={registering}
              >
                Cancel
              </Button>
              <Button type="submit" className="flex-1" disabled={registering || !credentialName.trim()}>
                {registering ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin mr-2" aria-hidden />
                    Creating...
                  </>
                ) : (
                  <>
                    Create Passkey
                    <ArrowRight className="h-4 w-4 ml-2" aria-hidden />
                  </>
                )}
              </Button>
            </div>
          </form>
        )}

        {error && step === "intro" && (
          <div
            id={errorId}
            role="alert"
            aria-live="assertive"
            className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 rounded-md p-3"
          >
            {error}
          </div>
        )}
      </CardContent>

      <CardFooter className="flex gap-2">
        {step === "intro" ? (
          <>
            <Button type="button" variant="outline" className="flex-1" onClick={onCancel}>
              Cancel
            </Button>
            <Button
              type="button"
              className="flex-1"
              onClick={() => setStep("name")}
            >
              Continue
              <ArrowRight className="h-4 w-4 ml-2" aria-hidden />
            </Button>
          </>
        ) : null}
      </CardFooter>
    </Card>
  );
}
