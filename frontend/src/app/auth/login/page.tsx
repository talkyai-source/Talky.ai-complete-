"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Mail, ArrowRight, Loader2, KeyRound, ArrowLeft } from "lucide-react";

type Step = "email" | "otp";

export default function LoginPage() {
    const router = useRouter();
    const [step, setStep] = useState<Step>("email");
    const [email, setEmail] = useState("");
    const [otpCode, setOtpCode] = useState("");
    const [loading, setLoading] = useState(false);
    const [message, setMessage] = useState("");
    const [error, setError] = useState("");

    // Handle email submission - sends OTP code
    async function handleEmailSubmit(e: React.FormEvent) {
        e.preventDefault();
        setLoading(true);
        setError("");
        setMessage("");

        try {
            const response = await api.login(email);
            const msg = typeof response === "string"
                ? response
                : response?.message || "Verification code sent! Check your email.";
            setMessage(msg);
            setStep("otp"); // Move to OTP input step
        } catch (err) {
            if (err instanceof Error) {
                setError(err.message);
            } else if (typeof err === "object" && err !== null) {
                setError((err as { detail?: string }).detail || "Login failed");
            } else {
                setError("Login failed. Please try again.");
            }
        } finally {
            setLoading(false);
        }
    }

    // Handle OTP verification
    async function handleOtpSubmit(e: React.FormEvent) {
        e.preventDefault();
        setLoading(true);
        setError("");

        try {
            const response = await api.verifyOtp(email, otpCode);

            // Store the tokens
            api.setToken(response.access_token);
            if (typeof window !== "undefined") {
                localStorage.setItem("refresh_token", response.refresh_token);
            }

            // Redirect to dashboard
            router.push("/dashboard");
        } catch (err) {
            if (err instanceof Error) {
                setError(err.message);
            } else if (typeof err === "object" && err !== null) {
                setError((err as { detail?: string }).detail || "Verification failed");
            } else {
                setError("Verification failed. Please try again.");
            }
        } finally {
            setLoading(false);
        }
    }

    // Go back to email step
    function handleBack() {
        setStep("email");
        setOtpCode("");
        setError("");
        setMessage("");
    }

    // Resend OTP code
    async function handleResend() {
        setLoading(true);
        setError("");
        setMessage("");

        try {
            const response = await api.login(email);
            const msg = typeof response === "string"
                ? response
                : response?.message || "New verification code sent!";
            setMessage(msg);
        } catch (err) {
            if (err instanceof Error) {
                setError(err.message);
            } else {
                setError("Failed to resend code. Please try again.");
            }
        } finally {
            setLoading(false);
        }
    }

    return (
        <div className="min-h-screen bg-neutral-50 flex items-center justify-center p-4">
            <div className="w-full max-w-md">
                {/* Logo */}
                <div className="text-center mb-8">
                    <Link href="/" className="inline-block">
                        <h1 className="text-3xl font-bold tracking-tight text-gray-900">
                            Talky.ai
                        </h1>
                        <p className="text-sm text-gray-500 mt-1">AI Voice Dialer</p>
                    </Link>
                </div>

                <Card>
                    <CardHeader className="text-center">
                        <CardTitle>Welcome back</CardTitle>
                        <CardDescription>
                            {step === "email"
                                ? "Enter your email to receive a verification code"
                                : `Enter the verification code sent to ${email}`
                            }
                        </CardDescription>
                    </CardHeader>

                    {step === "email" ? (
                        // Step 1: Email input
                        <form onSubmit={handleEmailSubmit}>
                            <CardContent className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="email">Email</Label>
                                    <div className="relative">
                                        <Mail className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                                        <Input
                                            id="email"
                                            type="email"
                                            placeholder="you@example.com"
                                            value={email}
                                            onChange={(e) => setEmail(e.target.value)}
                                            className="pl-10"
                                            required
                                            disabled={loading}
                                        />
                                    </div>
                                </div>

                                {error && (
                                    <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-3">
                                        {error}
                                    </div>
                                )}
                            </CardContent>

                            <CardFooter className="flex flex-col gap-4">
                                <Button type="submit" className="w-full" disabled={loading}>
                                    {loading ? (
                                        <>
                                            <Loader2 className="h-4 w-4 animate-spin" />
                                            Sending code...
                                        </>
                                    ) : (
                                        <>
                                            Send Verification Code
                                            <ArrowRight className="h-4 w-4" />
                                        </>
                                    )}
                                </Button>

                                <p className="text-sm text-gray-500 text-center">
                                    New to Talky.ai?{" "}
                                    <Link
                                        href="/auth/register"
                                        className="text-gray-900 font-medium hover:underline"
                                    >
                                        Create an account
                                    </Link>
                                </p>
                            </CardFooter>
                        </form>
                    ) : (
                        // Step 2: OTP input
                        <form onSubmit={handleOtpSubmit}>
                            <CardContent className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="otp">Verification Code</Label>
                                    <div className="relative">
                                        <KeyRound className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
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
                                        />
                                    </div>
                                    <p className="text-xs text-gray-500 text-center">
                                        Check your email for the verification code
                                    </p>
                                </div>

                                {error && (
                                    <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-3">
                                        {error}
                                    </div>
                                )}

                                {message && (
                                    <div className="text-sm text-green-700 bg-green-50 border border-green-200 rounded-md p-3">
                                        {message}
                                    </div>
                                )}
                            </CardContent>

                            <CardFooter className="flex flex-col gap-4">
                                <Button type="submit" className="w-full" disabled={loading || otpCode.length < 6}>
                                    {loading ? (
                                        <>
                                            <Loader2 className="h-4 w-4 animate-spin" />
                                            Verifying...
                                        </>
                                    ) : (
                                        <>
                                            Verify & Sign In
                                            <ArrowRight className="h-4 w-4" />
                                        </>
                                    )}
                                </Button>

                                <div className="flex items-center justify-between w-full">
                                    <button
                                        type="button"
                                        onClick={handleBack}
                                        className="text-sm text-gray-500 hover:text-gray-700 flex items-center gap-1"
                                        disabled={loading}
                                    >
                                        <ArrowLeft className="h-3 w-3" />
                                        Change email
                                    </button>
                                    <button
                                        type="button"
                                        onClick={handleResend}
                                        className="text-sm text-gray-900 font-medium hover:underline"
                                        disabled={loading}
                                    >
                                        Resend code
                                    </button>
                                </div>
                            </CardFooter>
                        </form>
                    )}
                </Card>

                <p className="text-xs text-gray-400 text-center mt-8">
                    By continuing, you agree to our Terms of Service and Privacy Policy.
                </p>
            </div>
        </div>
    );
}
