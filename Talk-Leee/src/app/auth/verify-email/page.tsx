"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { CheckCircle2, Loader2, Mail } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { isApiClientError } from "@/lib/http-client";
import { failedReadMessage } from "@/components/states/page-states";

/**
 * The page the "verify your email" link opens (/auth/verify-email?token=...).
 * The token is read once and checked once: the check is kept in a ref, which
 * survives React's development double-mount, so the second mount waits for the
 * same answer instead of asking again (a used token would turn a shown success
 * into "already used"). Once the server has answered, the token is removed from
 * the address bar and the history entry, as on accept-invite; after a network
 * error it stays, so a reload can try again. The token is never logged or shown.
 */

type Phase =
    | { kind: "checking" }
    | { kind: "verified" }
    | { kind: "expired" }
    | { kind: "failed"; message: string }
    | { kind: "missing" };

const EXPIRED = "This link has expired or was already used.";

function tokenFromAddress() {
    return (new URLSearchParams(window.location.search).get("token") ?? "").trim();
}

function clearAddress() {
    if (window.location.search) window.history.replaceState(window.history.state, "", window.location.pathname);
}

async function verify(token: string): Promise<Phase> {
    try {
        await api.request<unknown>({ path: "/auth/verify-email", method: "GET", query: { token } });
        clearAddress();
        return { kind: "verified" };
    } catch (err) {
        if (isApiClientError(err) && err.status !== undefined && err.status >= 400 && err.status < 500) {
            clearAddress();
            return { kind: "expired" };
        }
        return { kind: "failed", message: failedReadMessage(err, "Something went wrong. Please try again.") };
    }
}

function Heading({ children }: { children: React.ReactNode }) {
    return (
        <CardTitle asChild>
            <h1>{children}</h1>
        </CardTitle>
    );
}

function ResendForm() {
    const [email, setEmail] = useState("");
    const [sending, setSending] = useState(false);
    const [answer, setAnswer] = useState("");
    const [error, setError] = useState("");

    async function send(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        const address = email.trim().toLowerCase();
        if (!address) {
            setError("Enter your email address.");
            return;
        }
        setSending(true);
        setError("");
        setAnswer("");
        try {
            const reply = await api.request<{ message?: string }>({ path: "/auth/verify-email/resend", method: "POST", body: { email: address } });
            setAnswer(reply?.message || "If that address needs verifying, we have sent a new email.");
        } catch (err) {
            if (isApiClientError(err) && err.status === 429) setError("Too many attempts. Wait a moment and try again.");
            else setError(failedReadMessage(err, "Something went wrong. Please try again."));
        } finally {
            setSending(false);
        }
    }

    return (
        <form onSubmit={send} noValidate aria-busy={sending}>
            <CardContent className="space-y-4">
                <div className="space-y-2">
                    <Label htmlFor="verify-email-address">Email</Label>
                    <div className="relative">
                        <Mail className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" aria-hidden />
                        <Input
                            id="verify-email-address"
                            type="email"
                            autoComplete="email"
                            placeholder="you@example.com"
                            value={email}
                            onChange={(event) => setEmail(event.target.value)}
                            className="pl-10"
                            disabled={sending}
                        />
                    </div>
                </div>
                {answer ? <p role="status" className="text-sm text-muted-foreground">{answer}</p> : null}
                {error ? <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p> : null}
            </CardContent>
            <CardFooter className="flex flex-col gap-3">
                <Button type="submit" className="w-full" disabled={sending}>
                    {sending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
                    Send a new link
                </Button>
                <Link href="/auth/login" className="text-sm text-muted-foreground hover:text-foreground py-2">Back to sign in</Link>
            </CardFooter>
        </form>
    );
}

export default function VerifyEmailPage() {
    // The one check for this page. Not read during render; only the effect uses it.
    const attempt = useRef<Promise<Phase> | null>(null);
    const [phase, setPhase] = useState<Phase>({ kind: "checking" });

    useEffect(() => {
        let live = true;
        if (!attempt.current) {
            const token = tokenFromAddress();
            attempt.current = token ? verify(token) : Promise.resolve<Phase>({ kind: "missing" });
        }
        void attempt.current.then((next) => { if (live) setPhase(next); });
        return () => { live = false; };
    }, []);

    return (
        <main className="flex min-h-screen items-center justify-center bg-background p-4">
            <Card className="w-full max-w-md">
                {phase.kind === "checking" ? (
                    <CardHeader>
                        <Heading>Checking your link</Heading>
                        <CardDescription role="status" className="flex items-center gap-2">
                            <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> One moment…
                        </CardDescription>
                    </CardHeader>
                ) : phase.kind === "verified" ? (
                    <>
                        <CardHeader className="text-center">
                            <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full bg-green-100 dark:bg-green-900/30">
                                <CheckCircle2 className="h-6 w-6 text-green-600 dark:text-green-400" aria-hidden />
                            </div>
                            <Heading>Email verified</Heading>
                            <CardDescription>Your email is verified. You can now sign in.</CardDescription>
                        </CardHeader>
                        <CardFooter>
                            <Button asChild className="w-full">
                                <Link href="/auth/login">Sign in</Link>
                            </Button>
                        </CardFooter>
                    </>
                ) : (
                    <>
                        <CardHeader>
                            <Heading>{phase.kind === "expired" ? "This link cannot be used" : phase.kind === "failed" ? "We could not check your link" : "Verify your email"}</Heading>
                            <CardDescription role={phase.kind === "missing" ? undefined : "alert"}>
                                {phase.kind === "expired"
                                    ? EXPIRED
                                    : phase.kind === "failed"
                                      ? phase.message
                                      : "Enter your email address and we will send you a new link."}
                            </CardDescription>
                            {phase.kind === "expired" ? <CardDescription>Enter your email address and we will send you a new link.</CardDescription> : null}
                        </CardHeader>
                        <ResendForm />
                    </>
                )}
            </Card>
        </main>
    );
}
