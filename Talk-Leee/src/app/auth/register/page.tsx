"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Mail, Building2, User, Lock, ArrowRight, Loader2 } from "lucide-react";

export default function RegisterPage() {
    const router = useRouter();
    const searchParams = useSearchParams();
    const [formData, setFormData] = useState({
        email: "",
        password: "",
        confirmPassword: "",
        businessName: "",
        name: "",
    });
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const nameInputRef = useRef<HTMLInputElement | null>(null);
    const errorId = useId();

    useEffect(() => {
        const t = window.setTimeout(() => {
            nameInputRef.current?.focus();
        }, 0);
        return () => window.clearTimeout(t);
    }, []);

    const nextParam = searchParams.get("next");
    const redirectTarget =
        nextParam && nextParam.startsWith("/") && !nextParam.startsWith("//") ? nextParam : "/dashboard";

    function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
        setFormData((prev) => ({
            ...prev,
            [e.target.name]: e.target.value,
        }));
    }

    async function handleSubmit(e: React.FormEvent) {
        e.preventDefault();
        setError("");

        // Validate passwords match
        if (formData.password !== formData.confirmPassword) {
            setError("Passwords do not match.");
            return;
        }

        if (formData.password.length < 6) {
            setError("Password must be at least 6 characters.");
            return;
        }

        setLoading(true);

        try {
            const response = await api.register(
                formData.email,
                formData.password,
                formData.businessName,
                "basic",
                formData.name || undefined,
            );

            // Store the token — registration returns an access token directly
            api.setToken(response.access_token);

            // Redirect to requested protected page or dashboard by default
            router.push(redirectTarget);
        } catch (err) {
            if (err instanceof Error) {
                setError(err.message);
            } else if (typeof err === "object" && err !== null) {
                setError((err as { detail?: string }).detail || "Registration failed");
            } else {
                setError("Registration failed. Please try again.");
            }
        } finally {
            setLoading(false);
        }
    }

    return (
        <div className="min-h-screen bg-transparent flex items-center justify-center p-4">
            <div className="w-full max-w-md">
                {/* Logo */}
                <div className="text-center mb-8">
                    <Link href="/" className="inline-block">
                        <h1 className="text-3xl font-bold tracking-tight text-foreground">
                            Talk-Lee
                        </h1>
                        <p className="text-sm text-[#D2B48C] dark:text-cyan-400 mt-1">AI Voice Dialer</p>
                    </Link>
                </div>

                <Card>
                    <CardHeader className="text-center">
                        <CardTitle>Create your account</CardTitle>
                        <CardDescription>
                            Start automating your voice campaigns today
                        </CardDescription>
                    </CardHeader>

                    <form
                        onSubmit={handleSubmit}
                        aria-busy={loading}
                        aria-describedby={error ? errorId : undefined}
                    >
                        <CardContent className="space-y-4">
                            <div className="space-y-2">
                                <Label htmlFor="name">Your Name</Label>
                                <div className="relative">
                                    <User className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                                    <Input
                                        id="name"
                                        name="name"
                                        type="text"
                                        placeholder="John Doe"
                                        value={formData.name}
                                        onChange={handleChange}
                                        className="pl-10"
                                        disabled={loading}
                                        ref={nameInputRef}
                                        autoComplete="name"
                                        aria-invalid={error ? true : undefined}
                                        aria-describedby={error ? errorId : undefined}
                                    />
                                </div>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="businessName">Business Name</Label>
                                <div className="relative">
                                    <Building2 className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
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
                                        autoComplete="organization"
                                    />
                                </div>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="email">Work Email</Label>
                                <div className="relative">
                                    <Mail className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
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
                                        autoComplete="email"
                                    />
                                </div>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="password">Password</Label>
                                <div className="relative">
                                    <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                                    <Input
                                        id="password"
                                        name="password"
                                        type="password"
                                        placeholder="••••••••"
                                        value={formData.password}
                                        onChange={handleChange}
                                        className="pl-10"
                                        required
                                        disabled={loading}
                                        autoComplete="new-password"
                                        minLength={6}
                                    />
                                </div>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="confirmPassword">Confirm Password</Label>
                                <div className="relative">
                                    <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                                    <Input
                                        id="confirmPassword"
                                        name="confirmPassword"
                                        type="password"
                                        placeholder="••••••••"
                                        value={formData.confirmPassword}
                                        onChange={handleChange}
                                        className="pl-10"
                                        required
                                        disabled={loading}
                                        autoComplete="new-password"
                                        minLength={6}
                                    />
                                </div>
                            </div>

                            {error && (
                                <div id={errorId} role="alert" aria-live="assertive" className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-3">
                                    {error}
                                </div>
                            )}
                        </CardContent>

                        <CardFooter className="flex flex-col gap-4">
                            <Button type="submit" className="w-full" disabled={loading}>
                                {loading ? (
                                    <>
                                        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                                        Creating account...
                                    </>
                                ) : (
                                    <>
                                        Get Started
                                        <ArrowRight className="h-4 w-4" />
                                    </>
                                )}
                            </Button>

                            <p className="text-sm text-gray-500 text-center">
                                Already have an account?{" "}
                                <Link
                                    href="/auth/login"
                                    className="text-foreground font-medium hover:underline"
                                >
                                    Sign in
                                </Link>
                            </p>
                        </CardFooter>
                    </form>
                </Card>

                <p className="text-xs text-muted-foreground text-center mt-8">
                    By creating an account, you agree to our Terms of Service and Privacy Policy.
                </p>
            </div>
        </div>
    );
}
