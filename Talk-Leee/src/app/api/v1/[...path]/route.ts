import { NextResponse } from "next/server";

type RouteContext = { params: Promise<{ path?: string[] }> };

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const HOP_BY_HOP_HEADERS = new Set([
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
]);

function jsonError(message: string, status: number) {
    return NextResponse.json(
        { error: message },
        {
            status,
            headers: { "cache-control": "no-store" },
        },
    );
}

function resolveBackendBaseUrl() {
    const raw = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
    if (!raw) return null;
    try {
        return new URL(raw);
    } catch {
        return null;
    }
}

function buildTargetUrl(baseUrl: URL, request: Request, segments: string[]) {
    const target = new URL(baseUrl.toString());
    const basePath = target.pathname.replace(/\/+$/, "");
    const segmentPath = segments.join("/");
    target.pathname = `${basePath}/${segmentPath}`.replace(/\/{2,}/g, "/");

    const requestUrl = new URL(request.url);
    target.search = requestUrl.search;
    return target;
}

function copyRequestHeaders(request: Request) {
    const headers = new Headers();
    request.headers.forEach((value, key) => {
        const normalized = key.toLowerCase();
        if (HOP_BY_HOP_HEADERS.has(normalized)) return;
        headers.set(key, value);
    });
    return headers;
}

function copyResponseHeaders(upstream: Response) {
    const headers = new Headers();
    upstream.headers.forEach((value, key) => {
        const normalized = key.toLowerCase();
        if (HOP_BY_HOP_HEADERS.has(normalized)) return;
        headers.set(key, value);
    });
    headers.set("cache-control", "no-store");
    return headers;
}

async function proxyRequest(request: Request, segments: string[]) {
    const baseUrl = resolveBackendBaseUrl();
    if (!baseUrl) {
        return jsonError("NEXT_PUBLIC_API_BASE_URL is missing or invalid.", 500);
    }

    const targetUrl = buildTargetUrl(baseUrl, request, segments);

    let body: ArrayBuffer | undefined;
    const method = request.method.toUpperCase();
    if (!["GET", "HEAD"].includes(method)) {
        body = await request.arrayBuffer();
    }

    let upstream: Response;
    try {
        upstream = await fetch(targetUrl, {
            method,
            headers: copyRequestHeaders(request),
            body,
            redirect: "manual",
        });
    } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to reach backend API.";
        return jsonError(message, 502);
    }

    return new NextResponse(upstream.body, {
        status: upstream.status,
        headers: copyResponseHeaders(upstream),
    });
}

export async function GET(request: Request, ctx: RouteContext) {
    const { path } = await ctx.params;
    return proxyRequest(request, path ?? []);
}

export async function POST(request: Request, ctx: RouteContext) {
    const { path } = await ctx.params;
    return proxyRequest(request, path ?? []);
}

export async function PUT(request: Request, ctx: RouteContext) {
    const { path } = await ctx.params;
    return proxyRequest(request, path ?? []);
}

export async function PATCH(request: Request, ctx: RouteContext) {
    const { path } = await ctx.params;
    return proxyRequest(request, path ?? []);
}

export async function DELETE(request: Request, ctx: RouteContext) {
    const { path } = await ctx.params;
    return proxyRequest(request, path ?? []);
}

export async function OPTIONS(request: Request, ctx: RouteContext) {
    const { path } = await ctx.params;
    return proxyRequest(request, path ?? []);
}
