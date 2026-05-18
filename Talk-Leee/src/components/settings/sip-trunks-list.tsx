"use client";

import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Modal } from "@/components/ui/modal";
import {
    Plus,
    Power,
    PowerOff,
    Trash2,
    Loader2,
    ServerCog,
    Activity,
    Pencil,
    CheckCircle2,
    XCircle,
    AlertCircle,
} from "lucide-react";
import {
    useSipTrunks,
    useCreateSipTrunk,
    useUpdateSipTrunk,
    useTestSipTrunk,
    useActivateSipTrunk,
    useDeactivateSipTrunk,
    useDeleteSipTrunk,
    type SipTrunkInput,
    type SipTrunkRow,
} from "@/lib/telephony-api";
import { notificationsStore } from "@/lib/notifications";

const EMPTY_FORM: SipTrunkInput = {
    trunk_name: "",
    sip_domain: "",
    port: 5060,
    transport: "udp",
    direction: "both",
    auth_username: "",
    auth_password: "",
};

type ModalMode = "create" | "edit";

function TestStatusBadge({ trunk }: { trunk: SipTrunkRow }) {
    if (!trunk.last_test_result || !trunk.last_tested_at) {
        return (
            <span
                title="Click Test to verify reachability"
                className="inline-flex items-center gap-1 rounded-full border border-gray-500/30 bg-gray-500/10 px-2 py-0.5 text-xs font-semibold text-gray-700 dark:text-gray-400"
            >
                <AlertCircle className="h-3 w-3" aria-hidden /> Untested
            </span>
        );
    }
    const ok = trunk.last_test_result.ok;
    return (
        <span
            title={`${ok ? "OK" : trunk.last_test_result.error || "Failed"} · ${new Date(trunk.last_tested_at).toLocaleString()}`}
            className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold ${ok
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                : "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400"
                }`}
        >
            {ok ? (
                <><CheckCircle2 className="h-3 w-3" aria-hidden /> Reachable</>
            ) : (
                <><XCircle className="h-3 w-3" aria-hidden /> Unreachable</>
            )}
        </span>
    );
}

export function SipTrunksList() {
    const trunksQuery = useSipTrunks();
    const createMutation = useCreateSipTrunk();
    const updateMutation = useUpdateSipTrunk();
    const testMutation = useTestSipTrunk();
    const activateMutation = useActivateSipTrunk();
    const deactivateMutation = useDeactivateSipTrunk();
    const deleteMutation = useDeleteSipTrunk();

    const [isOpen, setIsOpen] = useState(false);
    const [mode, setMode] = useState<ModalMode>("create");
    const [editingId, setEditingId] = useState<string | null>(null);
    const [form, setForm] = useState<SipTrunkInput>(EMPTY_FORM);
    const [formError, setFormError] = useState<string | null>(null);
    const [clearAuth, setClearAuth] = useState(false);
    const [testingId, setTestingId] = useState<string | null>(null);

    const trunks: SipTrunkRow[] = trunksQuery.data ?? [];

    function openCreate() {
        setMode("create");
        setEditingId(null);
        setForm(EMPTY_FORM);
        setFormError(null);
        setClearAuth(false);
        setIsOpen(true);
    }

    function openEdit(t: SipTrunkRow) {
        setMode("edit");
        setEditingId(t.id);
        setForm({
            trunk_name: t.trunk_name,
            sip_domain: t.sip_domain,
            port: t.port,
            transport: t.transport,
            direction: t.direction,
            auth_username: t.auth_username || "",
            auth_password: "", // never returned from backend; user must re-enter to overwrite
        });
        setFormError(null);
        setClearAuth(false);
        setIsOpen(true);
    }

    async function handleSubmit() {
        setFormError(null);
        if (!form.trunk_name.trim() || !form.sip_domain.trim()) {
            setFormError("Trunk name and SIP domain are required.");
            return;
        }
        if (form.port < 1 || form.port > 65535) {
            setFormError("Port must be between 1 and 65535.");
            return;
        }

        try {
            if (mode === "create") {
                const payload: SipTrunkInput = { ...form };
                if (!payload.auth_username && !payload.auth_password) {
                    delete payload.auth_username;
                    delete payload.auth_password;
                } else if (!payload.auth_username || !payload.auth_password) {
                    setFormError("Provide both auth username and password, or leave both blank.");
                    return;
                }
                await createMutation.mutateAsync(payload);
                notificationsStore.create({
                    type: "success",
                    title: "SIP trunk added",
                    message: `${form.trunk_name} is saved (inactive). Click Test to verify reachability before activating.`,
                });
            } else {
                if (!editingId) return;
                const patch: Partial<SipTrunkInput> & { clear_auth?: boolean } = {
                    trunk_name: form.trunk_name,
                    sip_domain: form.sip_domain,
                    port: form.port,
                    transport: form.transport,
                    direction: form.direction,
                };
                if (clearAuth) {
                    patch.clear_auth = true;
                } else if (form.auth_username && form.auth_password) {
                    patch.auth_username = form.auth_username;
                    patch.auth_password = form.auth_password;
                } else if (form.auth_username && !form.auth_password) {
                    setFormError("Re-enter the password to overwrite, or toggle Clear Auth.");
                    return;
                }
                await updateMutation.mutateAsync({ id: editingId, patch });
                notificationsStore.create({
                    type: "success",
                    title: "SIP trunk updated",
                    message: form.trunk_name,
                });
            }
            setIsOpen(false);
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : "Save failed";
            setFormError(msg);
        }
    }

    async function handleTest(t: SipTrunkRow) {
        setTestingId(t.id);
        try {
            const r = await testMutation.mutateAsync(t.id);
            if (r.ok) {
                notificationsStore.create({
                    type: "success",
                    title: `${t.trunk_name} is reachable`,
                    message: `${r.latency_ms ?? 0} ms · ${r.detail || `${t.transport.toUpperCase()} ${r.target}`}`,
                });
            } else {
                notificationsStore.create({
                    type: "error",
                    title: `${t.trunk_name} unreachable`,
                    message: r.detail || r.error || "Probe failed",
                });
            }
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : "Test failed";
            notificationsStore.create({ type: "error", title: "Test failed", message: msg });
        } finally {
            setTestingId(null);
        }
    }

    async function handleToggle(t: SipTrunkRow) {
        try {
            if (t.is_active) {
                await deactivateMutation.mutateAsync(t.id);
                notificationsStore.create({
                    type: "success",
                    title: "Trunk deactivated",
                    message: t.trunk_name,
                });
            } else {
                await activateMutation.mutateAsync(t.id);
                notificationsStore.create({
                    type: "success",
                    title: "Trunk activated",
                    message: t.trunk_name,
                });
            }
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : "Operation failed";
            notificationsStore.create({ type: "error", title: "Operation failed", message: msg });
        }
    }

    async function handleDelete(t: SipTrunkRow) {
        if (!confirm(`Delete SIP trunk "${t.trunk_name}"? This cannot be undone.`)) return;
        try {
            await deleteMutation.mutateAsync(t.id);
            notificationsStore.create({
                type: "success",
                title: "SIP trunk deleted",
                message: t.trunk_name,
            });
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : "Delete failed";
            notificationsStore.create({ type: "error", title: "Delete failed", message: msg });
        }
    }

    return (
        <Card>
            <CardHeader>
                <div className="flex items-center justify-between">
                    <div>
                        <CardTitle className="flex items-center gap-2">
                            <ServerCog className="h-5 w-5" aria-hidden /> Local PBX / SIP Trunks
                        </CardTitle>
                        <CardDescription>
                            Point Talky at your own Asterisk / FreeSWITCH / Kamailio trunk. Run <strong>Test</strong> to
                            verify reachability — activation is blocked until at least one successful test.
                        </CardDescription>
                    </div>
                    <Button onClick={openCreate} size="sm">
                        <Plus className="mr-1 h-4 w-4" aria-hidden /> Add trunk
                    </Button>
                </div>
            </CardHeader>
            <CardContent>
                {trunksQuery.isLoading ? (
                    <div className="flex items-center justify-center py-8 text-muted-foreground">
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading trunks…
                    </div>
                ) : trunks.length === 0 ? (
                    <div className="py-8 text-center text-sm text-muted-foreground">
                        No SIP trunks configured yet. Click <strong>Add trunk</strong> to connect your PBX.
                    </div>
                ) : (
                    <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
                        <table className="min-w-full text-sm">
                            <thead>
                                <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                                    <th className="px-4 py-3">Trunk</th>
                                    <th className="px-4 py-3">Endpoint</th>
                                    <th className="px-4 py-3">Direction</th>
                                    <th className="px-4 py-3">Auth</th>
                                    <th className="px-4 py-3">Reachability</th>
                                    <th className="px-4 py-3">Active</th>
                                    <th className="px-4 py-3 text-right">Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {trunks.map((t) => (
                                    <tr key={t.id} className="border-b border-border last:border-b-0">
                                        <td className="px-4 py-3 font-semibold text-foreground">{t.trunk_name}</td>
                                        <td className="px-4 py-3 text-muted-foreground font-mono text-xs">
                                            {t.transport.toUpperCase()}://{t.sip_domain}:{t.port}
                                        </td>
                                        <td className="px-4 py-3 capitalize text-muted-foreground">{t.direction}</td>
                                        <td className="px-4 py-3 text-muted-foreground">
                                            {t.auth_configured ? t.auth_username || "configured" : "—"}
                                        </td>
                                        <td className="px-4 py-3">
                                            <TestStatusBadge trunk={t} />
                                        </td>
                                        <td className="px-4 py-3">
                                            <span
                                                className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${t.is_active
                                                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                                                    : "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400"
                                                    }`}
                                            >
                                                {t.is_active ? "Active" : "Inactive"}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3 text-right space-x-1 whitespace-nowrap">
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                onClick={() => handleTest(t)}
                                                disabled={testingId === t.id}
                                                title="Probe SIP host for reachability"
                                            >
                                                {testingId === t.id ? (
                                                    <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                                                ) : (
                                                    <><Activity className="mr-1 h-3 w-3" aria-hidden /> Test</>
                                                )}
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                onClick={() => openEdit(t)}
                                                title="Edit trunk"
                                            >
                                                <Pencil className="h-3 w-3" aria-hidden />
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant={t.is_active ? "outline" : "default"}
                                                onClick={() => handleToggle(t)}
                                                title={
                                                    t.is_active
                                                        ? "Deactivate"
                                                        : t.last_test_result?.ok
                                                            ? "Activate"
                                                            : "Run a successful Test first"
                                                }
                                            >
                                                {t.is_active ? (
                                                    <><PowerOff className="mr-1 h-3 w-3" aria-hidden /> Off</>
                                                ) : (
                                                    <><Power className="mr-1 h-3 w-3" aria-hidden /> On</>
                                                )}
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                onClick={() => handleDelete(t)}
                                                title="Delete"
                                            >
                                                <Trash2 className="h-3 w-3" aria-hidden />
                                            </Button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </CardContent>

            <Modal
                open={isOpen}
                onOpenChange={(next) => {
                    setIsOpen(next);
                    if (!next) {
                        setFormError(null);
                        setClearAuth(false);
                    }
                }}
                title={mode === "create" ? "Add SIP trunk" : "Edit SIP trunk"}
            >
                <div className="space-y-3">
                    {formError && (
                        <div role="alert" className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-400">
                            {formError}
                        </div>
                    )}
                    <div>
                        <Label htmlFor="trunk_name">Trunk name</Label>
                        <Input
                            id="trunk_name"
                            value={form.trunk_name}
                            onChange={(e) => setForm({ ...form, trunk_name: e.target.value })}
                            placeholder="primary-pbx"
                        />
                    </div>
                    <div className="grid grid-cols-3 gap-3">
                        <div className="col-span-2">
                            <Label htmlFor="sip_domain">SIP domain / host</Label>
                            <Input
                                id="sip_domain"
                                value={form.sip_domain}
                                onChange={(e) => setForm({ ...form, sip_domain: e.target.value })}
                                placeholder="pbx.example.com"
                            />
                        </div>
                        <div>
                            <Label htmlFor="port">Port</Label>
                            <Input
                                id="port"
                                type="number"
                                value={form.port}
                                onChange={(e) => setForm({ ...form, port: Number(e.target.value) || 5060 })}
                            />
                        </div>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <Label htmlFor="transport">Transport</Label>
                            <Select
                                ariaLabel="Transport"
                                value={form.transport}
                                onChange={(next) => {
                                    const t = next as SipTrunkInput["transport"];
                                    // Sensible default port per transport.
                                    const defaultPort = t === "tls" ? 5061 : 5060;
                                    setForm({
                                        ...form,
                                        transport: t,
                                        port: form.port === 5060 || form.port === 5061 ? defaultPort : form.port,
                                    });
                                }}
                            >
                                <option value="udp">UDP</option>
                                <option value="tcp">TCP</option>
                                <option value="tls">TLS</option>
                            </Select>
                        </div>
                        <div>
                            <Label htmlFor="direction">Direction</Label>
                            <Select
                                ariaLabel="Direction"
                                value={form.direction}
                                onChange={(next) => setForm({ ...form, direction: next as SipTrunkInput["direction"] })}
                            >
                                <option value="both">Both</option>
                                <option value="outbound">Outbound only</option>
                                <option value="inbound">Inbound only</option>
                            </Select>
                        </div>
                    </div>
                    {mode === "edit" && (
                        <div className="flex items-center gap-2">
                            <input
                                id="clear-auth"
                                type="checkbox"
                                checked={clearAuth}
                                onChange={(e) => setClearAuth(e.target.checked)}
                                className="h-4 w-4"
                            />
                            <Label htmlFor="clear-auth" className="cursor-pointer">
                                Remove current authentication (IP-based trunk)
                            </Label>
                        </div>
                    )}
                    {!clearAuth && (
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <Label htmlFor="auth_username">Auth username {mode === "edit" ? "(blank = keep)" : "(optional)"}</Label>
                                <Input
                                    id="auth_username"
                                    value={form.auth_username || ""}
                                    onChange={(e) => setForm({ ...form, auth_username: e.target.value })}
                                />
                            </div>
                            <div>
                                <Label htmlFor="auth_password">Auth password {mode === "edit" ? "(blank = keep)" : "(optional)"}</Label>
                                <Input
                                    id="auth_password"
                                    type="password"
                                    value={form.auth_password || ""}
                                    onChange={(e) => setForm({ ...form, auth_password: e.target.value })}
                                    placeholder={mode === "edit" ? "Leave blank to keep current" : ""}
                                />
                            </div>
                        </div>
                    )}
                    <div className="flex justify-end gap-2 pt-2">
                        <Button variant="outline" onClick={() => setIsOpen(false)} disabled={createMutation.isPending || updateMutation.isPending}>
                            Cancel
                        </Button>
                        <Button onClick={handleSubmit} disabled={createMutation.isPending || updateMutation.isPending}>
                            {createMutation.isPending || updateMutation.isPending ? (
                                <><Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Saving…</>
                            ) : mode === "create" ? (
                                "Save trunk"
                            ) : (
                                "Save changes"
                            )}
                        </Button>
                    </div>
                </div>
            </Modal>
        </Card>
    );
}
