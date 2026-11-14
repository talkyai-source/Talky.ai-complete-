export type ConnectorProviderType = "calendar" | "email" | "crm" | "drive" | "salesforce";

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

export const CONNECTOR_CHECKING_TEXT = "Checking status…";
export const CONNECTOR_RETURN_FAILED_TEXT = "The connection couldn't be completed. Try again.";
export const CONNECTOR_POPUP_CLOSED_TEXT = "The connection window was closed before finishing. Nothing was changed.";
export const CONNECTOR_EXPIRED_TEXT = "Credentials have expired. Reconnect to refresh access.";

const OAUTH_ERROR_SENTENCES: Record<string, string> = {
    access_denied: "Access wasn't granted. Try again and allow access.",
    invalid_state: "This connection attempt expired. Start again from Connectors.",
    authorization_changed: "This connection attempt expired. Start again from Connectors.",
    connector_mismatch: "This connection attempt expired. Start again from Connectors.",
    connector_not_found: "This connection attempt expired. Start again from Connectors.",
    missing_code: "This connection attempt expired. Start again from Connectors.",
    insufficient_scope: "Not all permissions were granted. Reconnect and allow everything requested.",
    missing_refresh_token: "Not all permissions were granted. Reconnect and allow everything requested.",
};

// The code is echoed from the return URL, which anyone can write. Only a plain
// code-shaped value is shown; anything else gets the sentence without brackets.
const ECHOABLE_OAUTH_CODE = /^[A-Za-z0-9_.-]{1,64}$/;

export function connectorOAuthErrorSentence(code: string | null | undefined): string {
    const cleaned = (code ?? "").trim();
    const known = Object.hasOwn(OAUTH_ERROR_SENTENCES, cleaned) ? OAUTH_ERROR_SENTENCES[cleaned] : undefined;
    if (known) return known;
    if (!ECHOABLE_OAUTH_CODE.test(cleaned)) return CONNECTOR_RETURN_FAILED_TEXT;
    return `${CONNECTOR_RETURN_FAILED_TEXT} (${cleaned})`;
}

/** The connector names the Connectors page shows on its cards. */
export const CONNECTOR_NAMES: Record<string, string> = {
    calendar: "Google Calendar",
    email: "Gmail",
    crm: "HubSpot",
    drive: "Google Drive",
    salesforce: "Salesforce",
};

export function parseConnectorsCallback(
    params: URLSearchParams,
    defaultProviderType?: string
): {
    ok: boolean;
    providerType?: string;
    message: string;
} {
    const providerType = params.get("type") ?? params.get("provider") ?? defaultProviderType ?? undefined;

    // The backend always sends `status` (success | error). Only its "success"
    // is a success; its "error" carries a code. Anything else did not come from
    // the backend's return and gets the plain sentence.
    const statusParam = (params.get("status") ?? "").toLowerCase();

    if (statusParam === "success") {
        return {
            ok: true,
            providerType,
            message: providerType ? `${CONNECTOR_NAMES[providerType] ?? providerType} connected successfully.` : "Connector connected successfully.",
        };
    }

    if (statusParam === "error") {
        return { ok: false, providerType, message: connectorOAuthErrorSentence(params.get("error")) };
    }

    return { ok: false, providerType, message: CONNECTOR_RETURN_FAILED_TEXT };
}

/** A return from the OAuth window, as relayed to the cards by the Connectors page. */
export type ConnectorOAuthReturn = {
    id: number;
    ok: boolean;
    providerType?: string;
    message: string;
};

/**
 * Calls `onClosedEarly` once when the OAuth window is closed while it was
 * still on the provider's pages. Returns a function that stops watching.
 *
 * `closed` alone is not proof: when the opener page is served with
 * `Cross-Origin-Opener-Policy: same-origin`, the handle reads `closed` as soon
 * as the window reaches the provider, while it is still open. So a close only
 * counts after the handle was seen open on a cross-origin page (reading its
 * location throws), and not once it is back on this origin (the return page).
 */
export function watchOAuthPopup(win: Window, onClosedEarly: () => void, intervalMs = 500): () => void {
    let seenOnProvider = false;
    let backOnThisOrigin = false;
    const id = setInterval(() => {
        if (win.closed) {
            clearInterval(id);
            if (seenOnProvider && !backOnThisOrigin) onClosedEarly();
            return;
        }
        try {
            const href = win.location.href;
            if (seenOnProvider && href !== "about:blank") backOnThisOrigin = true;
        } catch {
            seenOnProvider = true;
        }
    }, intervalMs);
    return () => clearInterval(id);
}

export function formatLastSync(value: string | null | undefined) {
    if (!value) return "—";
    const ms = Date.parse(value);
    if (!Number.isFinite(ms)) return value;
    return new Date(ms).toLocaleString();
}
