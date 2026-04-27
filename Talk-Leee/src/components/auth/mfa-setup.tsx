"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Loader2, Copy, Download, ArrowRight, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  startMfaSetup,
  verifyMfaSetup,
  validateTotpCode,
  downloadRecoveryCodes,
  copyToClipboard,
} from "@/lib/mfa-utils";

type Step = "qr" | "verify" | "recovery";

interface MFASetupProps {
  token: string;
  onSuccess: () => void;
  onError?: (error: string) => void;
  onCancel?: () => void;
}

export default function MFASetup({ token, onSuccess, onError, onCancel }: MFASetupProps) {
  const [step, setStep] = useState<Step>("qr");
  const [qrCode, setQrCode] = useState("");
  const [secret, setSecret] = useState("");
  const [manualKey, setManualKey] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [totpCode, setTotpCode] = useState("");
  const [loading, setLoading] = useState(true);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [downloadedCodes, setDownloadedCodes] = useState(false);
  const codeInputRef = useRef<HTMLInputElement | null>(null);
  const errorId = useId();

  // Fetch QR code and setup details
  useEffect(() => {
    async function fetchSetupDetails() {
      try {
        setLoading(true);
        const response = await startMfaSetup(token);
        setQrCode(response.qrCode);
        setSecret(response.secret);
        setManualKey(response.manualEntryKey);
        setError("");
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : "Failed to start MFA setup";
        setError(errorMsg);
        onError?.(errorMsg);
      } finally {
        setLoading(false);
      }
    }

    fetchSetupDetails();
  }, [token, onError]);

  // Auto-focus code input
  useEffect(() => {
    if (step === "verify") {
      const t = window.setTimeout(() => {
        codeInputRef.current?.focus();
      }, 0);
      return () => window.clearTimeout(t);
    }
  }, [step]);

  async function handleVerifyCode(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (!validateTotpCode(totpCode)) {
      const errorMsg = "Please enter a valid 6-8 digit code";
      setError(errorMsg);
      return;
    }

    setVerifying(true);

    try {
      const result = await verifyMfaSetup(token, totpCode, secret);
      if (result.success) {
        // In a real implementation, recovery codes would come from the backend
        // For now, we'll generate them client-side as a placeholder
        setRecoveryCodes([
          "XXXX-XXXX-XXXX-XXXX",
          "YYYY-YYYY-YYYY-YYYY",
          "ZZZZ-ZZZZ-ZZZZ-ZZZZ",
          "AAAA-AAAA-AAAA-AAAA",
          "BBBB-BBBB-BBBB-BBBB",
          "CCCC-CCCC-CCCC-CCCC",
          "DDDD-DDDD-DDDD-DDDD",
          "EEEE-EEEE-EEEE-EEEE",
        ]);
        setStep("recovery");
      }
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "Verification failed. Please try again.";
      setError(errorMsg);
    } finally {
      setVerifying(false);
    }
  }

  async function handleCopySecret() {
    const success = await copyToClipboard(manualKey);
    if (success) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }

  function handleDownloadCodes() {
    downloadRecoveryCodes(recoveryCodes, "talk-lee-mfa-recovery-codes.txt");
    setDownloadedCodes(true);
  }

  function handleCompleteSetup() {
    onSuccess();
  }

  if (loading) {
    return (
      <Card>
        <CardHeader className="text-center">
          <CardTitle>Setting up Two-Factor Authentication</CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-hidden />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="text-center">
        <CardTitle>Set Up Two-Factor Authentication</CardTitle>
        <CardDescription>Secure your account with an authenticator app</CardDescription>
      </CardHeader>

      <CardContent>
        <Tabs value={step} onValueChange={(value) => setStep(value as Step)} className="w-full">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="qr" disabled={step !== "qr" && step !== "verify"}>
              QR Code
            </TabsTrigger>
            <TabsTrigger value="verify" disabled={!qrCode}>
              Verify
            </TabsTrigger>
            <TabsTrigger value="recovery" disabled={!recoveryCodes.length}>
              Recovery
            </TabsTrigger>
          </TabsList>

          {/* Step 1: QR Code */}
          <TabsContent value="qr" className="space-y-4">
            <div className="space-y-4">
              <div className="bg-white dark:bg-white/5 border border-border rounded-lg p-4 flex items-center justify-center">
                {qrCode ? (
                  <img src={qrCode} alt="MFA QR Code" className="w-48 h-48" />
                ) : (
                  <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                )}
              </div>

              <div className="space-y-2">
                <p className="text-sm text-muted-foreground text-center">
                  Can&apos;t scan the QR code? Enter this key manually in your authenticator app:
                </p>
                <div className="bg-gray-50 dark:bg-white/5 border border-border rounded-md p-3">
                  <div className="flex items-center justify-between gap-2">
                    <code className="text-sm font-mono break-all">{manualKey}</code>
                    <button
                      type="button"
                      onClick={handleCopySecret}
                      className="flex-shrink-0 p-2 hover:bg-gray-200 dark:hover:bg-white/10 rounded-md transition-colors"
                      title="Copy to clipboard"
                    >
                      {copied ? (
                        <Check className="h-4 w-4 text-green-600" aria-hidden />
                      ) : (
                        <Copy className="h-4 w-4 text-muted-foreground" aria-hidden />
                      )}
                    </button>
                  </div>
                </div>
              </div>

              <div className="bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded-md p-3">
                <p className="text-xs text-amber-700 dark:text-amber-300">
                  <strong>Recommended apps:</strong> Google Authenticator, Microsoft Authenticator, Authy, or 1Password
                </p>
              </div>
            </div>
          </TabsContent>

          {/* Step 2: Verify */}
          <TabsContent value="verify" className="space-y-4">
            <form onSubmit={handleVerifyCode} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="verify-code">Enter the 6-digit code from your authenticator</Label>
                <Input
                  id="verify-code"
                  type="text"
                  placeholder="000000"
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, "").slice(0, 8))}
                  className="text-center text-lg tracking-widest font-mono"
                  maxLength={8}
                  inputMode="numeric"
                  pattern="[0-9]*"
                  required
                  disabled={verifying}
                  ref={codeInputRef}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={error ? errorId : undefined}
                />
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

              <Button type="submit" className="w-full" disabled={verifying || totpCode.length < 6}>
                {verifying ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin mr-2" aria-hidden />
                    Verifying...
                  </>
                ) : (
                  <>
                    Verify & Continue
                    <ArrowRight className="h-4 w-4 ml-2" aria-hidden />
                  </>
                )}
              </Button>
            </form>
          </TabsContent>

          {/* Step 3: Recovery Codes */}
          <TabsContent value="recovery" className="space-y-4">
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Save these recovery codes in a safe place. Each code can be used once if you lose access to your authenticator.
              </p>

              <div className="bg-gray-50 dark:bg-white/5 border border-border rounded-md p-4 max-h-64 overflow-y-auto">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {recoveryCodes.map((code, index) => (
                    <code
                      key={index}
                      className="text-sm font-mono p-2 bg-white dark:bg-white/10 rounded border border-border text-center"
                    >
                      {code}
                    </code>
                  ))}
                </div>
              </div>

              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="outline"
                  className="flex-1"
                  onClick={handleDownloadCodes}
                  disabled={downloadedCodes}
                >
                  <Download className="h-4 w-4 mr-2" aria-hidden />
                  {downloadedCodes ? "Downloaded" : "Download"}
                </Button>
              </div>

              <div className="bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 rounded-md p-3">
                <p className="text-xs text-green-700 dark:text-green-300">
                  ✓ Keep these codes secure. Don&apos;t share them with anyone.
                </p>
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </CardContent>

      <CardFooter className="flex gap-2">
        {step === "recovery" ? (
          <>
            <Button type="button" variant="outline" className="flex-1" onClick={onCancel}>
              Cancel
            </Button>
            <Button type="button" className="flex-1" onClick={handleCompleteSetup} disabled={!downloadedCodes}>
              Complete Setup
              <ArrowRight className="h-4 w-4 ml-2" aria-hidden />
            </Button>
          </>
        ) : (
          <Button
            type="button"
            variant="outline"
            className="w-full"
            onClick={onCancel}
          >
            Cancel
          </Button>
        )}
      </CardFooter>
    </Card>
  );
}
