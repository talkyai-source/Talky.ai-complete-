/**
 * Form model for the "Connect your PBX" dialog: defaults, loading a saved
 * line into the form, validation, and building the create/update payloads.
 *
 * Kept free of React so the rules (what is required, what is sent, which saved
 * settings are preserved) are unit tested directly.
 *
 * The API stores some settings as top-level columns (host, port, transport,
 * direction, credentials) and the rest inside the free-form `metadata` JSON.
 * Editing merges over the saved metadata so keys this form does not own are
 * kept exactly as they were.
 */
import type { SipTrunkInput, SipTrunkRow } from "@/lib/telephony-api";
import {
    formatAddressForEdit,
    parseProviderAddress,
    resolveAddress,
    type PbxTransport,
    type ResolvedAddress,
} from "@/components/settings/pbx-address";

export type DtmfMode = "rfc2833" | "sip-info" | "inband" | "auto";
export const DTMF_MODES: readonly DtmfMode[] = ["rfc2833", "sip-info", "inband", "auto"];

export type LineDirection = SipTrunkInput["direction"];
export type LineUpdate = Partial<SipTrunkInput> & { clear_auth?: boolean };

export const DEFAULT_REGISTER_INTERVAL = 3600;

export interface LineForm {
    /** What the person typed: host, host:port, IP, or a SIP URI. */
    address: string;
    /** Optional; blank means "use the provider address". */
    name: string;
    /** The provider trusts our server's IP address: no username or password. */
    ipAccess: boolean;
    username: string;
    password: string;
    /** Optional number shown to the people being called. */
    callerId: string;
    makeCalls: boolean;
    receiveCalls: boolean;
    // --- Advanced ---
    transport: "auto" | PbxTransport;
    /** Blank means "from the address, else the default". */
    port: string;
    authRealm: string;
    outboundProxy: string;
    dtmfMode: DtmfMode;
    srtp: boolean;
    /** Sign in to the provider so it knows where to send incoming calls. */
    register: boolean;
    registerInterval: string;
}

export function emptyLineForm(): LineForm {
    return {
        address: "",
        name: "",
        ipAccess: false,
        username: "",
        password: "",
        callerId: "",
        makeCalls: true,
        receiveCalls: true,
        transport: "auto",
        port: "",
        authRealm: "",
        outboundProxy: "",
        dtmfMode: "rfc2833",
        srtp: false,
        // Most username-and-password accounts must sign in for incoming calls to
        // arrive. IP-address access never signs in (forced off when saved).
        register: true,
        registerInterval: String(DEFAULT_REGISTER_INTERVAL),
    };
}

export function lineFormFromTrunk(trunk: SipTrunkRow): LineForm {
    const meta = trunk.metadata ?? {};
    const text = (value: unknown) => (typeof value === "string" ? value : "");
    return {
        address: formatAddressForEdit(trunk.sip_domain, trunk.port, trunk.transport),
        name: trunk.trunk_name,
        ipAccess: !trunk.auth_configured,
        username: trunk.auth_username || "",
        password: "", // never returned by the server; leaving it blank keeps the saved one
        callerId: text(meta.caller_id),
        makeCalls: trunk.direction !== "inbound",
        receiveCalls: trunk.direction !== "outbound",
        // "Automatic" means UDP, so a saved UDP line shows as Automatic.
        transport: trunk.transport === "udp" ? "auto" : trunk.transport,
        port: "",
        authRealm: text(meta.auth_realm),
        outboundProxy: text(meta.outbound_proxy),
        dtmfMode: DTMF_MODES.includes(meta.dtmf_mode as DtmfMode) ? (meta.dtmf_mode as DtmfMode) : "rfc2833",
        srtp: typeof meta.srtp === "boolean" ? meta.srtp : false,
        register: typeof meta.register === "boolean" ? meta.register : false,
        registerInterval: String(typeof meta.register_interval === "number" ? meta.register_interval : DEFAULT_REGISTER_INTERVAL),
    };
}

/** Whether a saved line has non-default Advanced settings worth showing open. */
export function hasAdvancedValues(form: LineForm): boolean {
    return Boolean(
        form.outboundProxy || form.authRealm || form.srtp || form.transport !== "auto" || form.dtmfMode !== "rfc2833",
    );
}

/** The Advanced port field: a number, `null` when blank, or "invalid". */
export function portFromField(text: string): number | null | "invalid" {
    const value = text.trim();
    if (!value) return null;
    if (!/^\d{1,5}$/.test(value) || Number(value) < 1 || Number(value) > 65535) return "invalid";
    return Number(value);
}

export function directionOf(form: Pick<LineForm, "makeCalls" | "receiveCalls">): LineDirection | null {
    if (form.makeCalls && form.receiveCalls) return "both";
    if (form.makeCalls) return "outbound";
    if (form.receiveCalls) return "inbound";
    return null;
}

export interface FormContext {
    mode: "create" | "edit";
    /** The saved line being edited. */
    original: SipTrunkRow | null;
}

export type CheckedForm =
    | { ok: false; error: string }
    | {
        ok: true;
        address: ResolvedAddress;
        name: string;
        direction: LineDirection;
        username: string;
        /** Edit only: keep the saved credentials because nothing about them changed. */
        preserveAuth: boolean;
        registerInterval: number;
    };

/** The number must look like a phone number; the API checks the same shape. */
const CALLER_ID = /^\+?[0-9][0-9\s().-]{1,}$/;

export function checkLineForm(form: LineForm, context: FormContext): CheckedForm {
    const parsed = parseProviderAddress(form.address);
    if (!parsed.ok) return { ok: false, error: parsed.reason };
    const portOverride = portFromField(form.port);
    if (portOverride === "invalid") return { ok: false, error: "The port must be a number between 1 and 65535." };
    const address = resolveAddress(parsed.value, {
        transport: form.transport === "auto" ? null : form.transport,
        port: portOverride,
    });

    const name = (form.name.trim() || address.host).slice(0, 100);
    if (name.length < 3) return { ok: false, error: "The line name must be at least 3 characters." };

    const direction = directionOf(form);
    if (!direction) return { ok: false, error: "Turn on Make calls, Receive calls, or both." };

    const callerId = form.callerId.trim();
    if (callerId && (callerId.length > 64 || !CALLER_ID.test(callerId))) {
        return { ok: false, error: "The number to show should be a phone number: digits, with spaces, dashes or brackets if you like." };
    }

    const original = context.original;
    const savedCredentials = context.mode === "edit" && Boolean(original?.auth_configured);
    const username = form.username.trim();
    const preserveAuth = savedCredentials && !form.ipAccess && username === (original?.auth_username || "") && !form.password;
    if (!form.ipAccess && !preserveAuth && (!username || !form.password)) {
        const changedUsername = savedCredentials && username !== (original?.auth_username || "") && !form.password;
        return {
            ok: false,
            error: changedUsername
                ? "Changing the username needs the password again. Enter both username and password."
                : "Enter both username and password, or switch on IP address access.",
        };
    }

    const intervalText = form.registerInterval.trim();
    const interval = Number(intervalText);
    const intervalOk = intervalText !== "" && Number.isInteger(interval) && interval >= 60 && interval <= 86400;
    if (!form.ipAccess && form.register && !intervalOk) {
        return { ok: false, error: "The sign-in refresh time must be between 60 and 86400 seconds." };
    }

    return {
        ok: true,
        address,
        name,
        direction,
        username,
        preserveAuth,
        registerInterval: intervalOk ? interval : DEFAULT_REGISTER_INTERVAL,
    };
}

/**
 * Settings kept in `metadata`, merged over what was saved so keys this form
 * does not own survive an edit. Blank optional text removes the key.
 */
function lineFormToMetadata(form: LineForm, base: Record<string, unknown>, registerInterval: number): Record<string, unknown> {
    const meta: Record<string, unknown> = { ...base };
    const setOrDelete = (key: string, value: string) => {
        const text = value.trim();
        if (text) meta[key] = text;
        else delete meta[key];
    };
    setOrDelete("caller_id", form.callerId);
    setOrDelete("outbound_proxy", form.outboundProxy);
    setOrDelete("auth_realm", form.authRealm);
    // Signing in needs credentials; an IP-address line never does.
    meta.register = !form.ipAccess && form.register;
    meta.register_interval = registerInterval;
    meta.dtmf_mode = form.dtmfMode;
    meta.srtp = form.srtp;
    return meta;
}

export function buildCreateInput(form: LineForm, checked: Extract<CheckedForm, { ok: true }>): SipTrunkInput {
    return {
        trunk_name: checked.name,
        sip_domain: checked.address.host,
        port: checked.address.port,
        transport: checked.address.transport,
        direction: checked.direction,
        ...(form.ipAccess ? {} : { auth_username: checked.username, auth_password: form.password }),
        metadata: lineFormToMetadata(form, {}, checked.registerInterval),
    };
}

export function buildUpdatePatch(form: LineForm, checked: Extract<CheckedForm, { ok: true }>, original: SipTrunkRow): LineUpdate {
    const patch: LineUpdate = {
        trunk_name: checked.name,
        sip_domain: checked.address.host,
        port: checked.address.port,
        transport: checked.address.transport,
        direction: checked.direction,
        metadata: lineFormToMetadata(form, original.metadata ?? {}, checked.registerInterval),
    };
    if (form.ipAccess) {
        if (original.auth_configured) patch.clear_auth = true;
    } else if (!checked.preserveAuth) {
        patch.auth_username = checked.username;
        patch.auth_password = form.password;
    }
    return patch;
}
