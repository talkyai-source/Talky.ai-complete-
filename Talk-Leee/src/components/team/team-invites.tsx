"use client";

import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RotateCw, Send, X } from "lucide-react";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ErrorState } from "@/components/states/page-states";
import { Select } from "@/components/ui/select";
import { isApiClientError } from "@/lib/http-client";
import { ASSIGNABLE_ROLES, roleName, teamApi, type AssignableRole, type TeamInvite } from "@/lib/team-api";
import { formatTimestamp } from "@/lib/timestamps";

/**
 * Inviting someone by email (QA F1-15, D4-2) on the Team page: the form, the
 * server's answer shown in place, and the invites still waiting, each with
 * Resend and Withdraw (Withdraw asks first). The Team page mounts this for
 * admins only, so nobody else ever asks the server for the invites.
 */

const INVITES_QUERY_KEY = ["team", "invites"] as const;
const INVITE_EMAIL_ID = "invite-email";
const emailSchema = z.string().email();

type Notice = { tone: "success" | "warning" | "error"; text: string };
const NOTICE_TONE: Record<Notice["tone"], string> = {
    success: "text-emerald-700 dark:text-emerald-400",
    warning: "text-amber-700 dark:text-amber-400",
    error: "text-destructive",
};

/** For the Team page's Invite button: bring the form into view and put the cursor in its Email field. */
export function focusInviteForm() {
    const field = document.getElementById(INVITE_EMAIL_ID);
    if (!(field instanceof HTMLElement)) return;
    field.scrollIntoView?.({ block: "center" });
    field.focus({ preventScroll: true });
}

export function TeamInvites({ timeZone }: { timeZone?: string }) {
    const qc = useQueryClient();
    const invites = useQuery({ queryKey: INVITES_QUERY_KEY, queryFn: () => teamApi.listInvites(), retry: false });
    const [withdrawing, setWithdrawing] = useState<TeamInvite | null>(null);
    const [inviteEmail, setInviteEmail] = useState("");
    const [inviteRole, setInviteRole] = useState<AssignableRole>("user");
    const [sending, setSending] = useState(false);
    const [notice, setNotice] = useState<Notice | null>(null);

    async function confirmWithdraw() {
        if (!withdrawing) return;
        await teamApi.withdrawInvite(withdrawing.id);
        setWithdrawing(null);
        await qc.invalidateQueries({ queryKey: INVITES_QUERY_KEY });
    }

    async function sendInvite(email: string, role: AssignableRole) {
        setSending(true);
        setNotice(null);
        try {
            const sent = await teamApi.invite(email, role);
            setNotice(sent.email_sent
                ? { tone: "success", text: `Invite sent to ${sent.email}.` }
                : { tone: "warning", text: `The invite to ${sent.email} was saved, but the email did not go out. Use Resend to try again.` });
            return true;
        } catch (err) {
            setNotice({ tone: "error", text: isApiClientError(err) ? err.message : "Could not send the invite. Try again." });
            return false;
        } finally {
            setSending(false);
            await qc.invalidateQueries({ queryKey: INVITES_QUERY_KEY });
        }
    }

    async function onInvite(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        const email = inviteEmail.trim().toLowerCase();
        if (!emailSchema.safeParse(email).success) {
            setNotice({ tone: "error", text: "Enter a valid email address." });
            return;
        }
        if (await sendInvite(email, inviteRole)) setInviteEmail("");
    }

    return (
        <>
            <Card>
                <CardHeader>
                    <CardTitle>Invite someone</CardTitle>
                </CardHeader>
                <CardContent>
                    <form className="flex flex-col gap-3 sm:flex-row sm:items-end" onSubmit={onInvite} noValidate>
                        <div className="flex-1 space-y-1.5">
                            <Label htmlFor={INVITE_EMAIL_ID}>Email</Label>
                            <Input
                                id={INVITE_EMAIL_ID}
                                type="email"
                                autoComplete="off"
                                value={inviteEmail}
                                onChange={(event) => setInviteEmail(event.target.value)}
                                placeholder="name@company.com"
                            />
                        </div>
                        <Select ariaLabel="Role for the new person" value={inviteRole} onChange={(next) => setInviteRole(next as AssignableRole)} className="w-44">
                            {ASSIGNABLE_ROLES.map((role) => (
                                <option key={role} value={role}>{roleName(role)}</option>
                            ))}
                        </Select>
                        <Button type="submit" disabled={sending}>
                            {sending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Send className="h-4 w-4" aria-hidden />}
                            Send invite
                        </Button>
                    </form>
                    {notice ? (
                        <p role={notice.tone === "error" ? "alert" : "status"} className={`mt-3 text-sm ${NOTICE_TONE[notice.tone]}`}>{notice.text}</p>
                    ) : null}
                    <p className="mt-3 text-xs text-muted-foreground">The link works for 7 days and only once. Someone who already has an account cannot be invited.</p>

                    <h3 className="mb-2 mt-6 text-sm font-semibold text-foreground">Waiting to join</h3>
                    {invites.isLoading ? (
                        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
                            <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading invites…
                        </p>
                    ) : invites.isError ? (
                        <ErrorState
                            message="Could not load the invites."
                            requestId={isApiClientError(invites.error) ? invites.error.requestId : undefined}
                            onRetry={() => { void invites.refetch(); }}
                            busy={invites.isFetching}
                        />
                    ) : (invites.data ?? []).length === 0 ? (
                        <p className="text-sm text-muted-foreground">No open invites.</p>
                    ) : (
                        <ul className="divide-y divide-border">
                            {(invites.data ?? []).map((invite) => (
                                <li key={invite.id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
                                    <div className="min-w-0">
                                        <p className="truncate font-medium text-foreground">{invite.email}</p>
                                        <p className="text-sm text-muted-foreground">
                                            {roleName(invite.role)} · link works until{" "}
                                            <time dateTime={invite.expires_at}>{formatTimestamp(invite.expires_at, timeZone).date}</time>
                                        </p>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="sm"
                                            disabled={sending}
                                            onClick={() => { void sendInvite(invite.email, invite.role as AssignableRole); }}
                                            aria-label={`Resend the invite to ${invite.email}`}
                                        >
                                            <RotateCw className="h-4 w-4" aria-hidden />
                                            Resend
                                        </Button>
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="sm"
                                            onClick={() => setWithdrawing(invite)}
                                            aria-label={`Withdraw the invite to ${invite.email}`}
                                        >
                                            <X className="h-4 w-4" aria-hidden />
                                            Withdraw
                                        </Button>
                                    </div>
                                </li>
                            ))}
                        </ul>
                    )}
                </CardContent>
            </Card>

            <ConfirmDialog
                open={withdrawing !== null}
                onOpenChange={(open) => { if (!open) setWithdrawing(null); }}
                intent="delete"
                title="Withdraw this invite?"
                description={withdrawing ? `The invite link sent to ${withdrawing.email} will stop working.` : undefined}
                warningText="You can invite them again later."
                confirmLabel="Withdraw"
                onConfirm={confirmWithdraw}
            />
        </>
    );
}
