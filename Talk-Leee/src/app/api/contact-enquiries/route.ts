import { contactReceiptSchema, contactRequestSchema } from "@/lib/contact-enquiries";

const json = (body: unknown, status: number, extraHeaders: Record<string, string> = {}) =>
  Response.json(body, { status, headers: { "Cache-Control": "no-store", ...extraHeaders } });

export async function POST(request: Request): Promise<Response> {
  // Self-hosted Next can expose an internal request.url behind TLS termination.
  // Trust an explicit deployment origin, never Host/X-Forwarded-Host supplied
  // with the request. It must also be allowed by backend CSRF configuration.
  let origin: string;
  try {
    const configured = new URL(process.env.CONTACT_ENQUIRY_PUBLIC_ORIGIN ?? "");
    if (!["https:", "http:"].includes(configured.protocol) || configured.username || configured.password || configured.pathname !== "/" || configured.search || configured.hash) throw new Error("Invalid origin");
    origin = configured.origin;
  } catch { return json({ error: "Contact intake is unavailable" }, 503); }
  if (request.headers.get("origin") !== origin) return json({ error: "Invalid request origin" }, 403);
  if (request.headers.get("content-type")?.split(";")[0]?.trim().toLowerCase() !== "application/json") {
    return json({ error: "Expected JSON" }, 415);
  }
  // The only upstream is deployment configuration, never a caller URL/header.
  let target: URL;
  try {
    const base = new URL(process.env.NEXT_PUBLIC_API_BASE_URL ?? "");
    if (!["https:", "http:"].includes(base.protocol) || base.username || base.password || base.search || base.hash || base.host === new URL(origin).host || base.host === new URL(request.url).host) throw new Error("Invalid backend");
    if (base.pathname.replace(/\/+$/, "") !== "/api/v1") throw new Error("Expected API base");
    target = new URL(`${base.href.replace(/\/+$/, "")}/public/contact-enquiries`);
  } catch { return json({ error: "Contact intake is unavailable" }, 503); }
  let payload;
  try {
    if (Number(request.headers.get("content-length") ?? 0) > 12_000) return json({ error: "Request too large" }, 413);
    const reader = request.body?.getReader();
    if (!reader) return json({ error: "Missing request body" }, 400);
    const chunks: Uint8Array[] = [];
    let length = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > 12_000) { await reader.cancel(); return json({ error: "Request too large" }, 413); }
      chunks.push(value);
    }
    const body = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
    const parsed = contactRequestSchema.safeParse(JSON.parse(new TextDecoder().decode(body)));
    if (!parsed.success) return json({ error: "Invalid contact details" }, 422);
    payload = parsed.data;
  } catch { return json({ error: "Invalid request body" }, 400); }

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
      return json({ error: "Receipt could not be verified" }, 502);
    }
    if ([409, 422, 429].includes(upstream.status)) {
      const retryAfter = upstream.headers.get("retry-after");
      return json({ error: "Contact request was not accepted" }, upstream.status,
        upstream.status === 429 && retryAfter && /^\d{1,6}$/.test(retryAfter) ? { "Retry-After": retryAfter } : {});
    }
    return json({ error: "Contact intake is unavailable" }, 503);
  } catch { return json({ error: "Receipt could not be confirmed" }, 503); }
  finally { clearTimeout(timeout); }
}
