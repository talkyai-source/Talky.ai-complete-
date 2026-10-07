"use client";

import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Modal } from "@/components/ui/modal";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
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
    AlertCircle,
    ChevronDown,
    ChevronUp,
} from "lucide-react";
import {
    useSipTrunks,
    useCreateSipTrunk,
    useUpdateSipTrunk,
    useTestSipTrunk,
    useActivateSipTrunk,
    useDeactivateSipTrunk,
    useDeleteSipTrunk,
    usePoolTrunks,
    usePoolAssignment,
    useSetPoolAssignment,
    type SipTrunkInput,
    type SipTrunkRow,
} from "@/lib/telephony-api";
import { useNotificationsActions } from "@/lib/notifications-client";

type DtmfMode = "rfc2833" | "sip-info" | "inband" | "auto";

/**
 * Form state for the Add/Edit trunk modal.
 *
 * The first seven fields map 1:1 to top-level columns the backend accepts
 * (`SIPTrunkCreateRequest`, which is `extra="forbid"`). Everything below
 * `caller_id` is persisted inside the trunk's free-form `metadata` JSON —
 * the backend stores/returns it verbatim, so these are added without any
 * schema migration. Codec selection and dial-prefix/strip-digits are
 * deliberately NOT here: the backend models those as separate CodecPolicy
 * and RoutePolicy resources.
 */
interface TrunkForm {
    trunk_name: string;
    sip_domain: string;
    port: number;
    transport: "udp" | "tcp" | "tls";
    direction: "inbound" | "outbound" | "both";
    auth_username: string;
    auth_password: string;
    // --- advanced (persisted in trunk.metadata) ---
    caller_id: string;
    outbound_proxy: string;
    auth_realm: string;
    register: boolean;
    register_interval: number;
    dtmf_mode: DtmfMode;
    srtp: boolean;
}

const EMPTY_FORM: TrunkForm = {
    trunk_name: "",
    sip_domain: "",
    port: 5060,
    transport: "udp",
    direction: "both",
    auth_username: "",
    auth_password: "",
    caller_id: "",
    outbound_proxy: "",
    auth_realm: "",
    register: false,
    register_interval: 3600,
    dtmf_mode: "rfc2833",
    srtp: false,
};

const DTMF_MODES: DtmfMode[] = ["rfc2833", "sip-info", "inband", "auto"];

/** Pull the metadata-backed fields off an existing trunk row into form shape. */
function metaToForm(meta: Record<string, unknown>): Pick<
    TrunkForm,
    "caller_id" | "outbound_proxy" | "auth_realm" | "register" | "register_interval" | "dtmf_mode" | "srtp"
> {
    const str = (v: unknown) => (typeof v === "string" ? v : "");
    return {
        caller_id: str(meta.caller_id),
        outbound_proxy: str(meta.outbound_proxy),
        auth_realm: str(meta.auth_realm),
        register: typeof meta.register === "boolean" ? meta.register : false,
        register_interval: typeof meta.register_interval === "number" ? meta.register_interval : 3600,
        dtmf_mode: DTMF_MODES.includes(meta.dtmf_mode as DtmfMode) ? (meta.dtmf_mode as DtmfMode) : "rfc2833",
        srtp: typeof meta.srtp === "boolean" ? meta.srtp : false,
    };
}

/**
 * Build the metadata payload from the form, merged over the row's existing
 * metadata so keys this UI doesn't own (set elsewhere) are preserved.
 * Empty optional strings are removed rather than written as "".
 */
function formToMeta(form: TrunkForm, base: Record<string, unknown>): Record<string, unknown> {
    const meta: Record<string, unknown> = { ...base };
    const setOrDel = (key: string, val: string) => {
        const t = val.trim();
        if (t) meta[key] = t;
        else delete meta[key];
    };
    setOrDel("caller_id", form.caller_id);
    setOrDel("outbound_proxy", form.outbound_proxy);
    setOrDel("auth_realm", form.auth_realm);
    meta.register = form.register;
    meta.register_interval = form.register_interval;
    meta.dtmf_mode = form.dtmf_mode;
    meta.srtp = form.srtp;
    return meta;
}

type ModalMode = "create" | "edit";

function TestStatusBadge({ trunk }: { trunk: SipTrunkRow }) {
    // Only current runtime evidence grants readiness; a saved probe or REGISTER
    // response alone does not prove the outbound contact can accept a call.
    const inboundOnly = trunk.direction === "inbound";
    const ready = trunk.is_active && (inboundOnly ? trunk.inbound_runtime_ready === true : trunk.runtime_ready);
    const detail = inboundOnly
        ? (ready ? "Inbound configuration is ready." : "Waiting for fresh inbound runtime evidence.")
        : trunk.runtime_status_detail || "Waiting for a live status check.";
    return (
        <div className="space-y-1">
            <span className={`inline-flex items-start gap-1 text-xs font-bold ${ready ? "text-emerald-700 dark:text-emerald-400" : "text-amber-700 dark:text-amber-400"}`}>
                {ready ? <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0" aria-hidden /> : <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />}
                <span>{inboundOnly ? "Inbound" : "Outbound"} {ready ? "ready" : "not ready"} · {detail}</span>
            </span>
            {trunk.direction === "both" && <div className="text-xs text-muted-foreground">Inbound {trunk.is_active && trunk.inbound_runtime_ready === true ? "ready" : "not ready"}</div>}
            {trunk.live_status_checked_at && <div className="text-xs text-muted-foreground">Checked {new Date(trunk.live_status_checked_at).toLocaleTimeString()}</div>}
            {trunk.last_test_result && <div className="text-xs text-muted-foreground">Last probe: {trunk.last_test_result.detail || (trunk.last_test_result.ok ? "Host responded" : trunk.last_test_result.error || "Failed")}</div>}
        </div>
    );
}

/**
 * Shared trunk pool selector — allot one of the platform's registered pool
 * accounts (e.g. 150001–150004) to this tenant. When set, this tenant's
 * outbound calls dial on that account with no own trunk / registration needed.
 */
function PoolAccountSelector() {
    const { create: createNotification } = useNotificationsActions();
    const poolQuery = usePoolTrunks();
    const assignmentQuery = usePoolAssignment();
    const setAssignment = useSetPoolAssignment();

    const pool = poolQuery.data ?? [];
    const current = assignmentQuery.data?.pool_trunk_id ?? "";

    // Nothing to show until the platform actually has pool accounts.
    if (!poolQuery.isLoading && pool.length === 0) return null;

    async function onChange(value: string) {
        try {
            await setAssignment.mutateAsync(value || null);
            createNotification({
                type: "success",
                title: value ? "Shared account assigned" : "Shared account cleared",
                message: value
                    ? "This tenant will now dial on the selected pool account."
                    : "This tenant will use its own trunk (or the default).",
            });
        } catch (e) {
            createNotification({
                type: "error",
                title: "Couldn't update",
                message: e instanceof Error ? e.message : "Failed to set the shared account.",
            });
        }
    }

    return (
        <div className="mb-4 rounded-lg border border-border bg-muted/30 p-3">
            <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <div className="text-sm font-medium text-foreground">Shared trunk account</div>
                    <div className="text-xs text-muted-foreground">
                        Dial on one of the platform&apos;s registered pool accounts — no setup or
                        registration needed. Overrides your own trunks when set.
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    <Select
                        ariaLabel="Shared trunk account"
                        value={current}
                        onChange={(next) => void onChange(next)}
                        disabled={setAssignment.isPending || poolQuery.isLoading}
                        className="min-w-[14rem]"
                    >
                        <option value="">— None (use my own trunk) —</option>
                        {pool.map((p) => (
                            <option key={p.id} value={p.id} disabled={!p.runtime_ready}>
                                {p.label}
                                {p.caller_id ? ` · ${p.caller_id}` : ""}
                                {p.registration_status && p.registration_status !== "registered"
                                    ? ` · ${p.registration_status}`
                                    : ""}
                                {!p.runtime_ready ? ` · unavailable: ${p.runtime_status_detail}` : ""}
                            </option>
                        ))}
                    </Select>
                    {setAssignment.isPending && (
                        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden />
                    )}
                </div>
            </div>
        </div>
    );
}

export function SipTrunksList() {
    const { create: createNotification } = useNotificationsActions();
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
    const [form, setForm] = useState<TrunkForm>(EMPTY_FORM);
    const [formError, setFormError] = useState<string | null>(null);
    const [authMode, setAuthMode] = useState<"ip" | "credentials">("ip");
    const [originalAuth, setOriginalAuth] = useState({ configured: false, username: "" });
    const [deletingTrunk, setDeletingTrunk] = useState<SipTrunkRow | null>(null);
    const [testingId, setTestingId] = useState<string | null>(null);
    const [showAdvanced, setShowAdvanced] = useState(false);
    // Existing metadata of the row being edited, so we preserve keys this
    // form doesn't own when we PATCH.
    const [baseMeta, setBaseMeta] = useState<Record<string, unknown>>({});

    const trunks: SipTrunkRow[] = trunksQuery.data ?? [];

    function openCreate() {
        setMode("create");
        setEditingId(null);
        setForm(EMPTY_FORM);
        setBaseMeta({});
        setFormError(null);
        setAuthMode("ip");
        setOriginalAuth({ configured: false, username: "" });
        setShowAdvanced(false);
        setIsOpen(true);
    }

    function openEdit(t: SipTrunkRow) {
        setMode("edit");
        setEditingId(t.id);
        const meta = t.metadata ?? {};
        const advanced = metaToForm(meta);
        setForm({
            trunk_name: t.trunk_name,
            sip_domain: t.sip_domain,
            port: t.port,
            transport: t.transport,
            direction: t.direction,
            auth_username: t.auth_username || "",
            auth_password: "", // never returned from backend; user must re-enter to overwrite
            ...advanced,
        });
        setBaseMeta(meta);
        setFormError(null);
        setAuthMode(t.auth_configured ? "credentials" : "ip");
        setOriginalAuth({ configured: t.auth_configured, username: t.auth_username || "" });
        // Auto-expand Advanced if this trunk already has any advanced values set.
        setShowAdvanced(
            Boolean(advanced.caller_id || advanced.outbound_proxy || advanced.auth_realm || advanced.register || advanced.srtp),
        );
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
        if (form.register && (form.register_interval < 60 || form.register_interval > 86400)) {
            setFormError("Register interval must be between 60 and 86400 seconds.");
            return;
        }

        const username = form.auth_username.trim();
        const preserveAuth = mode === "edit" && originalAuth.configured && username === originalAuth.username && !form.auth_password;
        if (authMode === "credentials" && !preserveAuth && (!username || !form.auth_password)) {
            setFormError("Provide both username and password to set or change authentication.");
            return;
        }
        const metadata = formToMeta({ ...form, register: authMode === "credentials" && form.register }, mode === "edit" ? baseMeta : {});
        try {
            if (mode === "create") {
                const payload: SipTrunkInput = {
                    trunk_name: form.trunk_name,
                    sip_domain: form.sip_domain,
                    port: form.port,
                    transport: form.transport,
                    direction: form.direction,
                    ...(authMode === "credentials" ? { auth_username: username, auth_password: form.auth_password } : {}),
                    metadata,
                };
                if (!payload.auth_username && !payload.auth_password) {
                    delete payload.auth_username;
                    delete payload.auth_password;
                } else if (!payload.auth_username || !payload.auth_password) {
                    setFormError("Provide both auth username and password, or leave both blank.");
                    return;
                }
                await createMutation.mutateAsync(payload);
                createNotification({
                    type: "success",
                    title: "SIP trunk added",
                    message: `${form.trunk_name} is saved disabled. Enable it and wait for Ready before calling.`,
                });
            } else {
                if (!editingId) return;
                const patch: Partial<SipTrunkInput> & { clear_auth?: boolean } = {
                    trunk_name: form.trunk_name,
                    sip_domain: form.sip_domain,
                    port: form.port,
                    transport: form.transport,
                    direction: form.direction,
                    metadata,
                };
                if (authMode === "ip" && originalAuth.configured) {
                    patch.clear_auth = true;
                } else if (authMode === "credentials" && !preserveAuth) {
                    patch.auth_username = username;
                    patch.auth_password = form.auth_password;
                }
                await updateMutation.mutateAsync({ id: editingId, patch });
                createNotification({
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
                createNotification({
                    type: "success",
                    title: `${t.trunk_name} is reachable`,
                    message: `${r.latency_ms ?? 0} ms · ${r.detail || `${t.transport.toUpperCase()} ${r.target}`}`,
                });
            } else {
                createNotification({
                    type: "error",
                    title: r.inconclusive ? `${t.trunk_name}: probe inconclusive` : `${t.trunk_name}: probe failed`,
                    message: r.detail || r.error || "Probe failed",
                });
            }
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : "Test failed";
            createNotification({ type: "error", title: "Test failed", message: msg });
        } finally {
            setTestingId(null);
        }
    }

    async function handleToggle(t: SipTrunkRow) {
        try {
            if (t.is_active) {
                await deactivateMutation.mutateAsync(t.id);
                createNotification({
                    type: "success",
                    title: "Trunk deactivated",
                    message: t.trunk_name,
                });
            } else {
                const enabled = await activateMutation.mutateAsync(t.id);
                createNotification({
                    type: enabled.runtime_ready ? "success" : "warning",
                    title: enabled.runtime_ready ? "Trunk runtime ready" : "Trunk enabled; runtime check pending",
                    message: enabled.runtime_ready
                        ? t.trunk_name
                        : `${t.trunk_name}: ${enabled.runtime_status_detail}`,
                });
            }
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : "Operation failed";
            createNotification({ type: "error", title: "Operation failed", message: msg });
        }
    }

    async function handleDelete(t: SipTrunkRow) {
        if (t.is_active) await deactivateMutation.mutateAsync(t.id);
        await deleteMutation.mutateAsync(t.id);
        createNotification({ type: "success", title: "SIP trunk deleted", message: t.trunk_name });
    }
    return (
        <Card>
            <CardHeader>
                {/*
                  * Below lg: stacked (title, full-width description, button bottom-right).
                  * lg and up: button sits top-right on the title's row; description
                  * spans the full width on the row below. DOM order is title,
                  * description, button in both.
                  */}
                <div className="grid grid-cols-1 gap-y-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:gap-x-4 lg:gap-y-1.5">
                    <CardTitle className="flex min-w-0 items-center gap-2 whitespace-nowrap text-[14px] sm:text-xl sm:leading-none lg:col-start-1 lg:row-start-1 lg:self-center">
                        <ServerCog className="h-4 w-4 sm:h-5 sm:w-5" aria-hidden /> Local PBX / SIP Trunks
                    </CardTitle>
                    <CardDescription className="lg:col-span-2 lg:row-start-2">
                        Connect your SIP provider or PBX using an IP allowlist or credentials. Enable the trunk,
                        then wait for <strong>Ready</strong> before calling. <strong>Test</strong> checks the host;
                        it does not enable calling. Live readiness refreshes automatically.
                    </CardDescription>
                    <Button
                        onClick={openCreate}
                        size="sm"
                        className="h-8 justify-self-end lg:col-start-2 lg:row-start-1 lg:h-7 lg:gap-1.5 lg:self-center lg:px-2.5 lg:[&_svg]:size-3.5"
                    >
                        <Plus className="mr-1 h-4 w-4" aria-hidden /> Add trunk
                    </Button>
                </div>
            </CardHeader>
            <CardContent>
                <PoolAccountSelector />
                {trunksQuery.isLoading ? (
                    <div className="flex items-center justify-center py-8 text-muted-foreground">
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading trunks…
                    </div>
                ) : trunksQuery.isError ? (
                    <div role="alert" className="py-4 text-sm text-destructive">Could not load SIP trunks. <Button variant="outline" size="sm" onClick={() => void trunksQuery.refetch()}>Retry</Button></div>
                ) : trunks.length === 0 ? (
                    <div className="py-8 text-center text-sm text-muted-foreground">
                        No SIP trunks configured yet. Click <strong>Add trunk</strong> to connect your PBX.
                    </div>
                ) : (
                    <div className="rounded-xl border border-border bg-card/50 overflow-x-auto">
                        {/*
                         * Fixed column widths (colgroup) except Endpoint, which is left
                         * unset so table-layout:fixed hands it 100% of any width beyond
                         * the min-width below. Every header and body cell uses the same
                         * px-2 horizontal padding, so the gap between every pair of
                         * columns is the same 16px; each width below is the column's
                         * longest real content, measured in a browser, plus that 16px, so
                         * the visible gaps stay close to even too. Trunk 137 (an 18-char
                         * hyphenated name like "blaze-pool-150004" wraps at the hyphens),
                         * Direction 80 ("outbound" is 64px), Auth 112 (an 11-digit number
                         * is 83px, so up to ~12 digits stay on one line), Live status 288
                         * (plain bold text that wraps; the detail comes from an Asterisk
                         * log line with no length cap), Active 77 (the "Inactive" pill is
                         * 60px), Endpoint 191 at the min-width (fits
                         * "UDP://sip3.blazedigital.com:5060", 174px, on one line).
                         * Text cells use wrap-anywhere (overflow-wrap:anywhere), so a value
                         * longer than its column (a 15-digit Auth, a hyphenless 60-char
                         * name) wraps inside its own column instead of spilling into the
                         * next one.
                         * The four control columns each get their own centred heading on
                         * the same single header row as the rest: Test 92, Edit 56,
                         * Status (On/Off) 88, Delete 56 — each the button's width plus the
                         * 16px padding, with the button centred in its cell under its own
                         * heading.
                         * min-width is the fixed-column sum (986) plus Endpoint's 191 =
                         * 1177px; below it the wrapper above scrolls horizontally instead
                         * of shrinking these. The settings page wraps this tab in its own
                         * outer Card, so the available width is at most 922px and the
                         * table scrolls at every desktop width as well as on phones and
                         * tablets; the wrapper's horizontal scroll reaches every column.
                         */}
                        <table className="table-fixed w-full min-w-[1177px] text-sm">
                            <colgroup>
                                <col style={{ width: 137 }} />
                                <col />
                                <col style={{ width: 80 }} />
                                <col style={{ width: 112 }} />
                                <col style={{ width: 288 }} />
                                <col style={{ width: 77 }} />
                                <col style={{ width: 92 }} />
                                <col style={{ width: 56 }} />
                                <col style={{ width: 88 }} />
                                <col style={{ width: 56 }} />
                            </colgroup>
                            <thead>
                                <tr className="bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                                    <th className="border-b border-border px-2 py-3">Trunk</th>
                                    <th className="border-b border-border px-2 py-3">Endpoint</th>
                                    <th className="border-b border-border px-2 py-3">Direction</th>
                                    <th className="border-b border-border px-2 py-3">Auth</th>
                                    <th className="border-b border-border px-2 py-3">Live status</th>
                                    <th className="border-b border-border px-2 py-3">Enabled</th>
                                    <th className="border-b border-border px-2 py-3 text-center">Test</th>
                                    <th className="border-b border-border px-2 py-3 text-center">Edit</th>
                                    <th className="border-b border-border px-2 py-3 text-center">Status</th>
                                    <th className="border-b border-border px-2 py-3 text-center">Delete</th>
                                </tr>
                            </thead>
                            <tbody>
                                {trunks.map((t) => (
                                    <tr key={t.id} className="border-b border-border last:border-b-0">
                                        <td className="px-2 py-4 font-semibold text-foreground wrap-anywhere">
                                            {t.trunk_name}
                                            {typeof t.metadata?.caller_id === "string" && t.metadata.caller_id ? (
                                                <div className="text-[10px] font-normal text-muted-foreground">
                                                    Caller ID: {t.metadata.caller_id as string}
                                                </div>
                                            ) : null}
                                        </td>
                                        <td className="px-2 py-4 text-muted-foreground font-mono text-xs wrap-anywhere">
                                            {t.transport.toUpperCase()}://{t.sip_domain}:{t.port}
                                        </td>
                                        <td className="px-2 py-4 capitalize text-muted-foreground wrap-anywhere">{t.direction}</td>
                                        <td className="px-2 py-4 text-muted-foreground wrap-anywhere">
                                            {t.auth_configured ? `Credentials${t.auth_username ? ` · ${t.auth_username}` : ""}` : "IP allowlist"}
                                        </td>
                                        <td className="px-2 py-4 wrap-anywhere">
                                            <TestStatusBadge trunk={t} />
                                        </td>
                                        <td className="px-2 py-4">
                                            <span
                                                className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${t.is_active
                                                    ? "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400"
                                                    : "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400"
                                                    }`}
                                            >
                                                {t.is_active ? "Yes" : "No"}
                                            </span>
                                        </td>
                                        <td className="px-2 py-4">
                                            <div className="flex items-center justify-center">
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
                                            </div>
                                        </td>
                                        <td className="px-2 py-4">
                                            <div className="flex items-center justify-center">
                                                <Button
                                                    size="sm"
                                                    variant="ghost"
                                                    onClick={() => openEdit(t)}
                                                    title="Edit trunk"
                                                    aria-label={`Edit ${t.trunk_name}`}
                                                >
                                                    <Pencil className="h-3 w-3" aria-hidden />
                                                </Button>
                                            </div>
                                        </td>
                                        <td className="px-2 py-4">
                                            <div className="flex items-center justify-center">
                                                <Button
                                                    size="sm"
                                                    variant={t.is_active ? "outline" : "default"}
                                                    onClick={() => handleToggle(t)}
                                                    title={t.is_active ? "Disable trunk" : "Enable trunk and check readiness"}
                                                    aria-label={`${t.is_active ? "Disable" : "Enable"} ${t.trunk_name}`}
                                                    disabled={activateMutation.isPending || deactivateMutation.isPending || deleteMutation.isPending}
                                                >
                                                    {t.is_active ? (
                                                        <><PowerOff className="mr-1 h-3 w-3" aria-hidden /> Off</>
                                                    ) : (
                                                        <><Power className="mr-1 h-3 w-3" aria-hidden /> On</>
                                                    )}
                                                </Button>
                                            </div>
                                        </td>
                                        <td className="px-2 py-4">
                                            <div className="flex items-center justify-center">
                                                <Button
                                                    size="sm"
                                                    variant="ghost"
                                                    onClick={() => setDeletingTrunk(t)}
                                                    title="Delete"
                                                    aria-label={`Delete ${t.trunk_name}`}
                                                    disabled={deleteMutation.isPending}
                                                >
                                                    <Trash2 className="h-3 w-3" aria-hidden />
                                                </Button>
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </CardContent>

            <ConfirmDialog
                open={Boolean(deletingTrunk)}
                onOpenChange={(open) => { if (!open) setDeletingTrunk(null); }}
                title="Delete SIP trunk"
                description={deletingTrunk?.trunk_name}
                warningText="The trunk will be disabled and removed. If a campaign, phone number or active call still uses it, remove that assignment or finish the call first. You can also disable it without deleting it."
                confirmLabel={deletingTrunk?.is_active ? "Disable and delete" : "Delete trunk"}
                onConfirm={async () => { if (deletingTrunk) await handleDelete(deletingTrunk); }}
            />

            <Modal
                open={isOpen}
                onOpenChange={(next) => {
                    setIsOpen(next);
                    if (!next) {
                        setFormError(null);
                        setShowAdvanced(false);
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
                    <div>
                        <Label>Authentication</Label>
                        <Select ariaLabel="Authentication" value={authMode} onChange={(value) => {
                            setAuthMode(value as "ip" | "credentials");
                            if (value === "ip") setForm({ ...form, register: false });
                        }}>
                            <option value="ip">IP allowlist (no password)</option>
                            <option value="credentials">Username and password</option>
                        </Select>
                        <p className="mt-1 text-xs text-muted-foreground">{authMode === "ip" ? "Allowlist the platform's outbound IP with your provider. SIP registration and credentials are not required." : "Leave the saved password blank to keep it. Changing the username requires a new password."}</p>
                    </div>
                    {authMode === "credentials" && (
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <Label htmlFor="auth_username">Auth username</Label>
                                <Input
                                    id="auth_username"
                                    value={form.auth_username || ""}
                                    onChange={(e) => setForm({ ...form, auth_username: e.target.value })}
                                />
                            </div>
                            <div>
                                <Label htmlFor="auth_password">Auth password {mode === "edit" && originalAuth.configured ? "(blank = keep)" : ""}</Label>
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

                    <div>
                        <Label htmlFor="caller_id">Caller ID / From number</Label>
                        <Input
                            id="caller_id"
                            value={form.caller_id}
                            onChange={(e) => setForm({ ...form, caller_id: e.target.value })}
                            placeholder="+15551234567"
                        />
                        <p className="mt-1 text-xs text-muted-foreground">
                            Authorized number in international format, for example +442079460000. The selected route and caller ID are checked before dialing.
                        </p>
                    </div>

                    <div className="border-t border-border pt-2">
                        <button
                            type="button"
                            onClick={() => setShowAdvanced((s) => !s)}
                            className="flex w-full items-center justify-between text-sm font-medium text-foreground"
                            aria-expanded={showAdvanced}
                        >
                            <span>Advanced options</span>
                            {showAdvanced ? (
                                <ChevronUp className="h-4 w-4" aria-hidden />
                            ) : (
                                <ChevronDown className="h-4 w-4" aria-hidden />
                            )}
                        </button>
                    </div>

                    {showAdvanced && (
                        <div className="space-y-3 rounded-md border border-border bg-muted/20 p-3">
                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <Label htmlFor="outbound_proxy">Outbound proxy</Label>
                                    <Input
                                        id="outbound_proxy"
                                        value={form.outbound_proxy}
                                        onChange={(e) => setForm({ ...form, outbound_proxy: e.target.value })}
                                        placeholder="proxy.example.com:5060"
                                    />
                                </div>
                                <div>
                                    <Label htmlFor="auth_realm">Auth ID / realm</Label>
                                    <Input
                                        id="auth_realm"
                                        value={form.auth_realm}
                                        onChange={(e) => setForm({ ...form, auth_realm: e.target.value })}
                                        placeholder="(if different from username)"
                                    />
                                </div>
                            </div>

                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <Label htmlFor="dtmf_mode">DTMF mode</Label>
                                    <Select
                                        ariaLabel="DTMF mode"
                                        value={form.dtmf_mode}
                                        onChange={(next) => setForm({ ...form, dtmf_mode: next as DtmfMode })}
                                    >
                                        <option value="rfc2833">RFC 2833 (recommended)</option>
                                        <option value="sip-info">SIP INFO</option>
                                        <option value="inband">In-band</option>
                                        <option value="auto">Auto</option>
                                    </Select>
                                </div>
                                <div>
                                    <Label htmlFor="register_interval">
                                        Register interval (s)
                                    </Label>
                                    <Input
                                        id="register_interval"
                                        type="number"
                                        value={form.register_interval}
                                        disabled={!form.register}
                                        onChange={(e) =>
                                            setForm({ ...form, register_interval: Number(e.target.value) || 3600 })
                                        }
                                    />
                                </div>
                            </div>

                            <div className="flex items-center gap-2">
                                <label className="-m-1 inline-flex cursor-pointer p-1">
                                    <input
                                        id="register"
                                        type="checkbox"
                                        checked={form.register}
                                        disabled={authMode === "ip"}
                                        onChange={(e) => setForm({ ...form, register: e.target.checked })}
                                        className="h-4 w-4"
                                    />
                                </label>
                                <Label htmlFor="register" className="cursor-pointer">
                                    Register with the PBX (leave off for IP-based trunks)
                                </Label>
                            </div>

                            <div className="flex items-center gap-2">
                                <label className="-m-1 inline-flex cursor-pointer p-1">
                                    <input
                                        id="srtp"
                                        type="checkbox"
                                        checked={form.srtp}
                                        onChange={(e) => setForm({ ...form, srtp: e.target.checked })}
                                        className="h-4 w-4"
                                    />
                                </label>
                                <Label htmlFor="srtp" className="cursor-pointer">
                                    Enable SRTP media encryption (typically with TLS transport)
                                </Label>
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
