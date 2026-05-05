"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Building2, KeyRound, Loader2, Lock, Mail, User } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type Step = "form" | "otp" | "password";

export default function RegisterClientPage() {
    const router = useRouter();
    const searchParams = useSearchParams();
    const [step, setStep] = useState<Step>("form");
    const [formData, setFormData] = useState({
        email: "",
        businessName: "",
        name: "",
    });
    const [otpCode, setOtpCode] = useState("");
    const [password, setPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");
    const [loading, setLoading] = useState(false);
    const [message, setMessage] = useState("");
    const [error, setError] = useState("");
    const nameInputRef = useRef<HTMLInputElement | null>(null);
    const otpInputRef = useRef<HTMLInputElement | null>(null);
    const passwordInputRef = useRef<HTMLInputElement | null>(null);
    const errorId = useId();
    const messageId = useId();
    const otpHelpId = useId();

    useEffect(() => {
        const t = window.setTimeout(() => {
            if (step === "form") nameInputRef.current?.focus();
            if (step === "otp") otpInputRef.current?.focus();
            if (step === "password") passwordInputRef.current?.focus();
        }, 0);
        return () => window.clearTimeout(t);
    }, [step]);

    function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
        setFormData((prev) => ({
            ...prev,
            [e.target.name]: e.target.value,
        }));
    }

    function extractError(err: unknown, fallback: string): string {
        if (err instanceof Error) return err.message;
        if (typeof err === "object" && err !== null) {
            return (err as { detail?: string }).detail || fallback;
        }
        return fallback;
    }

    async function handleFormSubmit(e: React.FormEvent) {
        e.preventDefault();
        setLoading(true);
        setError("");
        setMessage("");

        try {
            const response = await api.signupStart(
                formData.name.trim(),
                formData.businessName.trim(),
                formData.email.trim(),
            );
            setMessage(response.message || "Verification code sent! Check your email.");
            setStep("otp");
        } catch (err) {
            setError(extractError(err, "Registration failed. Please try again."));
        } finally {
            setLoading(false);
        }
    }

    async function handleOtpSubmit(e: React.FormEvent) {
        e.preventDefault();
        setLoading(true);
        setError("");
        setMessage("");

        try {
            await api.signupVerifyCode(formData.email.trim(), otpCode);
            setStep("password");
        } catch (err) {
            setError(extractError(err, "Invalid or expired verification code."));
        } finally {
            setLoading(false);
        }
    }

    async function handlePasswordSubmit(e: React.FormEvent) {
        e.preventDefault();
        setError("");
        setMessage("");

        if (password !== confirmPassword) {
            setError("Passwords do not match.");
            return;
        }

        setLoading(true);
        try {
            const response = await api.signupComplete(
                formData.email.trim(),
                otpCode,
                password,
                confirmPassword,
            );
            api.setToken(response.access_token);

            const rawNext = searchParams.get("next");
            const safeNext = rawNext && rawNext.startsWith("/") && !rawNext.startsWith("//") ? rawNext : null;

            // Use the role straight from the signup response. Calling
            // /auth/me here triggered the same "back to /auth/login" bug
            // that the login flow already documented: a transient 401 on
            // that round-trip trips the http-client's session-expired
            // handler, which clears the just-stored token and redirects.
            const role = (response as { role?: string | null }).role ?? null;

            router.push(role === "white_label_admin" ? "/white-label/dashboard" : safeNext ?? "/dashboard");
        } catch (err) {
            setError(extractError(err, "Could not create account. Please try again."));
        } finally {
            setLoading(false);
        }
    }

    function handleBackToForm() {
        setStep("form");
        setOtpCode("");
        setPassword("");
        setConfirmPassword("");
        setError("");
        setMessage("");
    }

    function handleBackToOtp() {
        setStep("otp");
        setPassword("");
        setConfirmPassword("");
        setError("");
        setMessage("");
    }

    async function handleResend() {
        setLoading(true);
        setError("");
        setMessage("");

        try {
            const response = await api.signupStart(
                formData.name.trim(),
                formData.businessName.trim(),
                formData.email.trim(),
            );
            setMessage(response.message || "New verification code sent!");
        } catch (err) {
            setError(extractError(err, "Failed to resend code. Please try again."));
        } finally {
            setLoading(false);
        }
    }

    const headerCopy: Record<Step, { title: string; description: string }> = {
        form: {
            title: "Create your account",
            description: "Start automating your voice campaigns today",
        },
        otp: {
            title: "Verify your email",
            description: `Enter the verification code sent to ${formData.email}`,
        },
        password: {
            title: "Set your password",
            description: "Choose a strong password to secure your account",
        },
    };

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
                        <p className="text-3xl font-bold tracking-tight text-foreground">Talk-Lee</p>
                        <p className="text-sm text-[#D2B48C] dark:text-cyan-400 mt-1">AI Voice Dialer</p>
                    </Link>
                </div>

                <Card>
                    <CardHeader className="text-center">
                        <CardTitle asChild>
                            <h1>{headerCopy[step].title}</h1>
                        </CardTitle>
                        <CardDescription>{headerCopy[step].description}</CardDescription>
                    </CardHeader>

                    {step === "form" ? (
                        <form onSubmit={handleFormSubmit} aria-busy={loading} aria-describedby={error ? errorId : undefined}>
                            <CardContent className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="name">Your Name</Label>
                                    <div className="relative">
                                        <User className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" aria-hidden />
                                        <Input
                                            id="name"
                                            name="name"
                                            type="text"
                                            placeholder="John Doe"
                                            value={formData.name}
                                            onChange={handleChange}
                                            className="pl-10"
                                            required
                                            disabled={loading}
                                            ref={nameInputRef}
                                            aria-invalid={error ? true : undefined}
                                            aria-describedby={error ? errorId : undefined}
                                        />
                                    </div>
                                </div>

                                <div className="space-y-2">
                                    <Label htmlFor="businessName">Business Name</Label>
                                    <div className="relative">
                                        <Building2 className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" aria-hidden />
                                        <Input
                                            id="businessName"
                                            name="businessName"
                                            type="text"
                                            placeholder="Acme Inc."
                                            value={formData.businessName}
                                            onChange={handleChange}
                                            className="pl-10"
                                            required
                                            disabled={loading}
                                        />
                                    </div>
                                </div>

                                <div className="space-y-2">
                                    <Label htmlFor="email">Work Email</Label>
                                    <div className="relative">
                                        <Mail className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" aria-hidden />
                                        <Input
                                            id="email"
                                            name="email"
                                            type="email"
                                            placeholder="you@company.com"
                                            value={formData.email}
                                            onChange={handleChange}
                                            className="pl-10"
                                            required
                                            disabled={loading}
                                        />
                                    </div>
                                </div>

                                {error ? (
                                    <div id={errorId} role="alert" aria-live="assertive" className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 rounded-md p-3">
                                        {error}
                                    </div>
                                ) : null}
                            </CardContent>

                            <CardFooter className="flex flex-col gap-4">
                                <Button type="submit" className="w-full" disabled={loading}>
                                    {loading ? (
                                        <>
                                            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                                            Sending code...
                                        </>
                                    ) : (
                                        <>
                                            Get Started
                                            <ArrowRight className="h-4 w-4" aria-hidden />
                                        </>
                                    )}
                                </Button>

                                <p className="text-sm text-muted-foreground text-center">
                                    Already have an account?{" "}
                                    <Link href="/auth/login" className="text-foreground font-medium hover:underline">
                                        Sign in
                                    </Link>
                                </p>
                            </CardFooter>
                        </form>
                    ) : null}

                    {step === "otp" ? (
                        <form
                            onSubmit={handleOtpSubmit}
                            aria-busy={loading}
                            aria-describedby={[message ? messageId : null, error ? errorId : null, otpHelpId].filter(Boolean).join(" ")}
                        >
                            <CardContent className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="otp">Verification Code</Label>
                                    <div className="relative">
                                        <KeyRound className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" aria-hidden />
                                        <Input
                                            id="otp"
                                            type="text"
                                            placeholder="Enter verification code"
                                            value={otpCode}
                                            onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, "").slice(0, 8))}
                                            className="pl-10 text-center text-lg tracking-widest font-mono"
                                            required
                                            disabled={loading}
                                            maxLength={8}
                                            autoComplete="one-time-code"
                                            inputMode="numeric"
                                            pattern="[0-9]*"
                                            ref={otpInputRef}
                                            aria-invalid={error ? true : undefined}
                                            aria-describedby={[message ? messageId : null, error ? errorId : null, otpHelpId].filter(Boolean).join(" ")}
                                        />
                                    </div>
                                    <p id={otpHelpId} className="text-xs text-muted-foreground text-center">
                                        Check your email for the verification code
                                    </p>
                                </div>

                                {error ? (
                                    <div id={errorId} role="alert" aria-live="assertive" className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 rounded-md p-3">
                                        {error}
                                    </div>
                                ) : null}

                                {message ? (
                                    <div id={messageId} role="status" aria-live="polite" className="text-sm text-green-700 dark:text-green-400 bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-800 rounded-md p-3">
                                        {message}
                                    </div>
                                ) : null}
                            </CardContent>

                            <CardFooter className="flex flex-col gap-4">
                                <Button type="submit" className="w-full" disabled={loading || otpCode.length < 6}>
                                    {loading ? (
                                        <>
                                            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                                            Verifying...
                                        </>
                                    ) : (
                                        <>
                                            Next
                                            <ArrowRight className="h-4 w-4" aria-hidden />
                                        </>
                                    )}
                                </Button>

                                <div className="flex items-center justify-between w-full">
                                    <button
                                        type="button"
                                        onClick={handleBackToForm}
                                        className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
                                        disabled={loading}
                                    >
                                        <ArrowLeft className="h-3 w-3" aria-hidden />
                                        Back
                                    </button>
                                    <button
                                        type="button"
                                        onClick={handleResend}
                                        className="text-sm text-foreground font-medium hover:underline"
                                        disabled={loading}
                                    >
                                        Didn&apos;t receive it? Resend
                                    </button>
                                </div>
                            </CardFooter>
                        </form>
                    ) : null}

                    {step === "password" ? (
                        <form
                            onSubmit={handlePasswordSubmit}
                            aria-busy={loading}
                            aria-describedby={error ? errorId : undefined}
                        >
                            <CardContent className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="password">Password</Label>
                                    <div className="relative">
                                        <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" aria-hidden />
                                        <Input
                                            id="password"
                                            type="password"
                                            placeholder="At least 8 characters"
                                            value={password}
                                            onChange={(e) => setPassword(e.target.value)}
                                            className="pl-10"
                                            required
                                            minLength={8}
                                            disabled={loading}
                                            autoComplete="new-password"
                                            ref={passwordInputRef}
                                            aria-invalid={error ? true : undefined}
                                            aria-describedby={error ? errorId : undefined}
                                        />
                                    </div>
                                </div>

                                <div className="space-y-2">
                                    <Label htmlFor="confirmPassword">Confirm Password</Label>
                                    <div className="relative">
                                        <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" aria-hidden />
                                        <Input
                                            id="confirmPassword"
                                            type="password"
                                            placeholder="Re-enter your password"
                                            value={confirmPassword}
                                            onChange={(e) => setConfirmPassword(e.target.value)}
                                            className="pl-10"
                                            required
                                            minLength={8}
                                            disabled={loading}
                                            autoComplete="new-password"
                                        />
                                    </div>
                                </div>

                                {error ? (
                                    <div id={errorId} role="alert" aria-live="assertive" className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 rounded-md p-3">
                                        {error}
                                    </div>
                                ) : null}
                            </CardContent>

                            <CardFooter className="flex flex-col gap-4">
                                <Button
                                    type="submit"
                                    className="w-full"
                                    disabled={loading || password.length < 8 || confirmPassword.length < 8}
                                >
                                    {loading ? (
                                        <>
                                            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                                            Creating account...
                                        </>
                                    ) : (
                                        <>
                                            Create Account
                                            <ArrowRight className="h-4 w-4" aria-hidden />
                                        </>
                                    )}
                                </Button>

                                <div className="flex items-center justify-start w-full">
                                    <button
                                        type="button"
                                        onClick={handleBackToOtp}
                                        className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
                                        disabled={loading}
                                    >
                                        <ArrowLeft className="h-3 w-3" aria-hidden />
                                        Back
                                    </button>
                                </div>
                            </CardFooter>
                        </form>
                    ) : null}
                </Card>

                <p className="text-xs text-muted-foreground text-center mt-8">
                    By creating an account, you agree to our Terms of Service and Privacy Policy.
                </p>
            </div>

        </div>
    );
}
