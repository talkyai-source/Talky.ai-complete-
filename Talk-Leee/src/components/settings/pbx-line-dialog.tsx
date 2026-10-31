"use client";

import { useRef, useState, type ReactNode } from "react";
import { ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { useCreateSipTrunk, useUpdateSipTrunk, type SipTrunkRow } from "@/lib/telephony-api";
import { describeAddress, parseProviderAddress, resolveAddress } from "@/components/settings/pbx-address";
import {
    buildCreateInput,
    buildUpdatePatch,
    checkLineForm,
    emptyLineForm,
    hasAdvancedValues,
    lineFormFromTrunk,
    portFromField,
    type DtmfMode,
    type LineForm,
} from "@/components/settings/pbx-line-form";

/** What the dialog reports once the server has saved the line. */
export interface SavedLine {
    /** The saved line's id; null only if the server did not return one. */
    id: string | null;
    name: string;
    mode: "create" | "edit";
}

function SwitchRow({
    title,
    hint,
    checked,
    onChange,
    disabled,
}: {
    title: string;
    hint?: ReactNode;
    checked: boolean;
    onChange: (next: boolean) => void;
    disabled?: boolean;
}) {
    return (
        <label className={cn("flex items-start justify-between gap-3 rounded-lg border border-border bg-muted/20 p-3", disabled ? "cursor-not-allowed opacity-70" : "cursor-pointer")}>
            <span className="min-w-0">
                <span className="block text-sm font-medium text-foreground">{title}</span>
                {hint ? <span className="mt-0.5 block text-xs text-muted-foreground">{hint}</span> : null}
            </span>
            <Switch checked={checked} onCheckedChange={onChange} ariaLabel={title} disabled={disabled} />
        </label>
    );
}

function Hint({ children, id }: { children: ReactNode; id?: string }) {
    return <p id={id} className="text-xs text-muted-foreground">{children}</p>;
}

/**
 * "Connect your PBX": one short form for adding or editing a line. The address
 * box takes whatever the provider sent (name, name:port, IP or a sip: address)
 * and the connection details are worked out from it. Mount it with a fresh
 * `key` each time it opens so the form starts from the right line.
 */
export function PbxLineDialog({
    open,
    mode,
    trunk,
    onOpenChange,
    onSaved,
}: {
    open: boolean;
    mode: "create" | "edit";
    /** The saved line when editing; null when connecting a new one. */
    trunk: SipTrunkRow | null;
    onOpenChange: (next: boolean) => void;
    onSaved: (saved: SavedLine) => void;
}) {
    const createMutation = useCreateSipTrunk();
    const updateMutation = useUpdateSipTrunk();
    const addressRef = useRef<HTMLInputElement>(null);
    const [form, setForm] = useState<LineForm>(() => (trunk ? lineFormFromTrunk(trunk) : emptyLineForm()));
    const [showAdvanced, setShowAdvanced] = useState<boolean>(() => (trunk ? hasAdvancedValues(lineFormFromTrunk(trunk)) : false));
    const [error, setError] = useState<string | null>(null);
    const saving = createMutation.isPending || updateMutation.isPending;
    const savedCredentials = mode === "edit" && Boolean(trunk?.auth_configured);

    function setField<K extends keyof LineForm>(key: K, value: LineForm[K]) {
        setForm((current) => ({ ...current, [key]: value }));
    }

    // What the address resolves to, shown under the field as the person types.
    const parsed = parseProviderAddress(form.address);
    const portOverride = portFromField(form.port);
    const preview = parsed.ok
        ? describeAddress(resolveAddress(parsed.value, {
            transport: form.transport === "auto" ? null : form.transport,
            port: portOverride === "invalid" ? null : portOverride,
        }))
        : null;
    const addressProblem = form.address.trim() && !parsed.ok ? parsed.reason : null;

    async function submit() {
        // Enter in a text box can submit while a save is in flight; one save at a time.
        if (saving) return;
        setError(null);
        const checked = checkLineForm(form, { mode, original: trunk });
        if (!checked.ok) {
            setError(checked.error);
            return;
        }
        try {
            if (mode === "create") {
                const created = await createMutation.mutateAsync(buildCreateInput(form, checked));
                onOpenChange(false);
                onSaved({ id: created?.id ?? null, name: checked.name, mode });
            } else {
                if (!trunk) return;
                await updateMutation.mutateAsync({ id: trunk.id, patch: buildUpdatePatch(form, checked, trunk) });
                onOpenChange(false);
                onSaved({ id: trunk.id, name: checked.name, mode });
            }
        } catch (e: unknown) {
            setError(e instanceof Error && e.message ? e.message : "Couldn't save this line. Please try again.");
        }
    }

    return (
        <Modal
            open={open}
            onOpenChange={onOpenChange}
            title={mode === "create" ? "Connect your PBX" : "Edit your PBX connection"}
            description="Enter the details your provider or PBX gave you. We'll test the connection when you save."
            initialFocusRef={addressRef}
            footer={
                <div className="space-y-3">
                    {/* Next to Save, not at the top of the scrolling form, so a refusal is seen wherever the person scrolled to. */}
                    {error ? (
                        <div role="alert" className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-400">
                            {error}
                        </div>
                    ) : null}
                    <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                        <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
                            Cancel
                        </Button>
                        <Button onClick={() => void submit()} disabled={saving}>
                            {saving ? <><Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden /> Saving…</> : "Save and test"}
                        </Button>
                    </div>
                </div>
            }
        >
            <form
                className="space-y-4"
                noValidate
                onSubmit={(event) => {
                    event.preventDefault();
                    void submit();
                }}
            >
                <div className="space-y-1.5">
                    <Label htmlFor="pbx-address">Provider address</Label>
                    <Input
                        id="pbx-address"
                        ref={addressRef}
                        value={form.address}
                        onChange={(e) => setField("address", e.target.value)}
                        placeholder="pbx.example.com"
                        autoComplete="off"
                        autoCapitalize="none"
                        spellCheck={false}
                        aria-invalid={addressProblem ? true : undefined}
                        aria-describedby="pbx-address-help"
                    />
                    <p
                        id="pbx-address-help"
                        className={cn("text-xs", addressProblem ? "text-red-700 dark:text-red-400" : "text-muted-foreground")}
                    >
                        {addressProblem ?? preview ?? "The address your provider gave you: a name or an IP address, with a port if they gave you one."}
                    </p>
                </div>

                <SwitchRow
                    title="My provider allows our server by IP address (no username or password)"
                    hint={form.ipAccess ? "Your provider needs to accept calls from our servers. No username or password is sent." : undefined}
                    checked={form.ipAccess}
                    onChange={(next) => setField("ipAccess", next)}
                />

                {form.ipAccess ? null : (
                    <div className="space-y-2">
                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                            <div className="space-y-1.5">
                                <Label htmlFor="pbx-username">Username</Label>
                                <Input
                                    id="pbx-username"
                                    value={form.username}
                                    onChange={(e) => setField("username", e.target.value)}
                                    autoComplete="off"
                                    autoCapitalize="none"
                                    spellCheck={false}
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="pbx-password">Password</Label>
                                <Input
                                    id="pbx-password"
                                    type="password"
                                    value={form.password}
                                    onChange={(e) => setField("password", e.target.value)}
                                    autoComplete="new-password"
                                    placeholder={savedCredentials ? "Leave blank to keep the saved password" : undefined}
                                />
                            </div>
                        </div>
                        {savedCredentials ? (
                            <Hint>Leave the password blank to keep the one that&apos;s saved. If you change the username, enter the password again.</Hint>
                        ) : null}
                    </div>
                )}

                <div className="space-y-1.5">
                    <Label htmlFor="pbx-caller-id">Number to show when calling</Label>
                    <Input
                        id="pbx-caller-id"
                        value={form.callerId}
                        onChange={(e) => setField("callerId", e.target.value)}
                        placeholder="Optional — the number your provider gave you"
                        autoComplete="off"
                        inputMode="tel"
                    />
                    <Hint>Optional. The number people see when your agent calls them, exactly as your provider gave it to you.</Hint>
                </div>

                <div className="space-y-1.5">
                    <Label htmlFor="pbx-name">Line name</Label>
                    <Input
                        id="pbx-name"
                        value={form.name}
                        onChange={(e) => setField("name", e.target.value)}
                        placeholder="Defaults to the provider address"
                        autoComplete="off"
                    />
                    <Hint>Optional. A short name to tell your lines apart.</Hint>
                </div>

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <SwitchRow
                        title="Make calls"
                        hint="Your AI agent can call out through this line."
                        checked={form.makeCalls}
                        onChange={(next) => setField("makeCalls", next)}
                    />
                    <SwitchRow
                        title="Receive calls"
                        hint="Your AI agent can answer calls that arrive on this line."
                        checked={form.receiveCalls}
                        onChange={(next) => setField("receiveCalls", next)}
                    />
                </div>

                <div className="border-t border-border pt-3">
                    <button
                        type="button"
                        onClick={() => setShowAdvanced((shown) => !shown)}
                        aria-expanded={showAdvanced}
                        aria-controls="pbx-advanced"
                        className="flex w-full items-center justify-between rounded-md py-1 text-sm font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                        <span>Advanced settings</span>
                        {showAdvanced ? <ChevronUp className="h-4 w-4" aria-hidden /> : <ChevronDown className="h-4 w-4" aria-hidden />}
                    </button>
                </div>

                {showAdvanced ? (
                    <div id="pbx-advanced" className="space-y-3 rounded-lg border border-border bg-muted/20 p-3">
                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                            <div className="space-y-1.5">
                                <Label htmlFor="pbx-transport">Transport</Label>
                                <Select
                                    id="pbx-transport"
                                    ariaLabel="Transport"
                                    value={form.transport}
                                    onChange={(next) => setField("transport", next as LineForm["transport"])}
                                >
                                    <option value="auto">Automatic</option>
                                    <option value="udp">UDP</option>
                                    <option value="tcp">TCP</option>
                                    <option value="tls">TLS</option>
                                </Select>
                                <Hint>Automatic uses UDP unless the address says otherwise.</Hint>
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="pbx-port">Port</Label>
                                <Input
                                    id="pbx-port"
                                    inputMode="numeric"
                                    value={form.port}
                                    onChange={(e) => setField("port", e.target.value)}
                                    placeholder="Automatic"
                                    autoComplete="off"
                                />
                                <Hint>Leave blank to use the port from the address, or the standard one.</Hint>
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="pbx-auth-realm">Auth ID / realm</Label>
                                <Input
                                    id="pbx-auth-realm"
                                    value={form.authRealm}
                                    onChange={(e) => setField("authRealm", e.target.value)}
                                    placeholder="(if different from username)"
                                    autoComplete="off"
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="pbx-outbound-proxy">Outbound proxy</Label>
                                <Input
                                    id="pbx-outbound-proxy"
                                    value={form.outboundProxy}
                                    onChange={(e) => setField("outboundProxy", e.target.value)}
                                    placeholder="proxy.example.com:5060"
                                    autoComplete="off"
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="pbx-dtmf">DTMF mode</Label>
                                <Select
                                    id="pbx-dtmf"
                                    ariaLabel="DTMF mode"
                                    value={form.dtmfMode}
                                    onChange={(next) => setField("dtmfMode", next as DtmfMode)}
                                >
                                    <option value="rfc2833">RFC 2833 (recommended)</option>
                                    <option value="sip-info">SIP INFO</option>
                                    <option value="inband">In-band</option>
                                    <option value="auto">Auto</option>
                                </Select>
                                <Hint>How keypad presses are sent to your provider.</Hint>
                            </div>
                        </div>

                        <SwitchRow
                            title="Encrypt call audio (SRTP)"
                            hint="Works with a TLS connection. Leave off unless your provider asks for it."
                            checked={form.srtp}
                            onChange={(next) => setField("srtp", next)}
                        />
                        <SwitchRow
                            title="Register with the provider"
                            hint={form.ipAccess
                                ? "Not used when your provider allows our server by IP address."
                                : "Keeps this line signed in so incoming calls can reach it. Most accounts with a username and password need this."}
                            checked={!form.ipAccess && form.register}
                            onChange={(next) => setField("register", next)}
                            disabled={form.ipAccess}
                        />
                        <div className="space-y-1.5">
                            <Label htmlFor="pbx-register-interval">Sign-in refresh (seconds)</Label>
                            <Input
                                id="pbx-register-interval"
                                inputMode="numeric"
                                value={form.registerInterval}
                                disabled={form.ipAccess || !form.register}
                                onChange={(e) => setField("registerInterval", e.target.value)}
                                autoComplete="off"
                            />
                            <Hint>How often to renew the sign-in. Between 60 and 86400.</Hint>
                        </div>
                    </div>
                ) : null}

                {/* Lets Enter submit from any text box; the visible Save button sits in the dialog footer. */}
                <button type="submit" hidden tabIndex={-1} aria-hidden />
            </form>
        </Modal>
    );
}
