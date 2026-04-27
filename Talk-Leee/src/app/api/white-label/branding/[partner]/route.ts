import { NextResponse } from "next/server";
import { getWhiteLabelBranding } from "@/lib/white-label/branding";
import { enforceMultiLevelRateLimit } from "@/server/api-security";

type RouteContext = { params: Promise<{ partner: string }> };

export const dynamic = "force-dynamic";

export async function GET(req: Request, ctx: RouteContext) {
    const rate = await enforceMultiLevelRateLimit({ request: req, tier: "default", path: "/api/white-label/branding/[partner]", method: "GET" });
    if (!rate.ok) return NextResponse.json({ detail: "Too many requests" }, { status: 429, headers: rate.headers });

    const { partner } = await ctx.params;
    const branding = getWhiteLabelBranding(partner);
    if (!branding) {
        return NextResponse.json({ error: "Unknown partner" }, { status: 404, headers: { "cache-control": "no-store" } });
    }

    return NextResponse.json(branding, { status: 200, headers: { "cache-control": "no-store", ...rate.headers } });
}
