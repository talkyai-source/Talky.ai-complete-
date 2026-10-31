import type { PageContext } from "@/lib/support-api";

/*
 * What a new support conversation shares about where it was opened: the
 * route, a campaign or call id when the URL carries one, the app version and
 * the viewport. The server drops ids the tenant does not own.
 */

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function supportPageContext(input: { pathname: string | null; commitSha?: string; viewport?: { width: number; height: number } }): PageContext {
    const context: PageContext = {};
    const pathname = (input.pathname ?? "").split("?")[0]!.split("#")[0]!;
    if (pathname.startsWith("/") && pathname.length <= 200) context.route = pathname;
    const segments = pathname.split("/").filter(Boolean);
    const after = (name: string) => {
        const index = segments.indexOf(name);
        const candidate = index >= 0 ? segments[index + 1] : undefined;
        return candidate && UUID.test(candidate) ? candidate.toLowerCase() : undefined;
    };
    const campaignId = after("campaigns");
    if (campaignId) context.campaign_id = campaignId;
    const callId = after("calls");
    if (callId) context.call_id = callId;
    if (input.commitSha && /^[A-Za-z0-9._+-]{1,40}$/.test(input.commitSha)) context.app_version = input.commitSha;
    if (input.viewport && input.viewport.width > 0 && input.viewport.height > 0) {
        context.viewport = `${Math.round(input.viewport.width)}x${Math.round(input.viewport.height)}`;
    }
    return context;
}

/** The line shown above the chat: "Includes: this page, campaign, app version, screen size". */
export function describePageContext(context: PageContext): string {
    const parts: string[] = [];
    if (context.route) parts.push("this page");
    if (context.campaign_id) parts.push("the campaign you are viewing");
    if (context.call_id) parts.push("the call you are viewing");
    if (context.app_version) parts.push("the app version");
    if (context.viewport) parts.push("your screen size");
    return parts.length ? `Includes: ${parts.join(", ")}.` : "Includes: nothing about this page.";
}
