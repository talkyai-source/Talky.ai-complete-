import { contactReceiptSchema, contactRequestSchema } from "@/lib/contact-enquiries";

const json = (body: unknown, status: number, extraHeaders: Record<string, string> = {}) =>
  Response.json(body, { status, headers: { "Cache-Control": "no-store", ...extraHeaders } });

// One server-side line per failed request, so an operator can tell the causes
// apart: which check failed, the upstream status where there is one, how long
// the request took, and the request reference the visitor sees on screen.
// This codebase has no server logger (lib/monitoring.ts only forwards security
// events to Sentry), so this is console.error, which reaches the service
// journal. Never logs the visitor's details or any environment value.
type Failure = { reason: string; status: number; upstreamStatus?: number; requestId?: string; requestOrigin?: string | null; error?: unknown };
function logFailure(startedAt: number, f: Failure): void {
  const parts = [
    `reason=${f.reason}`,
    `status=${f.status}`,
    `upstream_status=${f.upstreamStatus ?? "-"}`,
    `duration_ms=${Math.round(performance.now() - startedAt)}`,
    `request_id=${f.requestId ?? "-"}`,
  ];
  if (f.requestOrigin !== undefined) {
    parts.push(`request_origin=${f.requestOrigin === null ? "-" : f.requestOrigin === "" ? "<empty>" : /^[\w.:/-]{1,200}$/.test(f.requestOrigin) ? f.requestOrigin : "<unprintable>"}`);
  }
  if (f.error !== undefined) {
    const e = f.error as { name?: unknown; cause?: { code?: unknown } } | null;
    parts.push(`error=${typeof e?.name === "string" ? e.name : typeof f.error}`, `error_code=${typeof e?.cause?.code === "string" ? e.cause.code : "-"}`);
  }
  console.error(`[contact-enquiries] ${parts.join(" ")}`);
}

export async function POST(request: Request): Promise<Response> {
  const startedAt = performance.now();
  // Every non-success response goes through here so it is logged. The body's
  // `outcome` tells the browser whether the message was never forwarded
  // ("not_sent") or whether its fate is unknown ("unconfirmed").
  const fail = (body: Record<string, unknown>, status: number, f: Omit<Failure, "status">, extraHeaders: Record<string, string> = {}) => {
    logFailure(startedAt, { ...f, status });
    return json(body, status, extraHeaders);
  };
  // Self-hosted Next can expose an internal request.url behind TLS termination.
  // Trust an explicit deployment origin, never Host/X-Forwarded-Host supplied
  // with the request. It must also be allowed by backend CSRF configuration.
  let origin: string;
  const configuredOrigin = process.env.CONTACT_ENQUIRY_PUBLIC_ORIGIN;
  try {
    const configured = new URL(configuredOrigin ?? "");
    if (!["https:", "http:"].includes(configured.protocol) || configured.username || configured.password || configured.pathname !== "/" || configured.search || configured.hash) throw new Error("Invalid origin");
    origin = configured.origin;
  } catch { return fail({ error: "Contact intake is unavailable", outcome: "not_sent" }, 503, { reason: configuredOrigin ? "public_origin_invalid" : "public_origin_missing" }); }
  const requestOrigin = request.headers.get("origin");
  if (requestOrigin !== origin) return fail({ error: "Invalid request origin", outcome: "not_sent" }, 403, { reason: "origin_mismatch", requestOrigin });
  if (request.headers.get("content-type")?.split(";")[0]?.trim().toLowerCase() !== "application/json") {
    return fail({ error: "Expected JSON", outcome: "not_sent" }, 415, { reason: "content_type" });
  }
  // The only upstream is deployment configuration, never a caller URL/header.
  let target: URL;
  const configuredBase = process.env.NEXT_PUBLIC_API_BASE_URL;
  try {
    const base = new URL(configuredBase ?? "");
    if (!["https:", "http:"].includes(base.protocol) || base.username || base.password || base.search || base.hash || base.host === new URL(origin).host || base.host === new URL(request.url).host) throw new Error("Invalid backend");
    if (base.pathname.replace(/\/+$/, "") !== "/api/v1") throw new Error("Expected API base");
    target = new URL(`${base.href.replace(/\/+$/, "")}/public/contact-enquiries`);
  } catch { return fail({ error: "Contact intake is unavailable", outcome: "not_sent" }, 503, { reason: configuredBase ? "api_base_invalid" : "api_base_missing" }); }
  let payload;
  try {
    if (Number(request.headers.get("content-length") ?? 0) > 12_000) return fail({ error: "Request too large", outcome: "not_sent" }, 413, { reason: "body_too_large" });
    const reader = request.body?.getReader();
    if (!reader) return fail({ error: "Missing request body", outcome: "not_sent" }, 400, { reason: "body_missing" });
    const chunks: Uint8Array[] = [];
    let length = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > 12_000) { await reader.cancel(); return fail({ error: "Request too large", outcome: "not_sent" }, 413, { reason: "body_too_large" }); }
      chunks.push(value);
    }
    const body = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
    const parsed = contactRequestSchema.safeParse(JSON.parse(new TextDecoder().decode(body)));
    if (!parsed.success) return fail({ error: "Invalid contact details", outcome: "not_sent" }, 422, { reason: "body_invalid" });
    payload = parsed.data;
  } catch { return fail({ error: "Invalid request body", outcome: "not_sent" }, 400, { reason: "body_unreadable" }); }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12_000);
  try {
    const upstream = await fetch(target, {
      method: "POST", headers: { "Content-Type": "application/json", Origin: origin },
      body: JSON.stringify(payload), credentials: "omit", redirect: "error", cache: "no-store", signal: controller.signal,
    });
    if (upstream.status === 200 || upstream.status === 201) {
      const receipt = contactReceiptSchema.safeParse(await upstream.json());
      if (receipt.success && receipt.data.receipt_id === payload.request_id) return json(receipt.data, upstream.status);
      return fail({ error: "Receipt could not be verified", outcome: "unconfirmed" }, 502, { reason: "receipt_invalid", upstreamStatus: upstream.status, requestId: payload.request_id });
    }
    if ([409, 422, 429].includes(upstream.status)) {
      const retryAfter = upstream.headers.get("retry-after");
      return fail({ error: "Contact request was not accepted" }, upstream.status, { reason: "upstream_rejected", upstreamStatus: upstream.status, requestId: payload.request_id },
        upstream.status === 429 && retryAfter && /^\d{1,6}$/.test(retryAfter) ? { "Retry-After": retryAfter } : {});
    }
    return fail({ error: "Contact intake is unavailable", outcome: "unconfirmed" }, 503, { reason: "upstream_status", upstreamStatus: upstream.status, requestId: payload.request_id });
  } catch (error) {
    return fail({ error: "Receipt could not be confirmed", outcome: "unconfirmed" }, 503, { reason: controller.signal.aborted ? "upstream_timeout" : "upstream_error", requestId: payload.request_id, error });
  }
  finally { clearTimeout(timeout); }
}
