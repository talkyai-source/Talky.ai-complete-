"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
    ArrowLeft,
    ArrowRight,
    Eye,
    EyeOff,
    Loader2,
    Lock,
    Mail,
    ShieldAlert,
} from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
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
import { api } from "@/lib/api";
import MFAVerification from "@/components/auth/mfa-verification";
import PasskeyLogin from "@/components/auth/passkey-login";

// ─── Types ───────────────────────────────────────────────────────────────────
type Step = "email" | "password" | "mfa" | "passkey";
type LoginTokens = { access_token: string; refresh_token: string; role?: string };

// ─── Validation ──────────────────────────────────────────────────────────────
const emailSchema = z.string().email("Please enter a valid email address");

// ─── Constants ───────────────────────────────────────────────────────────────
const TURNSTILE_SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY;

// ─── Animation variants ─────────────────────────────────────────────────────
const stepVariants = {
    enter: (dir: number) => ({ x: dir > 0 ? 24 : -24, opacity: 0 }),
    center: { x: 0, opacity: 1 },
    exit: (dir: number) => ({ x: dir > 0 ? -24 : 24, opacity: 0 }),
};
const stepTransition = { duration: 0.2, ease: "easeInOut" as const };

// ─── HIBP Password Breach Check (k-anonymity) ───────────────────────────────
async function checkPasswordBreach(password: string): Promise<number> {
    try {
        const encoder = new TextEncoder();
        const data = encoder.encode(password);
        const hashBuffer = await crypto.subtle.digest("SHA-1", data);
        const hashHex = Array.from(new Uint8Array(hashBuffer))
            .map((b) => b.toString(16).padStart(2, "0"))
            .join("")
            .toUpperCase();
        const prefix = hashHex.slice(0, 5);
        const suffix = hashHex.slice(5);

        const res = await fetch(`https://api.pwnedpasswords.com/range/${prefix}`, {
            cache: "force-cache",
        });
        if (!res.ok) return 0;

        const text = await res.text();
        for (const line of text.split("\n")) {
            const [hash, count] = line.split(":");
            if (hash?.trim() === suffix) return parseInt(count?.trim() ?? "0", 10);
        }
        return 0;
    } catch {
        return 0;
    }
}

// ─── Turnstile CAPTCHA Widget (conditional on env var) ───────────────────────
function TurnstileWidget({
    siteKey,
    onVerify,
    onExpire,
}: {
    siteKey: string;
    onVerify: (token: string) => void;
    onExpire: () => void;
}) {
    const containerRef = useRef<HTMLDivElement>(null);
    const widgetIdRef = useRef<string | null>(null);

    useEffect(() => {
        if (!containerRef.current) return;

        const existing = document.querySelector(
            'script[src*="challenges.cloudflare.com/turnstile"]',
        );

        function render() {
            if (!containerRef.current || widgetIdRef.current) return;
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const w = (window as any).turnstile as
                | { render: (el: HTMLElement, opts: Record<string, unknown>) => string; remove: (id: string) => void }
                | undefined;
            if (!w) return;
            widgetIdRef.current = w.render(containerRef.current, {
                sitekey: siteKey,
                callback: onVerify,
                "expired-callback": onExpire,
                theme: "auto",
                size: "flexible",
            });
        }

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        if (existing && (window as any).turnstile) {
            render();
        } else if (!existing) {
            const script = document.createElement("script");
            script.src =
                "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
            script.async = true;
            script.onload = () => render();
            document.head.appendChild(script);
        } else {
            const id = setInterval(() => {
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                if ((window as any).turnstile) {
                    clearInterval(id);
                    render();
                }
            }, 100);
            return () => clearInterval(id);
        }

        return () => {
            if (widgetIdRef.current) {
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                const w = (window as any).turnstile as
                    | { remove: (id: string) => void }
                    | undefined;
                try { w?.remove(widgetIdRef.current); } catch { /* ignore */ }
                widgetIdRef.current = null;
            }
        };
    }, [siteKey, onVerify, onExpire]);

    return <div ref={containerRef} className="flex justify-center" />;
}

// ─── Main Login Component ────────────────────────────────────────────────────
export default function LoginClientPage() {
    const router = useRouter();
    const searchParams = useSearchParams();

    const [step, setStep] = useState<Step>("email");
    const [direction, setDirection] = useState(1);
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [showPassword, setShowPassword] = useState(false);
    const [rememberMe, setRememberMe] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [emailError, setEmailError] = useState("");
    const [showPasskey, setShowPasskey] = useState(false);
    const [breachCount, setBreachCount] = useState<number | null>(null);
    const [checkingBreach, setCheckingBreach] = useState(false);
    const [turnstileToken, setTurnstileToken] = useState<string | null>(null);

    const emailInputRef = useRef<HTMLInputElement | null>(null);
    const errorId = useId();

    // Auto-focus email input
    useEffect(() => {
        const t = window.setTimeout(() => {
            if (step === "email") emailInputRef.current?.focus();
        }, 0);
        return () => window.clearTimeout(t);
    }, [step]);

    // ─── Shared post-login handler (deduplicated) ────────────────────
    const handleLoginSuccess = useCallback(
        async (tokens: LoginTokens) => {
            api.setToken(tokens.access_token);
            localStorage.setItem("refresh_token", tokens.refresh_token);
            if (rememberMe) localStorage.setItem("remember_me", "true");

            const rawNext = searchParams.get("next");
            const safeNext =
                rawNext && rawNext.startsWith("/") && !rawNext.startsWith("//")
                    ? rawNext
                    : null;

            // Use the role straight from the login response. Calling
            // /auth/me here used to be the source of a "after login,
            // redirected back to login" bug: any 401 from that round-trip
            // (clock skew on a freshly-issued token, transient backend
            // hiccup, etc.) would trip the http-client's session-expired
            // handler, clear the token we just stored, and bounce the user
            // to /auth/login — making it look like the login never worked.
            const role = tokens.role ?? null;

            router.push(
                role === "white_label_admin"
                    ? "/white-label/dashboard"
                    : safeNext ?? "/dashboard",
            );
        },
        [router, searchParams, rememberMe],
    );

    // ─── Step navigation ─────────────────────────────────────────────
    function goToStep(next: Step, dir: 1 | -1 = 1) {
        setDirection(dir);
        setStep(next);
        setError("");
    }

    // ─── Email validation (Zod) ──────────────────────────────────────
    function validateEmail(): boolean {
        const result = emailSchema.safeParse(email);
        if (!result.success) {
            setEmailError(result.error.errors[0]?.message ?? "Invalid email");
            return false;
        }
        setEmailError("");
        return true;
    }

    // ─── Account lockout / rate-limit error parser ───────────────────
    function parseApiError(err: unknown): string {
        if (err instanceof Error) {
            const msg = err.message.toLowerCase();
            if (msg.includes("locked") || msg.includes("too many attempts")) {
                return "Your account has been temporarily locked due to too many failed attempts. Please try again in 15 minutes or reset your password.";
            }
            if (msg.includes("rate limit") || msg.includes("429")) {
                return "Too many requests. Please wait a moment before trying again.";
            }
            return err.message;
        }
        if (typeof err === "object" && err !== null) {
            return (err as { detail?: string }).detail || "An error occurred";
        }
        return "An unexpected error occurred. Please try again.";
    }

    // ─── Password submit ─────────────────────────────────────────────
    async function handlePasswordSubmit(e: React.FormEvent) {
        e.preventDefault();
        setLoading(true);
        setError("");

        try {
            void turnstileToken;
            const response = await api.login(email, password);

            if ((response as unknown as { mfa_required?: boolean }).mfa_required) {
                goToStep("mfa");
                return;
            }

            await handleLoginSuccess(response as unknown as LoginTokens);
        } catch (err) {
            setError(parseApiError(err));
        } finally {
            setLoading(false);
        }
    }

    // ─── HIBP breach check on password blur ──────────────────────────
    async function handlePasswordBlur() {
        if (!password || password.length < 4) return;
        setCheckingBreach(true);
        const count = await checkPasswordBreach(password);
        setBreachCount(count);
        setCheckingBreach(false);
    }

    // ─── Back handler ────────────────────────────────────────────────
    function handleBack() {
        goToStep("email", -1);
        setPassword("");
        setShowPasskey(false);
        setBreachCount(null);
    }

    // ─── Step description ────────────────────────────────────────────
    function getStepDescription(): string {
        if (step === "email") return "Sign in with your email and password";
        if (step === "password") return "Enter your password to continue";
        if (step === "mfa") return "Verify with two-factor authentication";
        if (step === "passkey") return "Sign in with a passkey";
        return "";
    }

    // ─── Render ──────────────────────────────────────────────────────
    return (
        <div className="relative min-h-screen bg-transparent flex items-center justify-center p-4 overflow-hidden">
            {/* Background effects */}
            <div className="absolute inset-0 z-0 pointer-events-none">
                <div className="absolute inset-0 authHeroGradientBase" />
                <div className="absolute -inset-[30%] authHeroGradientBlobs" />
                <div className="absolute inset-0 authHeroGradientVignette" />
                <div className="absolute inset-0 authServicesGrid" />
            </div>

            <div className="relative z-10 w-full max-w-md">
                {/* Branding */}
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

                <style>{`.login-card, .login-card:hover, .dark .login-card, .dark .login-card:hover { transform: none !important; translate: none !important; transition: none !important; box-shadow: 0 1px 2px 0 rgb(0 0 0 / 0.05) !important; border-color: inherit !important; } .login-card, .login-card:hover { background-color: white !important; } .dark .login-card, .dark .login-card:hover, .login-card:is(.dark *), .login-card:is(.dark *):hover { background-color: #0f172a !important; box-shadow: 0 1px 2px 0 rgb(0 0 0 / 0.1) !important; }`}</style>
                <Card className="login-card">
                    <CardHeader className="text-center">
                        <CardTitle asChild>
                            <h1>Welcome back</h1>
                        </CardTitle>
                        <CardDescription>{getStepDescription()}</CardDescription>
                    </CardHeader>

                    <AnimatePresence mode="wait" custom={direction}>
                        {/* ─── EMAIL STEP ──────────────────────────────── */}
                        {step === "email" && !showPasskey ? (
                            <motion.div
                                key="email"
                                custom={direction}
                                variants={stepVariants}
                                initial="enter"
                                animate="center"
                                exit="exit"
                                transition={stepTransition}
                            >
                                <form
                                    onSubmit={(e) => {
                                        e.preventDefault();
                                        if (validateEmail()) goToStep("password");
                                    }}
                                    aria-busy={loading}
                                    aria-describedby={error ? errorId : undefined}
                                >
                                    <CardContent className="space-y-4">
                                        {/* Email field */}
                                        <div className="space-y-2">
                                            <Label htmlFor="email">Email</Label>
                                            <div className="relative">
                                                <Mail
                                                    className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"
                                                    aria-hidden
                                                />
                                                <Input
                                                    id="email"
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
                                                    ref={emailInputRef}
                                                    aria-invalid={
                                                        emailError || error ? true : undefined
                                                    }
                                                    aria-describedby={
                                                        [
                                                            emailError ? "email-error" : null,
                                                            error ? errorId : null,
                                                        ]
                                                            .filter(Boolean)
                                                            .join(" ") || undefined
                                                    }
                                                />
                                            </div>
                                            {emailError && (
                                                <p
                                                    id="email-error"
                                                    className="text-sm text-red-600 dark:text-red-400"
                                                >
                                                    {emailError}
                                                </p>
                                            )}
                                        </div>

                                        {/* Remember me */}
                                        <label className="flex items-center gap-2 cursor-pointer select-none">
                                            <input
                                                type="checkbox"
                                                checked={rememberMe}
                                                onChange={(e) => setRememberMe(e.target.checked)}
                                                className="h-4 w-4 rounded border-input text-primary focus:ring-ring focus:ring-offset-background accent-primary"
                                            />
                                            <span className="text-sm text-muted-foreground">
                                                Remember me
                                            </span>
                                        </label>

                                        {/* Turnstile CAPTCHA */}
                                        {TURNSTILE_SITE_KEY && (
                                            <TurnstileWidget
                                                siteKey={TURNSTILE_SITE_KEY}
                                                onVerify={setTurnstileToken}
                                                onExpire={() => setTurnstileToken(null)}
                                            />
                                        )}

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
                                        {/* Primary action */}
                                        <Button
                                            type="submit"
                                            className="w-full"
                                            disabled={
                                                loading ||
                                                (!!TURNSTILE_SITE_KEY && !turnstileToken)
                                            }
                                        >
                                            {loading ? (
                                                <>
                                                    <Loader2
                                                        className="h-4 w-4 animate-spin"
                                                        aria-hidden
                                                    />
                                                    Continuing...
                                                </>
                                            ) : (
                                                <>
                                                    Continue with Password
                                                    <ArrowRight
                                                        className="h-4 w-4"
                                                        aria-hidden
                                                    />
                                                </>
                                            )}
                                        </Button>

                                        {/* Alternative login method */}
                                        <div className="flex flex-col items-center gap-2 w-full">
                                            <button
                                                type="button"
                                                onClick={() => setShowPasskey(true)}
                                                className="text-sm text-muted-foreground hover:text-foreground hover:underline"
                                            >
                                                Sign in with Passkey
                                            </button>
                                        </div>

                                        <p className="text-sm text-muted-foreground text-center">
                                            New to Talk-Lee?{" "}
                                            <Link
                                                href="/auth/register"
                                                className="text-foreground font-medium hover:underline"
                                            >
                                                Create an account
                                            </Link>
                                        </p>
                                    </CardFooter>
                                </form>
                            </motion.div>

                        /* ─── PASSWORD STEP ─────────────────────────── */
                        ) : step === "password" ? (
                            <motion.div
                                key="password"
                                custom={direction}
                                variants={stepVariants}
                                initial="enter"
                                animate="center"
                                exit="exit"
                                transition={stepTransition}
                            >
                                <form
                                    onSubmit={handlePasswordSubmit}
                                    aria-busy={loading}
                                    aria-describedby={error ? errorId : undefined}
                                >
                                    <CardContent className="space-y-4">
                                        <div className="space-y-2">
                                            <Label htmlFor="login-password">Password</Label>
                                            <div className="relative">
                                                <Lock
                                                    className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"
                                                    aria-hidden
                                                />
                                                <Input
                                                    id="login-password"
                                                    type={showPassword ? "text" : "password"}
                                                    placeholder="Enter your password"
                                                    value={password}
                                                    onChange={(e) => {
                                                        setPassword(e.target.value);
                                                        setBreachCount(null);
                                                    }}
                                                    onBlur={handlePasswordBlur}
                                                    className="pl-10 pr-10"
                                                    required
                                                    disabled={loading}
                                                    autoComplete="current-password"
                                                    autoFocus
                                                />
                                                {/* Password visibility toggle */}
                                                <button
                                                    type="button"
                                                    onClick={() =>
                                                        setShowPassword(!showPassword)
                                                    }
                                                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                                                    aria-label={
                                                        showPassword
                                                            ? "Hide password"
                                                            : "Show password"
                                                    }
                                                    tabIndex={-1}
                                                >
                                                    {showPassword ? (
                                                        <EyeOff className="h-4 w-4" />
                                                    ) : (
                                                        <Eye className="h-4 w-4" />
                                                    )}
                                                </button>
                                            </div>
                                            <div className="flex items-center justify-between">
                                                <p className="text-xs text-muted-foreground">
                                                    Signing in as {email}
                                                </p>
                                                {/* Forgot password link */}
                                                <Link
                                                    href={`/auth/forgot-password?email=${encodeURIComponent(email)}`}
                                                    className="text-xs text-foreground font-medium hover:underline"
                                                >
                                                    Forgot password?
                                                </Link>
                                            </div>
                                        </div>

                                        {/* Breach detection warning */}
                                        {breachCount !== null && breachCount > 0 && (
                                            <div
                                                role="alert"
                                                className="flex items-start gap-2 text-sm text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded-md p-3"
                                            >
                                                <ShieldAlert
                                                    className="h-4 w-4 mt-0.5 shrink-0"
                                                    aria-hidden
                                                />
                                                <p>
                                                    This password has appeared in{" "}
                                                    <strong>
                                                        {breachCount.toLocaleString()}
                                                    </strong>{" "}
                                                    known data breaches. Consider changing it
                                                    after signing in.
                                                </p>
                                            </div>
                                        )}
                                        {checkingBreach && (
                                            <p className="text-xs text-muted-foreground flex items-center gap-1">
                                                <Loader2
                                                    className="h-3 w-3 animate-spin"
                                                    aria-hidden
                                                />
                                                Checking password security...
                                            </p>
                                        )}

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
                                        <Button
                                            type="submit"
                                            className="w-full"
                                            disabled={loading || !password}
                                        >
                                            {loading ? (
                                                <>
                                                    <Loader2
                                                        className="h-4 w-4 animate-spin"
                                                        aria-hidden
                                                    />
                                                    Signing in...
                                                </>
                                            ) : (
                                                <>
                                                    Sign In
                                                    <ArrowRight
                                                        className="h-4 w-4"
                                                        aria-hidden
                                                    />
                                                </>
                                            )}
                                        </Button>
                                        <div className="flex items-center justify-between w-full">
                                            <button
                                                type="button"
                                                onClick={handleBack}
                                                className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
                                                disabled={loading}
                                            >
                                                <ArrowLeft className="h-3 w-3" aria-hidden />{" "}
                                                Change email
                                            </button>
                                        </div>
                                    </CardFooter>
                                </form>
                            </motion.div>

                        /* ─── MFA STEP ──────────────────────────────── */
                        ) : step === "mfa" ? (
                            <motion.div
                                key="mfa"
                                custom={direction}
                                variants={stepVariants}
                                initial="enter"
                                animate="center"
                                exit="exit"
                                transition={stepTransition}
                            >
                                <MFAVerification
                                    email={email}
                                    onSuccess={handleLoginSuccess}
                                    onBackClick={handleBack}
                                    onError={(err) => setError(err)}
                                />
                            </motion.div>

                        /* ─── PASSKEY STEP ──────────────────────────── */
                        ) : step === "passkey" || showPasskey ? (
                            <motion.div
                                key="passkey"
                                custom={direction}
                                variants={stepVariants}
                                initial="enter"
                                animate="center"
                                exit="exit"
                                transition={stepTransition}
                            >
                                <div className="space-y-4">
                                    <CardContent className="space-y-4">
                                        <PasskeyLogin
                                            onSuccess={handleLoginSuccess}
                                            onError={(err) => setError(err)}
                                            disabled={loading}
                                        />
                                    </CardContent>
                                    <CardFooter>
                                        <button
                                            type="button"
                                            onClick={handleBack}
                                            className="text-sm text-muted-foreground hover:text-foreground flex items-center justify-center gap-1 w-full py-2"
                                            disabled={loading}
                                        >
                                            <ArrowLeft className="h-3 w-3" aria-hidden />
                                            Back to email
                                        </button>
                                    </CardFooter>
                                </div>
                            </motion.div>
                        ) : null}
                    </AnimatePresence>
                </Card>

                {/* Terms & Privacy with actual links */}
                <p className="text-xs text-muted-foreground text-center mt-8">
                    By continuing, you agree to our{" "}
                    <Link
                        href="/terms"
                        className="underline hover:text-foreground transition-colors"
                    >
                        Terms of Service
                    </Link>{" "}
                    and{" "}
                    <Link
                        href="/privacy"
                        className="underline hover:text-foreground transition-colors"
                    >
                        Privacy Policy
                    </Link>
                    .
                </p>
            </div>
        </div>
    );
}
