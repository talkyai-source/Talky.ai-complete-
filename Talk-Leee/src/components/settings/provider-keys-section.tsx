"use client";

import { useEffect, useId, useRef, useState } from "react";
import { z } from "zod";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";

const path = "/tenant-ai-credentials/";
const providers = [
    ["groq", "Groq"], ["gemini", "Gemini"], ["cerebras", "Cerebras"],
    ["deepgram", "Deepgram"], ["assemblyai", "AssemblyAI"], ["cartesia", "Cartesia"],
    ["elevenlabs", "ElevenLabs"], ["openai", "OpenAI"], ["deepseek", "DeepSeek"],
    ["anthropic", "Anthropic"], ["xai", "xAI"],
] as const;

const credentialSchema = z.object({
    id: z.string().uuid(), tenant_id: z.string().uuid(), provider: z.string().max(64),
    credential_kind: z.string(), last4: z.string().max(8).nullable(), label: z.string().nullable(),
    status: z.enum(["active", "disabled"]),
    validation_status: z.enum(["valid", "invalid"]).nullish().transform(value => value ?? null),
    last_checked_at: z.string().datetime({ offset: true }).nullish().transform(value => value ?? null),
}).refine(value => (value.validation_status === null) === (value.last_checked_at === null));
type Credential = z.infer<typeof credentialSchema>;
type State = { scope: string; rows: Credential[]; provider: string; key: string; label: string;
    loading: boolean; busy: boolean; error: string | null };
function initial(scope: string): State {
    return { scope, rows: [], provider: "groq", key: "", label: "", loading: true, busy: false, error: null };
}

export function ProviderKeysSection({ tenantId, userId, canEdit }: {
    tenantId?: string | null; userId?: string | null; canEdit: boolean;
}) {
    const scope = `${userId ?? ""}:${tenantId ?? ""}`;
    const [state, setState] = useState(() => initial(scope));
    const owner = useRef<{ scope: string; controller: AbortController } | null>(null);
    const writeLock = useRef<symbol | null>(null);
    const fieldId = useId();
    // A changed identity never renders an earlier account's rows or draft,
    // including the render before the effect disposes its outstanding requests.
    const shown = state.scope === scope ? state : initial(scope);

    useEffect(() => {
        const current = { scope, controller: new AbortController() };
        owner.current = current;
        writeLock.current = null;
        // eslint-disable-next-line react-hooks/set-state-in-effect -- clear private account state at the identity boundary
        setState(initial(scope));
        if (tenantId && userId) {
            void api.request<unknown>({ path, signal: current.controller.signal,
                ownsOperation: () => owner.current === current }).then(value => {
                const rows = z.array(credentialSchema).parse(value);
                if (rows.some(row => row.tenant_id !== tenantId)) throw new Error("Credential ownership could not be verified.");
                if (owner.current === current) setState(previous => ({ ...previous, rows, loading: false }));
            }).catch(() => {
                if (owner.current === current) setState(previous => ({ ...previous, loading: false, error: "Could not load provider keys." }));
            });
        }
        return () => {
            current.controller.abort();
            if (owner.current === current) owner.current = null;
        };
    }, [scope, tenantId, userId]);

    function draft(field: "provider" | "key" | "label", value: string) {
        setState(previous => ({ ...(previous.scope === scope ? previous : initial(scope)), [field]: value }));
    }

    async function mutate(operation: "save" | "check" | "disable", credential?: Credential) {
        const current = owner.current;
        if (!canEdit || !tenantId || !userId || !current || current.scope !== scope || shown.loading || writeLock.current) return;
        if (operation === "save" && (shown.key.length < 8 || shown.key.length > 8192)) return;
        if (operation !== "save" && !credential) return;
        const lock = Symbol("credential-write");
        writeLock.current = lock;
        const submittedKey = shown.key;
        setState(previous => ({ ...previous, busy: true, error: null }));
        try {
            const value = await api.request<unknown>({
                path: operation === "save" ? path : path + credential!.id + (operation === "check" ? "/check" : ""),
                method: operation === "disable" ? "DELETE" : "POST",
                ...(operation === "save" ? { body: { provider: shown.provider, api_key: submittedKey,
                    credential_kind: "api_key", label: shown.label.trim() || null } } : {}),
                signal: current.controller.signal, ownsOperation: () => owner.current === current,
            });
            if (owner.current !== current) return;
            if (operation === "disable") {
                setState(previous => ({ ...previous, rows: previous.rows.map(row =>
                    row.id === credential!.id ? { ...row, status: "disabled" } : row) }));
            } else {
                const saved = credentialSchema.parse(value);
                if (saved.tenant_id !== tenantId) throw new Error("Credential ownership could not be verified.");
                setState(previous => {
                    const rows = previous.rows.filter(row => row.id !== saved.id).map(row =>
                        operation === "save" && row.provider === saved.provider && row.credential_kind === saved.credential_kind
                            ? { ...row, status: "disabled" as const } : row);
                    return { ...previous, rows: [saved, ...rows], ...(operation === "save" ? { label: "" } : {}) };
                });
            }
        } catch (error) {
            if (owner.current === current) {
                const message = error instanceof Error ? error.message : "Provider key operation failed.";
                setState(previous => ({ ...previous, error: submittedKey ? message.split(submittedKey).join("[redacted]") : message }));
            }
        } finally {
            if (writeLock.current === lock) writeLock.current = null;
            if (owner.current === current) setState(previous => ({ ...previous, busy: false,
                ...(operation === "save" ? { key: "" } : {}) }));
        }
    }

    return <section className="rounded-2xl border border-border bg-card p-5 space-y-4" aria-labelledby={`${fieldId}-title`}>
        <div>
            <h3 id={`${fieldId}-title`} className="text-base font-semibold">Provider API keys</h3>
            <p className="text-sm text-muted-foreground">Use your tenant&apos;s own provider keys. Saving a key does not change the selected AI provider.</p>
            <p className="mt-1 text-xs text-muted-foreground">A successful credential check does not verify call readiness, available credit or generation permissions.</p>
        </div>
        {!tenantId || !userId ? <p>Sign in with a tenant to manage provider keys.</p> : <>
            {shown.error && <p role="alert" className="text-sm text-destructive">{shown.error}</p>}
            {shown.loading ? <p className="text-sm text-muted-foreground">Loading keys…</p> :
                shown.rows.length === 0 ? <p className="text-sm text-muted-foreground">No saved provider keys.</p> :
                    <ul className="space-y-3">{shown.rows.map(row => <li key={row.id} className="rounded-lg border border-border p-3 text-sm">
                        <div className="flex flex-wrap items-center gap-2">
                            <span className="font-medium">{row.label || row.provider}</span>
                            <span className="text-muted-foreground">{row.provider} · ••••{row.last4 ?? ""}</span>
                            <span>{row.status === "active" ? "Active" : "Disabled"}</span>
                            <span>{row.validation_status === "valid" ? "Valid" : row.validation_status === "invalid" ? "Invalid" : "Not checked"}</span>
                        </div>
                        {row.last_checked_at && <p className="mt-1 text-xs text-muted-foreground">Last checked: <time dateTime={row.last_checked_at}>{new Date(row.last_checked_at).toLocaleString()}</time></p>}
                        {canEdit && <div className="mt-2 flex gap-2">
                            <Button type="button" variant="outline" size="sm" disabled={shown.busy} onClick={() => void mutate("check", row)}>Check key</Button>
                            {row.status === "active" && <Button type="button" variant="outline" size="sm" disabled={shown.busy} onClick={() => void mutate("disable", row)}>Disable key</Button>}
                        </div>}
                    </li>)}</ul>}
            {canEdit ? <form className="grid gap-3 sm:grid-cols-2" onSubmit={event => { event.preventDefault(); void mutate("save"); }}>
                <div className="space-y-1"><Label htmlFor={`${fieldId}-provider`}>Provider</Label>
                    <Select id={`${fieldId}-provider`} ariaLabel="Provider" value={shown.provider} onChange={value => draft("provider", value)} disabled={shown.busy}>
                        {providers.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </Select></div>
                <div className="space-y-1"><Label htmlFor={`${fieldId}-label`}>Label</Label>
                    <Input id={`${fieldId}-label`} value={shown.label} maxLength={128} disabled={shown.busy} onChange={event => draft("label", event.target.value)} /></div>
                <div className="space-y-1 sm:col-span-2"><Label htmlFor={`${fieldId}-key`}>API key</Label>
                    <Input id={`${fieldId}-key`} type="password" autoComplete="new-password" value={shown.key} minLength={8} maxLength={8192} required disabled={shown.busy} onChange={event => draft("key", event.target.value)} />
                    <p className="text-xs text-muted-foreground">The provider is checked before this replaces an active key. The full key is never shown again.</p></div>
                <div className="sm:col-span-2"><Button type="submit" disabled={shown.loading || shown.busy || shown.key.length < 8}>Validate and save</Button></div>
            </form> : <p className="text-xs text-muted-foreground">Only administrators can save, check or disable provider keys.</p>}
        </>}
    </section>;
}
