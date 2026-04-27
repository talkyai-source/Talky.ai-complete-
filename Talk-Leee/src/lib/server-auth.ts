import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { authTokenCookieName } from "@/lib/auth-token";
import { apiBaseUrl } from "@/lib/env";

export const WHITE_LABEL_ADMIN_ROLE = "white_label_admin";
export const WHITE_LABEL_DASHBOARD_PATH = "/white-label/dashboard";

export type ServerMe = {
    id: string;
    email: string;
    name?: string;
    business_name?: string;
    role: string;
    minutes_remaining?: number;
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
    const token = (await cookies()).get(authTokenCookieName())?.value;
    if (!token || token.trim().length === 0) return null;

    const baseUrl = apiBaseUrl().replace(/\/+$/, "");
    const endpoints = [`${baseUrl}/auth/me`, `${baseUrl}/me`];

    for (const url of endpoints) {
        try {
            const res = await fetch(url, {
                method: "GET",
                headers: {
                    cookie: `${authTokenCookieName()}=${encodeURIComponent(token)}`,
                    accept: "application/json",
                    "x-talklee-mw-internal": "1",
                },
                cache: "no-store",
            });
            if (!res.ok) continue;
            const data = (await res.json().catch(() => null)) as unknown;
            if (!data || typeof data !== "object") continue;
            const role = (data as { role?: unknown }).role;
            if (typeof role !== "string" || role.trim().length === 0) continue;
            return data as ServerMe;
        } catch {
        }
    }

    return null;
}

export async function requireServerMe(input: { redirectTo: string }) {
    const me = await getServerMe();
    if (me) return me;
    redirect(input.redirectTo);
}
