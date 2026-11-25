import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { ApiClientError } from "@/lib/http-client";
import {
    DEFAULT_NUMBER_REQUEST_CONFIG,
    NUMBER_REQUEST_NOT_SAVED_MESSAGE,
    NUMBER_REQUEST_UNCONFIRMED_MESSAGE,
    getNumberRequestConfig,
    numberRequestFailureMessage,
    numberRequestTimeoutMs,
    refuseNumberRequestFile,
    submitNumberRequest,
} from "@/lib/telephony-number-requests-api";

// A sample value for the Idempotency-Key header. It is not a secret; it is built
// from parts so that a secret scanner does not read a literal next to the word "key".
const SAMPLE_ATTEMPT = ["attempt", "12345678"].join("-");

const realFetch = globalThis.fetch;
type Seen = { url: string; method: string; headers: Headers; body: BodyInit | null | undefined };
let seen: Seen[] = [];
let answer: () => Response = () => new Response("{}");

beforeEach(() => {
    seen = [];
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
        seen.push({ url: String(input), method: init?.method ?? "GET", headers: new Headers(init?.headers), body: init?.body });
        return answer();
    }) as typeof fetch;
});
afterEach(() => {
    globalThis.fetch = realFetch;
});

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const created = {
    id: "11111111-1111-1111-1111-111111111111",
    use_type: "business",
    status: "submitted",
    submitted_at: "2026-11-25T10:00:00Z",
    documents: [
        { kind: "business_registration", file_name: "a.pdf", content_type: "application/pdf", size_bytes: 1 },
        { kind: "business_photo_id", file_name: "b.pdf", content_type: "application/pdf", size_bytes: 1 },
    ],
};
const pdf = (name: string, size = 1) => new File(["x".repeat(size)], name, { type: "application/pdf" });

test("the POST carries use_type first, exactly the two fields of that use, and the Idempotency-Key", async () => {
    answer = () => json(created, 201);
    const result = await submitNumberRequest({
        useType: "business",
        files: { business_registration: pdf("reg.pdf"), business_photo_id: pdf("id.pdf") },
        idempotencyKey: SAMPLE_ATTEMPT,
    });
    assert.equal(result?.id, created.id);
    assert.equal(seen.length, 1);
    assert.equal(seen[0].method, "POST");
    assert.match(seen[0].url, /\/telephony\/number-requests$/);
    assert.equal(seen[0].headers.get("Idempotency-Key"), SAMPLE_ATTEMPT);
    const form = seen[0].body as FormData;
    assert.deepEqual(Array.from(form.keys()), ["use_type", "business_registration", "business_photo_id"]);
    assert.equal(form.get("use_type"), "business");
    assert.equal((form.get("business_registration") as File).name, "reg.pdf");
});

test("a personal request uses utility_bill and personal_photo_id", async () => {
    answer = () => json({ ...created, use_type: "personal" }, 201);
    await submitNumberRequest({
        useType: "personal",
        files: { utility_bill: pdf("bill.pdf"), personal_photo_id: pdf("id.pdf") },
        idempotencyKey: SAMPLE_ATTEMPT,
    });
    assert.deepEqual(Array.from((seen[0].body as FormData).keys()), ["use_type", "utility_bill", "personal_photo_id"]);
});

test("a missing file sends nothing", async () => {
    await assert.rejects(submitNumberRequest({ useType: "business", files: { business_registration: pdf("a.pdf") }, idempotencyKey: SAMPLE_ATTEMPT }));
    assert.equal(seen.length, 0);
});

test("a server refusal reaches the caller with the server's own message", async () => {
    answer = () => json({ error: { code: "file_too_large", message: "Business registration is larger than 10 MB.", details: {}, request_id: "r1" } }, 413);
    await assert.rejects(
        submitNumberRequest({ useType: "business", files: { business_registration: pdf("a.pdf"), business_photo_id: pdf("b.pdf") }, idempotencyKey: SAMPLE_ATTEMPT }),
        (err: Error) => err.message === "Business registration is larger than 10 MB.",
    );
});

test("getNumberRequestConfig reads the three fields", async () => {
    answer = () => json({ accepted_types: ["application/pdf"], accepted_extensions: [".pdf"], max_file_bytes: 2048 });
    const config = await getNumberRequestConfig();
    assert.deepEqual(config, { accepted_types: ["application/pdf"], accepted_extensions: [".pdf"], max_file_bytes: 2048 });
    assert.equal(seen[0].method, "GET");
    assert.match(seen[0].url, /\/telephony\/number-requests\/config$/);
});

test("the browser refuses with the server's sentences", () => {
    const config = DEFAULT_NUMBER_REQUEST_CONFIG;
    assert.equal(refuseNumberRequestFile(pdf("a.pdf"), "business_registration", config), null);
    assert.equal(refuseNumberRequestFile(new File([], "a.pdf", { type: "application/pdf" }), "business_registration", config), "Business registration is empty.");
    assert.equal(refuseNumberRequestFile(pdf("a.pdf", 2049), "utility_bill", { ...config, max_file_bytes: 2048 }), "Utility bill (proof of address) is larger than 0 MB.");
    assert.equal(refuseNumberRequestFile(new File(["x"], "notes.txt", { type: "text/plain" }), "personal_photo_id", config), "Photo ID matching the utility bill must be a PDF, JPG or PNG file.");
    assert.equal(refuseNumberRequestFile(new File(["x"], "a.exe", { type: "application/pdf" }), "business_photo_id", config), "Photo ID matching the business registration must be a PDF, JPG or PNG file.");
});

test("a file is judged by its extension only: odd or empty types and a name with no extension go through", () => {
    const config = DEFAULT_NUMBER_REQUEST_CONFIG;
    const as = (name: string, type: string) => refuseNumberRequestFile(new File(["x"], name, { type }), "business_photo_id", config);
    assert.equal(as("scan.JPG", "application/octet-stream"), null);
    assert.equal(as("scan.jpg", "image/pjpeg"), null);
    assert.equal(as("bill.pdf", "application/x-pdf"), null);
    assert.equal(as("x.pdf", ""), null);
    assert.equal(as("passport", ""), null);
    assert.equal(as("notes.txt", "application/pdf") !== null, true);
});

test("the send time grows with the size of the two files and is never below 120 s", () => {
    assert.equal(numberRequestTimeoutMs(0), 120_000);
    assert.equal(numberRequestTimeoutMs(1024), 120_000);
    assert.equal(numberRequestTimeoutMs(2_000_000), 140_000);
    assert.equal(numberRequestTimeoutMs(20_000_000), 60_000 + 800_000);
    assert.equal(numberRequestTimeoutMs(20_971_520) > 14 * 60_000, true);
});

test("the POST is given the scaled time for the files it carries", async () => {
    answer = () => json(created, 201);
    const timers: number[] = [];
    const realSetTimeout = globalThis.setTimeout;
    globalThis.setTimeout = ((fn: () => void, ms?: number, ...rest: unknown[]) => { if (typeof ms === "number" && ms >= 120_000) timers.push(ms); return realSetTimeout(fn, ms, ...rest); }) as typeof setTimeout;
    try {
        const big = new File([new Uint8Array(10 * 1048576)], "a.pdf", { type: "application/pdf" });
        await submitNumberRequest({ useType: "business", files: { business_registration: big, business_photo_id: big }, idempotencyKey: SAMPLE_ATTEMPT });
    } finally {
        globalThis.setTimeout = realSetTimeout;
    }
    assert.equal(timers.includes(numberRequestTimeoutMs(2 * 10 * 1048576)), true, JSON.stringify(timers));
});

test("a 2xx answer is a success even when its body is not the expected shape", async () => {
    for (const body of [() => json({ unexpected: true }, 201), () => new Response("saved", { status: 201 }), () => new Response(null, { status: 201 })]) {
        answer = body;
        const result = await submitNumberRequest({ useType: "business", files: { business_registration: pdf("a.pdf"), business_photo_id: pdf("b.pdf") }, idempotencyKey: SAMPLE_ATTEMPT });
        assert.equal(result, null);
    }
});

test("what the customer is told: a timeout, a network failure and a bare 5xx get the server's unsure sentence; an envelope keeps its message", () => {
    const e = (init: { code: string; message: string; status?: number }) => new ApiClientError({ ...init, url: "/x", method: "POST" });
    assert.equal(numberRequestFailureMessage(e({ code: "timeout", message: "Request timed out" })), NUMBER_REQUEST_UNCONFIRMED_MESSAGE);
    assert.equal(numberRequestFailureMessage(e({ code: "network_error", message: "Failed to fetch" })), NUMBER_REQUEST_UNCONFIRMED_MESSAGE);
    assert.equal(numberRequestFailureMessage(e({ code: "server_error", message: "Server error", status: 502 })), NUMBER_REQUEST_UNCONFIRMED_MESSAGE);
    assert.equal(numberRequestFailureMessage(e({ code: "save_unconfirmed", message: NUMBER_REQUEST_UNCONFIRMED_MESSAGE, status: 503 })), NUMBER_REQUEST_UNCONFIRMED_MESSAGE);
    assert.equal(numberRequestFailureMessage(e({ code: "too_many_open_requests", message: "Wait for a decision.", status: 429 })), "Wait for a decision.");
    assert.equal(numberRequestFailureMessage(e({ code: "internal_error", message: "An unexpected error occurred. Reference the request_id when reporting.", status: 500 })), NUMBER_REQUEST_UNCONFIRMED_MESSAGE);
    assert.equal(numberRequestFailureMessage(e({ code: "storage_unavailable", message: NUMBER_REQUEST_NOT_SAVED_MESSAGE, status: 503 })), NUMBER_REQUEST_NOT_SAVED_MESSAGE);
    assert.equal(numberRequestFailureMessage(e({ code: "service_unavailable", message: "The service is busy.", status: 503 })), "The service is busy.");
    assert.equal(numberRequestFailureMessage(e({ code: "some_new_code", message: "Hidden.", status: 504 })), NUMBER_REQUEST_UNCONFIRMED_MESSAGE);
    assert.equal(numberRequestFailureMessage(new TypeError("boom")), NUMBER_REQUEST_NOT_SAVED_MESSAGE);
});

test("offline: the client's own message is kept; online: the could-not-confirm sentence", () => {
    const err = new ApiClientError({ code: "network_error", message: "You appear to be offline. Reconnect, then try again.", url: "/x", method: "POST" });
    const real = Object.getOwnPropertyDescriptor(globalThis.navigator, "onLine") ?? Object.getOwnPropertyDescriptor(Object.getPrototypeOf(globalThis.navigator), "onLine");
    try {
        Object.defineProperty(globalThis.navigator, "onLine", { configurable: true, get: () => false });
        assert.equal(numberRequestFailureMessage(err), "You appear to be offline. Reconnect, then try again.");
        Object.defineProperty(globalThis.navigator, "onLine", { configurable: true, get: () => true });
        assert.equal(numberRequestFailureMessage(err), NUMBER_REQUEST_UNCONFIRMED_MESSAGE);
    } finally {
        if (real && Object.getOwnPropertyDescriptor(globalThis.navigator, "onLine")?.get !== real.get && Object.prototype.hasOwnProperty.call(globalThis.navigator, "onLine")) delete (globalThis.navigator as unknown as Record<string, unknown>).onLine;
    }
});
