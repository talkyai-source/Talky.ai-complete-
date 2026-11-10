"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Loader2, UserPlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { isApiClientError } from "@/lib/http-client";
import { MIN_PASSWORD_LENGTH } from "@/lib/password-policy";
import { inviteApi, roleName, type InvitePreview } from "@/lib/team-api";

/**
 * QA F1-15 / D4-2: where an invited person joins a workspace. The token sits
 * in the link's fragment (#token=...), which browsers never send to a server.
 * It is read once, removed from the address bar and the history entry, and
 * travels only in request bodies.
 */

type Phase =
    | { kind: "checking" }
    | { kind: "missing" }
    | { kind: "invalid" }
    | { kind: "form"; invite: InvitePreview }
    | { kind: "done"; email: string };

const NO_LONGER_VALID = "This invite link is no longer valid. Ask the person who invited you for a new one.";

function tokenFromAddress() {
    return (new URLSearchParams(window.location.hash.replace(/^#/, "")).get("token") ?? "").trim();
}

function failureMessage(err: unknown) {
    return isApiClientError(err) ? err.message : "Something went wrong. Please try again.";
}

function Heading({ children }: { children: React.ReactNode }) {
    return (
        <CardTitle asChild>
            <h1>{children}</h1>
        </CardTitle>
    );
}

export default function AcceptInvitePage() {
    // Kept across React's development double-mount, after the address is cleared.
    const token = useRef("");
    const [phase, setPhase] = useState<Phase>({ kind: "checking" });
    const [name, setName] = useState("");
    const [password, setPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");
    const [error, setError] = useState("");
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        let live = true;
        async function check() {
            token.current = token.current || tokenFromAddress();
            if (window.location.hash) window.history.replaceState(window.history.state, "", window.location.pathname);
            if (!token.current) {
                await Promise.resolve();
                if (live) setPhase({ kind: "missing" });
                return;
            }
            try {
                const invite = await inviteApi.preview(token.current);
                if (live) setPhase({ kind: "form", invite });
            } catch (err) {
                if (!live) return;
                if (isApiClientError(err) && err.status === 410) setPhase({ kind: "invalid" });
                else setError(failureMessage(err));
            }
        }
        void check();
        return () => { live = false; };
    }, []);

    async function join(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        setError("");
        if (!name.trim()) {
            setError("Enter your name.");
            return;
        }
        if (password.length < MIN_PASSWORD_LENGTH) {
            setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
            return;
        }
        if (password !== confirmPassword) {
            setError("Passwords do not match.");
            return;
        }
        setSubmitting(true);
        try {
            const joined = await inviteApi.accept({ token: token.current, name: name.trim(), password, confirmPassword });
            setPhase({ kind: "done", email: joined.email });
        } catch (err) {
            if (isApiClientError(err) && err.status === 410) setPhase({ kind: "invalid" });
            else setError(failureMessage(err));
        } finally {
            setSubmitting(false);
        }
    }

    return (
        <main className="flex min-h-screen items-center justify-center bg-background p-4">
            <Card className="w-full max-w-md">
                {phase.kind === "checking" ? (
                    <CardHeader>
                        <Heading>Checking your invite</Heading>
                        <CardDescription role="status" className="flex items-center gap-2">
                            {error ? error : (<><Loader2 className="h-4 w-4 animate-spin" aria-hidden /> One moment…</>)}
                        </CardDescription>
                    </CardHeader>
                ) : phase.kind === "missing" ? (
                    <CardHeader>
                        <Heading>This link is incomplete</Heading>
                        <CardDescription>Open the invite link from your email again. If it still does not work, ask the person who invited you for a new one.</CardDescription>
                    </CardHeader>
                ) : phase.kind === "invalid" ? (
                    <CardHeader>
                        <Heading>This invite cannot be used</Heading>
                        <CardDescription>{NO_LONGER_VALID}</CardDescription>
                    </CardHeader>
                ) : phase.kind === "done" ? (
                    <>
                        <CardHeader>
                            <Heading>You&apos;re in</Heading>
                            <CardDescription>Your account for {phase.email} is ready. Sign in to continue.</CardDescription>
                        </CardHeader>
                        <CardFooter>
                            <Button asChild className="w-full">
                                <Link href="/auth/login">Sign in</Link>
                            </Button>
                        </CardFooter>
                    </>
                ) : (
                    <form onSubmit={join} noValidate>
                        <CardHeader>
                            <Heading>Join {phase.invite.workspace_name || "your team"}</Heading>
                            <CardDescription>
                                You were invited as {roleName(phase.invite.role)}. You will sign in as {phase.invite.email}.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="space-y-1.5">
                                <Label htmlFor="invite-name">Your name</Label>
                                <Input id="invite-name" autoComplete="name" value={name} onChange={(event) => setName(event.target.value)} />
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="invite-password">Password</Label>
                                <Input id="invite-password" type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} />
                                <p className="text-xs text-muted-foreground">At least {MIN_PASSWORD_LENGTH} characters.</p>
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="invite-confirm">Confirm password</Label>
                                <Input id="invite-confirm" type="password" autoComplete="new-password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} />
                            </div>
                            {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
                            <p className="text-xs text-muted-foreground">
                                By joining, you agree to our{" "}
                                <Link href="/terms" className="underline hover:text-foreground">Terms of Service</Link>
                                {" "}and{" "}
                                <Link href="/privacy" className="underline hover:text-foreground">Privacy Policy</Link>.
                            </p>
                        </CardContent>
                        <CardFooter>
                            <Button type="submit" className="w-full" disabled={submitting}>
                                {submitting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <UserPlus className="h-4 w-4" aria-hidden />}
                                Join the workspace
                            </Button>
                        </CardFooter>
                    </form>
                )}
            </Card>
        </main>
    );
}
