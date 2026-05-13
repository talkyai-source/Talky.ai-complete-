"use client";

import Link from "next/link";
import { Suspense, useId, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
    ArrowLeft,
    ArrowRight,
    CheckCircle2,
    KeyRound,
    Loader2,
    Lock,
    Mail,
} from "lucide-react";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import {
    Card,
    CardContent,
    CardDescription,
    CardFooter,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiBaseUrl } from "@/lib/env";

const emailSchema = z.string().email("Please enter a valid email address");

type Step = "email" | "reset" | "done";

function ForgotPasswordInner() {
    const router = useRouter();
    const searchParams = useSearchParams();
    const [step, setStep] = useState<Step>("email");
    const [email, setEmail] = useState(searchParams.get("email") ?? "");
    const [code, setCode] = useState("");
    const [newPassword, setNewPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [emailError, setEmailError] = useState("");
    const inputRef = useRef<HTMLInputElement | null>(null);
    const errorId = useId();

    async function handleSendCode(e: React.FormEvent) {
        e.preventDefault();
        const result = emailSchema.safeParse(email);
        if (!result.success) {
            setEmailError(result.error.errors[0]?.message ?? "Invalid email");
            return;
        }
        setEmailError("");
        setLoading(true);
        setError("");

        try {
            const res = await fetch(`${apiBaseUrl()}/auth/forgot-password`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email }),
            });
            if (!res.ok) {
                const body = await res.json().catch(() => null);
                throw new Error(
                    body?.detail || body?.message || "Failed to send reset code",
                );
            }
            // Backend always returns 200 to prevent user-enumeration. Move on
            // to the code-entry step regardless — if the email isn't
            // registered the user just won't receive a code, which is the
            // expected UX for this defence.
            setStep("reset");
        } catch (err) {
            setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
        } finally {
            setLoading(false);
        }
    }

    async function handleReset(e: React.FormEvent) {
        e.preventDefault();
        setError("");
        if (!code.trim()) {
            setError("Enter the 6-digit code from your email.");
            return;
        }
        if (newPassword.length < 8) {
            setError("Password must be at least 8 characters.");
            return;
        }
        if (newPassword !== confirmPassword) {
            setError("Passwords do not match.");
            return;
        }
        setLoading(true);
        try {
            const res = await fetch(`${apiBaseUrl()}/auth/reset-password`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    email,
                    code: code.trim(),
                    new_password: newPassword,
                    confirm_password: confirmPassword,
                }),
            });
            if (!res.ok) {
                const body = await res.json().catch(() => null);
                throw new Error(
                    body?.detail || body?.message || "Failed to reset password",
                );
            }
            setStep("done");
            // Auto-redirect after a moment so the user actually reads the
            // success state before being pulled back to /auth/login.
            setTimeout(() => {
                router.push(`/auth/login?email=${encodeURIComponent(email)}`);
            }, 2500);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
        } finally {
            setLoading(false);
        }
    }

    return (
        <div className="relative min-h-screen bg-transparent flex items-center justify-center p-4 overflow-hidden">
            <div className="absolute inset-0 z-0 pointer-events-none">
                <div className="absolute inset-0 authHeroGradientBase" />
                <div className="absolute -inset-[30%] authHeroGradientBlobs" />
                <div className="absolute inset-0 authHeroGradientVignette" />
                <div className="absolute inset-0 authServicesGrid" />
            </div>

            <div className="relative z-10 w-full max-w-md">
                <div className="text-center mb-8">
                    <Link href="/" className="inline-block">
                        <p className="text-3xl font-bold tracking-tight text-foreground">
                            Talk-Lee
                        </p>
                        <p className="text-sm text-[#D2B48C] dark:text-cyan-400 mt-1">
                            AI Voice Dialer
                        </p>
                    </Link>
                </div>

                <Card>
                    <CardHeader className="text-center">
                        <CardTitle asChild>
                            <h1>
                                {step === "done"
                                    ? "Password reset"
                                    : step === "reset"
                                      ? "Enter your code"
                                      : "Reset your password"}
                            </h1>
                        </CardTitle>
                        <CardDescription>
                            {step === "done"
                                ? "Redirecting you to sign in..."
                                : step === "reset"
                                  ? `Enter the 6-digit code we sent to ${email}, and choose a new password.`
                                  : "Enter your email and we'll send you a 6-digit code."}
                        </CardDescription>
                    </CardHeader>

                    {step === "done" ? (
                        <>
                            <CardContent>
                                <div className="text-center space-y-3">
                                    <div className="mx-auto w-12 h-12 rounded-full bg-green-100 dark:bg-green-900/30 flex items-center justify-center">
                                        <CheckCircle2 className="h-6 w-6 text-green-600 dark:text-green-400" />
                                    </div>
                                    <p className="text-sm text-muted-foreground">
                                        Your password has been reset. You can now sign in with your new password.
                                    </p>
                                </div>
                            </CardContent>
                            <CardFooter>
                                <Link
                                    href={`/auth/login?email=${encodeURIComponent(email)}`}
                                    className="text-sm text-muted-foreground hover:text-foreground flex items-center justify-center gap-1 w-full py-2"
                                >
                                    <ArrowLeft className="h-3 w-3" aria-hidden />
                                    Go to login
                                </Link>
                            </CardFooter>
                        </>
                    ) : step === "reset" ? (
                        <form
                            onSubmit={handleReset}
                            aria-busy={loading}
                            aria-describedby={error ? errorId : undefined}
                        >
                            <CardContent className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="reset-code">Code</Label>
                                    <div className="relative">
                                        <KeyRound
                                            className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"
                                            aria-hidden
                                        />
                                        <Input
                                            id="reset-code"
                                            type="text"
                                            inputMode="numeric"
                                            placeholder="123456"
                                            value={code}
                                            onChange={(e) => {
                                                setCode(e.target.value.replace(/[^0-9]/g, ""));
                                                if (error) setError("");
                                            }}
                                            className="pl-10 font-mono tracking-widest"
                                            required
                                            maxLength={6}
                                            disabled={loading}
                                            autoFocus
                                        />
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="reset-new-password">New password</Label>
                                    <div className="relative">
                                        <Lock
                                            className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"
                                            aria-hidden
                                        />
                                        <Input
                                            id="reset-new-password"
                                            type="password"
                                            placeholder="At least 8 characters"
                                            value={newPassword}
                                            onChange={(e) => {
                                                setNewPassword(e.target.value);
                                                if (error) setError("");
                                            }}
                                            className="pl-10"
                                            required
                                            minLength={8}
                                            disabled={loading}
                                        />
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="reset-confirm-password">Confirm new password</Label>
                                    <div className="relative">
                                        <Lock
                                            className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"
                                            aria-hidden
                                        />
                                        <Input
                                            id="reset-confirm-password"
                                            type="password"
                                            placeholder="Repeat new password"
                                            value={confirmPassword}
                                            onChange={(e) => {
                                                setConfirmPassword(e.target.value);
                                                if (error) setError("");
                                            }}
                                            className="pl-10"
                                            required
                                            minLength={8}
                                            disabled={loading}
                                        />
                                    </div>
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
                            </CardContent>

                            <CardFooter className="flex flex-col gap-3">
                                <Button type="submit" className="w-full" disabled={loading}>
                                    {loading ? (
                                        <>
                                            <Loader2
                                                className="h-4 w-4 animate-spin"
                                                aria-hidden
                                            />
                                            Resetting...
                                        </>
                                    ) : (
                                        <>
                                            Reset Password
                                            <ArrowRight className="h-4 w-4" aria-hidden />
                                        </>
                                    )}
                                </Button>
                                <button
                                    type="button"
                                    onClick={() => {
                                        setStep("email");
                                        setError("");
                                        setCode("");
                                        setNewPassword("");
                                        setConfirmPassword("");
                                    }}
                                    className="text-sm text-muted-foreground hover:text-foreground flex items-center justify-center gap-1 w-full py-2"
                                    disabled={loading}
                                >
                                    <ArrowLeft className="h-3 w-3" aria-hidden />
                                    Use a different email
                                </button>
                            </CardFooter>
                        </form>
                    ) : (
                        <form
                            onSubmit={handleSendCode}
                            aria-busy={loading}
                            aria-describedby={error ? errorId : undefined}
                        >
                            <CardContent className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="reset-email">Email</Label>
                                    <div className="relative">
                                        <Mail
                                            className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"
                                            aria-hidden
                                        />
                                        <Input
                                            id="reset-email"
                                            type="email"
                                            placeholder="you@example.com"
                                            value={email}
                                            onChange={(e) => {
                                                setEmail(e.target.value);
                                                if (emailError) setEmailError("");
                                            }}
                                            className="pl-10"
                                            required
                                            disabled={loading}
                                            ref={inputRef}
                                            aria-invalid={
                                                emailError || error ? true : undefined
                                            }
                                        />
                                    </div>
                                    {emailError && (
                                        <p className="text-sm text-red-600 dark:text-red-400">
                                            {emailError}
                                        </p>
                                    )}
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
                            </CardContent>

                            <CardFooter className="flex flex-col gap-4">
                                <Button type="submit" className="w-full" disabled={loading}>
                                    {loading ? (
                                        <>
                                            <Loader2
                                                className="h-4 w-4 animate-spin"
                                                aria-hidden
                                            />
                                            Sending...
                                        </>
                                    ) : (
                                        <>
                                            Send Reset Code
                                            <ArrowRight className="h-4 w-4" aria-hidden />
                                        </>
                                    )}
                                </Button>
                                <Link
                                    href="/auth/login"
                                    className="text-sm text-muted-foreground hover:text-foreground flex items-center justify-center gap-1 w-full py-2"
                                >
                                    <ArrowLeft className="h-3 w-3" aria-hidden />
                                    Back to login
                                </Link>
                            </CardFooter>
                        </form>
                    )}
                </Card>
            </div>
        </div>
    );
}

export default function ForgotPasswordPage() {
    return (
        <Suspense>
            <ForgotPasswordInner />
        </Suspense>
    );
}
