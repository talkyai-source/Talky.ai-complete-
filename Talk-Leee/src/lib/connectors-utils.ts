export type ConnectorProviderType = "calendar" | "email" | "crm" | "drive";

export type ConnectorCardAction = "connect" | "reconnect" | "disconnect";

export function connectorCardActionFromStatus(status: string): ConnectorCardAction {
    if (status === "connected") return "disconnect";
    if (status === "expired" || status === "error") return "reconnect";
    return "connect";
}

export function connectorCardActionLabel(action: ConnectorCardAction) {
    if (action === "disconnect") return "Disconnect";
    if (action === "reconnect") return "Reconnect";
    return "Connect";
}

export function extractAuthorizationUrl(data: unknown): string {
    if (typeof data === "string") {
        const trimmed = data.trim();
        if (trimmed.length > 0) return trimmed;
    }
    if (!data || typeof data !== "object") throw new Error("Authorization failed. Please try again.");
    const obj = data as Record<string, unknown>;
    const candidates = ["authorization_url", "authorize_url", "auth_url", "redirect_url", "url"];
    for (const k of candidates) {
        const v = obj[k];
        if (typeof v === "string" && v.trim().length > 0) return v.trim();
    }
    throw new Error("Authorization failed. Please try again.");
}

export function parseConnectorsCallback(
    params: URLSearchParams,
    defaultProviderType?: string
): {
    ok: boolean;
    providerType?: string;
    message: string;
} {
    const providerType = params.get("type") ?? params.get("provider") ?? defaultProviderType ?? undefined;

    const statusParam = (params.get("status") ?? "").toLowerCase();
    const okParam = (params.get("ok") ?? params.get("success") ?? "").toLowerCase();
    const error = params.get("error") ?? params.get("error_description") ?? params.get("message") ?? "";

    const ok =
        okParam === "1" ||
        okParam === "true" ||
        okParam === "yes" ||
        statusParam === "success" ||
        statusParam === "ok" ||
        (statusParam === "" && okParam === "" && error === "");

    if (ok) {
        return {
            ok: true,
            providerType,
            message: providerType ? `${providerType} connected successfully.` : "Connector connected successfully.",
        };
    }

    const cleaned = error.trim().length > 0 ? error.trim() : "Authorization failed. Please try again.";
    return { ok: false, providerType, message: cleaned };
}

export function formatLastSync(value: string | null | undefined) {
    if (!value) return "—";
    const ms = Date.parse(value);
    if (!Number.isFinite(ms)) return value;
    return new Date(ms).toLocaleString();
}
