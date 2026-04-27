"use client";

import Link from "next/link";
import { Suspense, useId, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, ArrowRight, CheckCircle2, Loader2, Mail } from "lucide-react";
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

function ForgotPasswordInner() {
    const searchParams = useSearchParams();
    const [email, setEmail] = useState(searchParams.get("email") ?? "");
    const [loading, setLoading] = useState(false);
    const [sent, setSent] = useState(false);
    const [error, setError] = useState("");
    const [emailError, setEmailError] = useState("");
    const inputRef = useRef<HTMLInputElement | null>(null);
    const errorId = useId();

    async function handleSubmit(e: React.FormEvent) {
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
                    body?.detail || body?.message || "Failed to send reset link",
                );
            }
            setSent(true);
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
                            <h1>{sent ? "Check your email" : "Reset your password"}</h1>
                        </CardTitle>
                        <CardDescription>
                            {sent
                                ? `We sent a password reset link to ${email}`
                                : "Enter your email and we'll send you a reset link"}
                        </CardDescription>
                    </CardHeader>

                    {sent ? (
                        <>
                            <CardContent className="space-y-4">
                                <div className="text-center space-y-3">
                                    <div className="mx-auto w-12 h-12 rounded-full bg-green-100 dark:bg-green-900/30 flex items-center justify-center">
                                        <CheckCircle2 className="h-6 w-6 text-green-600 dark:text-green-400" />
                                    </div>
                                    <p className="text-sm text-muted-foreground">
                                        If an account exists for{" "}
                                        <strong className="text-foreground">{email}</strong>,
                                        you will receive a password reset link shortly.
                                    </p>
                                    <p className="text-xs text-muted-foreground">
                                        The link will expire in 30 minutes. Check your spam
                                        folder if you don&apos;t see it.
                                    </p>
                                </div>
                            </CardContent>
                            <CardFooter>
                                <Link
                                    href="/auth/login"
                                    className="text-sm text-muted-foreground hover:text-foreground flex items-center justify-center gap-1 w-full py-2"
                                >
                                    <ArrowLeft className="h-3 w-3" aria-hidden />
                                    Back to login
                                </Link>
                            </CardFooter>
                        </>
                    ) : (
                        <form
                            onSubmit={handleSubmit}
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
                                            Send Reset Link
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
