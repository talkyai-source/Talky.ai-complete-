/**
 * Provider-address parsing for the "Connect your PBX" dialog.
 *
 * People paste whatever their provider sent them: a host name, `host:port`, an
 * IP address, or a full SIP URI such as `sip:pbx.example.com:5080;transport=tcp`.
 * This turns any of those into the three values the API stores (host, port,
 * transport) so the form never has to ask for protocol details up front.
 *
 * Pure and dependency free so it is unit tested without a DOM.
 */

export type PbxTransport = "udp" | "tcp" | "tls";

/** What the address text says on its own. `null` means "not stated". */
export interface ParsedAddress {
    host: string;
    port: number | null;
    transport: PbxTransport | null;
}

export type AddressResult =
    | { ok: true; value: ParsedAddress }
    | { ok: false; reason: string };

/** The final connection values, after defaults and any Advanced overrides. */
export interface ResolvedAddress {
    host: string;
    port: number;
    transport: PbxTransport;
}

const BAD_HOST = "That doesn't look like a valid address. Use a name such as pbx.example.com, or an IP address.";
const BAD_PORT = "The port must be a number between 1 and 65535.";
const DNS_LABEL = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const IPV4 = /^\d{1,3}(?:\.\d{1,3}){3}$/;

function isValidHost(host: string): boolean {
    if (!host || host.length > 253) return false;
    // A bare IPv6 literal: hex digits, colons and (for mapped addresses) dots.
    if (host.includes(":")) return /^[0-9a-f:.]+$/.test(host) && host.split(":").length >= 3;
    if (IPV4.test(host)) return host.split(".").every((octet) => Number(octet) <= 255);
    return host.split(".").every((label) => DNS_LABEL.test(label));
}

/**
 * Accepts `host`, `host:port`, an IPv4/IPv6 address, or a SIP URI
 * (`sip:`/`sips:`, optional `user@`, optional `;transport=` parameter).
 * A `sips:` address implies TLS; an explicit `;transport=` wins over the scheme.
 */
export function parseProviderAddress(input: string): AddressResult {
    let rest = input.trim();
    if (!rest) return { ok: false, reason: "Enter the address your provider gave you." };
    if (rest.startsWith("<") && rest.endsWith(">")) rest = rest.slice(1, -1).trim();

    let transport: PbxTransport | null = null;
    const scheme = /^(sips?|udp|tcp|tls):(?:\/\/)?/i.exec(rest);
    if (scheme) {
        const name = scheme[1].toLowerCase();
        if (name === "sips" || name === "tls") transport = "tls";
        else if (name === "tcp" || name === "udp") transport = name;
        rest = rest.slice(scheme[0].length);
    }
    // Some other kind of link (https://...) is not a phone-system address.
    if (rest.includes("://")) return { ok: false, reason: BAD_HOST };

    // Header part (`?x=y`), URI parameters (`;transport=tcp`), user@ and /path
    // are never part of the server address.
    const query = rest.indexOf("?");
    if (query >= 0) rest = rest.slice(0, query);
    let params = "";
    const semicolon = rest.indexOf(";");
    if (semicolon >= 0) {
        params = rest.slice(semicolon + 1);
        rest = rest.slice(0, semicolon);
    }
    const at = rest.lastIndexOf("@");
    if (at >= 0) rest = rest.slice(at + 1);
    const slash = rest.indexOf("/");
    if (slash >= 0) rest = rest.slice(0, slash);

    let host = rest.trim();
    let portText = "";
    if (host.startsWith("[")) {
        const end = host.indexOf("]");
        if (end < 0) return { ok: false, reason: BAD_HOST };
        const tail = host.slice(end + 1);
        host = host.slice(1, end);
        if (tail) {
            if (!tail.startsWith(":")) return { ok: false, reason: BAD_HOST };
            portText = tail.slice(1);
        }
    } else if (host.split(":").length === 2) {
        [host, portText] = host.split(":") as [string, string];
    }
    // More than one colon without brackets is a bare IPv6 address with no port.

    host = host.trim().toLowerCase().replace(/\.$/, "");
    if (!isValidHost(host)) return { ok: false, reason: BAD_HOST };

    let port: number | null = null;
    portText = portText.trim();
    if (portText) {
        if (!/^\d{1,5}$/.test(portText) || Number(portText) < 1 || Number(portText) > 65535) {
            return { ok: false, reason: BAD_PORT };
        }
        port = Number(portText);
    }

    const declared = /(?:^|;)\s*transport=([^;]*)/i.exec(params);
    if (declared) {
        const value = declared[1].trim().toLowerCase();
        if (value !== "udp" && value !== "tcp" && value !== "tls") {
            return { ok: false, reason: `The connection type "${declared[1].trim()}" isn't supported. Use UDP, TCP or TLS.` };
        }
        transport = value;
    }

    return { ok: true, value: { host, port, transport } };
}

/**
 * Applies defaults and Advanced overrides: the transport is the override, else
 * what the address said, else UDP; the port is the override, else what the
 * address said, else 5060 (5061 for TLS).
 */
export function resolveAddress(
    parsed: ParsedAddress,
    overrides: { transport?: PbxTransport | null; port?: number | null } = {},
): ResolvedAddress {
    const transport = overrides.transport ?? parsed.transport ?? "udp";
    const port = overrides.port ?? parsed.port ?? (transport === "tls" ? 5061 : 5060);
    return { host: parsed.host, port, transport };
}

/** The small line shown under the field, e.g. "sip3.example.com · port 5060". */
export function describeAddress(address: ResolvedAddress): string {
    const base = `${address.host} · port ${address.port}`;
    return address.transport === "udp" ? base : `${base} · ${address.transport.toUpperCase()}`;
}

/** The saved server on a line's card, e.g. "sip3.example.com:5060" (plus the connection type when it is not UDP). */
export function formatServer(address: ResolvedAddress): string {
    const base = `${address.host.includes(":") ? `[${address.host}]` : address.host}:${address.port}`;
    return address.transport === "udp" ? base : `${base} · ${address.transport.toUpperCase()}`;
}

/** Rebuilds the text a person would have typed for a saved line, for the Edit form. */
export function formatAddressForEdit(host: string, port: number, transport: PbxTransport): string {
    const defaultPort = transport === "tls" ? 5061 : 5060;
    if (port === defaultPort) return host;
    return `${host.includes(":") ? `[${host}]` : host}:${port}`;
}
