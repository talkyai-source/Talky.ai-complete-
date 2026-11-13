"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2, PhoneOff, Plus, Search, Trash2, Upload } from "lucide-react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { DefaultCountrySelect } from "@/components/contacts/default-country-select";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ErrorState } from "@/components/states/page-states";
import { useAuth } from "@/hooks/useAuth";
import { dncApi, DNC_LIST_LIMIT, type DncBulkResult, type DncEntry } from "@/lib/dnc-api";
import { isApiClientError } from "@/lib/http-client";
import { parsePhoneNumber, PHONE_HELP_TEXT, type CountrySelection } from "@/lib/phone-number";
import { formatTimestamp } from "@/lib/timestamps";

/**
 * QA F1-15 (QA-UI-151): the Do-Not-Call list had an API but no screen, so a
 * customer could not see who is on it. Everyone in the tenant can read it;
 * adding, importing and removing are for the roles the server admits.
 */

// backend require_admin, after normalize_role's aliases.
const DNC_ADMIN_ROLES = new Set(["tenant_admin", "partner_admin", "platform_admin", "admin", "owner", "super_admin"]);
const DNC_QUERY_KEY = ["dnc", "entries"] as const;

const SOURCE_LABELS: Record<string, string> = {
    caller_opt_out: "Caller asked to stop",
    manual_admin: "Added by your team",
    bulk_import: "Imported",
    ftc_national: "National Do-Not-Call registry",
    regulator_complaint: "Regulator complaint",
};

function sourceLabel(source: string) {
    return SOURCE_LABELS[source] ?? source;
}

function failureText(error: unknown, fallback: string) {
    if (isApiClientError(error) && error.status === 422) return "Not a valid phone number for the selected country.";
    if (isApiClientError(error) && error.status === 403) return "Your role cannot change the Do-Not-Call list.";
    return error instanceof Error ? error.message : fallback;
}

export default function DoNotCallPage() {
    const { user } = useAuth();
    const timeZone = user?.account_timezone;
    const canManage = DNC_ADMIN_ROLES.has(user?.role ?? "");
    const qc = useQueryClient();
    const entries = useQuery({ queryKey: DNC_QUERY_KEY, queryFn: () => dncApi.list(), retry: false });
    const refresh = () => qc.invalidateQueries({ queryKey: DNC_QUERY_KEY });

    const [search, setSearch] = useState("");
    const visible = useMemo(() => {
        const digits = search.replace(/\D/g, "");
        const list = entries.data ?? [];
        return digits ? list.filter((entry) => entry.normalized_number.replace(/\D/g, "").includes(digits)) : list;
    }, [entries.data, search]);

    // Add one number.
    const [number, setNumber] = useState("");
    const [country, setCountry] = useState<CountrySelection>("");
    const [reason, setReason] = useState("");
    const [adding, setAdding] = useState(false);
    const [addError, setAddError] = useState("");
    const [addNotice, setAddNotice] = useState("");
    async function addNumber(event: React.FormEvent) {
        event.preventDefault();
        setAddError("");
        setAddNotice("");
        const parsed = parsePhoneNumber(number, country);
        if (!parsed.ok) {
            setAddError(parsed.error);
            return;
        }
        setAdding(true);
        try {
            const entry = await dncApi.add({ e164: parsed.e164, defaultCountry: country, reason: reason.trim() });
            setAddNotice(`${entry.normalized_number} is on the Do-Not-Call list.`);
            setNumber("");
            setReason("");
            await refresh();
        } catch (error) {
            setAddError(failureText(error, "The number was not added."));
        } finally {
            setAdding(false);
        }
    }

    // Import many.
    const [bulkText, setBulkText] = useState("");
    const [bulkCountry, setBulkCountry] = useState<CountrySelection>("");
    const [importing, setImporting] = useState(false);
    const [bulkError, setBulkError] = useState("");
    const [bulkResult, setBulkResult] = useState<DncBulkResult | null>(null);
    async function importNumbers(event: React.FormEvent) {
        event.preventDefault();
        setBulkError("");
        setBulkResult(null);
        const numbers = bulkText.split(/[\r\n,;]+/).map((line) => line.trim()).filter(Boolean);
        if (numbers.length === 0) {
            setBulkError("Paste at least one number.");
            return;
        }
        setImporting(true);
        try {
            setBulkResult(await dncApi.bulkImport({ numbers, defaultCountry: bulkCountry }));
            setBulkText("");
            await refresh();
        } catch (error) {
            setBulkError(failureText(error, "The numbers were not imported."));
        } finally {
            setImporting(false);
        }
    }

    // Remove, after a confirmation that names the number.
    const [removing, setRemoving] = useState<DncEntry | null>(null);
    async function confirmRemove() {
        if (!removing) return;
        await dncApi.remove(removing.id);
        setRemoving(null);
        await refresh();
    }

    return (
        <DashboardLayout title="Do-Not-Call list" description="Numbers no campaign will call. Callers who ask to stop are added automatically.">
            <div className="mb-6">
                <Link href="/contacts" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
                    <ArrowLeft className="h-4 w-4" aria-hidden />
                    Back to contacts
                </Link>
            </div>

            <div className="space-y-6">
                {canManage ? (
                    <div className="grid gap-6 lg:grid-cols-2">
                        <form onSubmit={addNumber} className="content-card space-y-3" aria-labelledby="dnc-add-heading">
                            <h2 id="dnc-add-heading" className="text-base font-semibold text-foreground">Add a number</h2>
                            <div>
                                <Label htmlFor="dnc-number">Phone number</Label>
                                <Input id="dnc-number" value={number} onChange={(e) => setNumber(e.target.value)} className="mt-1" inputMode="tel" autoComplete="off" />
                                <p className="mt-1 text-xs text-muted-foreground">{PHONE_HELP_TEXT}</p>
                            </div>
                            <DefaultCountrySelect id="dnc-country" value={country} onChange={setCountry} disabled={adding} />
                            <div>
                                <Label htmlFor="dnc-reason">Reason (optional)</Label>
                                <Input id="dnc-reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={512} className="mt-1" />
                            </div>
                            {addError ? <p role="alert" className="text-sm text-destructive">{addError}</p> : null}
                            {addNotice ? <p role="status" className="text-sm text-emerald-700 dark:text-emerald-400">{addNotice}</p> : null}
                            <Button type="submit" disabled={adding || !number.trim()}>
                                {adding ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Plus className="h-4 w-4" aria-hidden />}
                                Add to list
                            </Button>
                        </form>

                        <form onSubmit={importNumbers} className="content-card space-y-3" aria-labelledby="dnc-import-heading">
                            <h2 id="dnc-import-heading" className="text-base font-semibold text-foreground">Import many</h2>
                            <div>
                                <Label htmlFor="dnc-bulk">Numbers, one per line</Label>
                                <textarea
                                    id="dnc-bulk"
                                    value={bulkText}
                                    onChange={(e) => setBulkText(e.target.value)}
                                    rows={5}
                                    className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground"
                                />
                            </div>
                            <DefaultCountrySelect id="dnc-bulk-country" value={bulkCountry} onChange={setBulkCountry} disabled={importing} />
                            {bulkError ? <p role="alert" className="text-sm text-destructive">{bulkError}</p> : null}
                            {bulkResult ? (
                                <div role="status" className="text-sm text-foreground">
                                    <p>{`Added ${bulkResult.accepted_count}, already listed ${bulkResult.skipped_count}, not valid ${bulkResult.invalid_count}.`}</p>
                                    {bulkResult.invalid.length > 0 ? (
                                        <p className="mt-1 break-all text-xs text-muted-foreground">Not valid: {bulkResult.invalid.slice(0, 20).join(", ")}{bulkResult.invalid.length > 20 ? "…" : ""}</p>
                                    ) : null}
                                </div>
                            ) : null}
                            <Button type="submit" variant="outline" disabled={importing || !bulkText.trim()}>
                                {importing ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Upload className="h-4 w-4" aria-hidden />}
                                Import numbers
                            </Button>
                        </form>
                    </div>
                ) : null}

                <section className="content-card" aria-labelledby="dnc-list-heading">
                    <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <h2 id="dnc-list-heading" className="text-base font-semibold text-foreground">Numbers on the list</h2>
                        <div className="relative w-full sm:max-w-xs">
                            <label htmlFor="dnc-search" className="sr-only">Search by number</label>
                            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
                            <Input id="dnc-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search by number…" className="pl-9" />
                        </div>
                    </div>

                    {entries.isLoading ? (
                        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
                            <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading the Do-Not-Call list…
                        </p>
                    ) : entries.isError ? (
                        <ErrorState
                            message="Could not load the Do-Not-Call list."
                            requestId={isApiClientError(entries.error) ? entries.error.requestId : undefined}
                            onRetry={() => { void entries.refetch(); }}
                            busy={entries.isFetching}
                        />
                    ) : (entries.data ?? []).length === 0 ? (
                        <div className="flex items-center gap-3 text-sm text-muted-foreground">
                            <PhoneOff className="h-5 w-5" aria-hidden />
                            No numbers on your Do-Not-Call list yet.
                        </div>
                    ) : (
                        <>
                            {(entries.data ?? []).length >= DNC_LIST_LIMIT ? (
                                <p className="mb-3 text-xs text-muted-foreground">Showing the newest {DNC_LIST_LIMIT} numbers. Search finds numbers among these.</p>
                            ) : null}
                            {visible.length === 0 ? (
                                <p className="text-sm text-muted-foreground">No number matches “{search}”.</p>
                            ) : (
                                <div className="overflow-x-auto">
                                    <table className="w-full text-sm">
                                        <thead>
                                            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                                                <th scope="col" className="px-3 py-2">Number</th>
                                                <th scope="col" className="px-3 py-2">Why</th>
                                                <th scope="col" className="px-3 py-2">Added</th>
                                                <th scope="col" className="px-3 py-2">Until</th>
                                                {canManage ? <th scope="col" className="px-3 py-2"><span className="sr-only">Actions</span></th> : null}
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {visible.map((entry) => (
                                                <tr key={entry.id} className="border-b border-border/60">
                                                    <td className="px-3 py-2 font-medium tabular-nums text-foreground">{entry.normalized_number}</td>
                                                    <td className="px-3 py-2 text-muted-foreground">
                                                        {sourceLabel(entry.source)}
                                                        {entry.reason ? <span className="block text-xs">{entry.reason}</span> : null}
                                                    </td>
                                                    <td className="px-3 py-2 text-muted-foreground">
                                                        {entry.created_at ? <time dateTime={entry.created_at} title={formatTimestamp(entry.created_at, timeZone).utc}>{formatTimestamp(entry.created_at, timeZone).label}</time> : "—"}
                                                    </td>
                                                    <td className="px-3 py-2 text-muted-foreground">
                                                        {entry.expires_at ? <time dateTime={entry.expires_at} title={formatTimestamp(entry.expires_at, timeZone).utc}>{formatTimestamp(entry.expires_at, timeZone).label}</time> : "No end date"}
                                                    </td>
                                                    {canManage ? (
                                                        <td className="px-3 py-2 text-right">
                                                            <Button type="button" variant="ghost" size="sm" onClick={() => setRemoving(entry)} aria-label={`Remove ${entry.normalized_number}`}>
                                                                <Trash2 className="h-4 w-4" aria-hidden />
                                                                Remove
                                                            </Button>
                                                        </td>
                                                    ) : null}
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </>
                    )}
                </section>
            </div>

            <ConfirmDialog
                open={removing !== null}
                onOpenChange={(open) => { if (!open) setRemoving(null); }}
                intent="delete"
                title="Remove from the Do-Not-Call list?"
                description={removing ? `Campaigns will be able to call ${removing.normalized_number} again.` : undefined}
                warningText={removing?.source === "caller_opt_out"
                    ? "This caller asked not to be called. Remove the number only if they have since asked to be called."
                    : "Remove the number only if calling it is allowed again."}
                confirmLabel="Remove"
                onConfirm={confirmRemove}
            />
        </DashboardLayout>
    );
}
