import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";

export const WHITE_LABEL_ADMIN_ROLE = "white_label_admin";
export const WHITE_LABEL_DASHBOARD_PATH = "/white-label/dashboard";

export type ServerMe = {
    id: string;
    email: string;
    name?: string;
    business_name?: string;
    role: string;
    minutes_remaining?: number | null;
    minutes_state?: "known" | "unlimited" | "unavailable";
};

function isLocalHostHostHeader(hostHeader: string | null) {
    if (!hostHeader) return false;
    const host = hostHeader.split(":")[0]?.trim().toLowerCase();
    return host === "localhost" || host === "127.0.0.1" || host === "[::1]";
}

export async function shouldBypassAuthOnThisRequest() {
    if (process.env.NODE_ENV === "production") return false;
    if (process.env.TALKLEE_REQUIRE_AUTH === "1") return false;
    const hostHeader = (await headers()).get("host");
    return isLocalHostHostHeader(hostHeader);
}

export async function getServerMe(): Promise<ServerMe | null> {
    const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
    if (!configured) return null;
    const store = await cookies();
    const cookieHeader = ["talky_at", "talky_sid"]
        .map((name) => ({ name, value: store.get(name)?.value }))
        .filter(({ value }) => value && value.trim().length > 0)
        .map(({ name, value }) => `${name}=${encodeURIComponent(value!)}`)
        .join("; ");
    if (!cookieHeader) return null;

    try {
        const res = await fetch(`${configured.replace(/\/+$/, "")}/auth/me`, {
            method: "GET",
            headers: {
                cookie: cookieHeader,
                accept: "application/json",
                "x-talklee-mw-internal": "1",
            },
            cache: "no-store",
        });
        if (!res.ok) return null;
        const data = (await res.json().catch(() => null)) as unknown;
        if (!data || typeof data !== "object") return null;
        const me = data as Partial<ServerMe>;
        if (![me.id, me.email, me.role].every((value) => typeof value === "string" && value.trim().length > 0)) return null;
        return me as ServerMe;
    } catch {
        return null;
    }
}

export async function requireServerMe(input: { redirectTo: string }) {
    const me = await getServerMe();
    if (me) return me;
    redirect(input.redirectTo);
}
