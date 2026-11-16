import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { SENTRY_SCRUB_OPTIONS, scrubSentryBreadcrumb, scrubSentryEvent, scrubSentryTransaction } from "@/lib/sentry-scrub";
import type { Breadcrumb, Event as SentryEvent } from "@sentry/nextjs";

// TransactionEvent is not exported by @sentry/nextjs; this is its shape.
type TransactionEvent = SentryEvent & { type: "transaction" };

type Bag = Record<string, unknown>;

function scrub(event: Bag): Bag {
    const out = scrubSentryEvent(event as SentryEvent);
    assert.ok(out, "event was dropped");
    return out as unknown as Bag;
}

function msg(text: string): string {
    return scrub({ event_id: "e1", message: text }).message as string;
}

test("patterns are scrubbed", () => {
    const cases: Array<[string, string, string]> = [
        ["call to +442071234567 failed", "442071234567", "[phone]"],
        ["dialing 020 7123 4567 now", "7123 4567", "[phone]"],
        ["dialing 07911 123456 now", "07911 123456", "[phone]"],
        ["dialing (415) 555-2671 now", "555-2671", "[phone]"],
        ["number +44 20 7123 4567 busy", "7123 4567", "[phone]"],
        ["card 4111 1111 1111 1111 declined", "4111 1111", "[phone]"],
        ["lead jane.doe+x@example.co.uk replied", "jane.doe", "[email]"],
        ["Authorization: Bearer abc.DEF-123_xyz=", "abc.DEF", "[token]"],
        ["jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.SflKxwRJSMeKKF2QT4fw ok", "eyJhbGci", "[token]"],
        ["key sk-proj-abcdefghijklmnop1234 used", "abcdefghijklmnop", "[key]"],
        ["key sk_live_51HabcdefGHIJKLmnop used", "51Habcdef", "[key]"],
        ["hook whsec_abcdefghijklmnopqrstuv bad", "abcdefghijklmnop", "[key]"],
        ["hook ghp_abcdefghijklmnopqrstuvwxyz0123 bad", "abcdefghij", "[key]"],
        ["aws AKIAIOSFODNN7EXAMPLE leaked", "IOSFODNN7", "[key]"],
        ["slack xoxb-1234567890-abcdefghijkl leaked", "1234567890-abc", "[key]"],
    ];
    for (const [text, leaked, marker] of cases) {
        const out = msg(text);
        assert.ok(!out.includes(leaked), `${text} -> ${out}`);
        assert.ok(out.includes(marker), `${text} -> ${out}`);
    }
});

test("URL credentials keep scheme and host", () => {
    const out = msg("db down postgresql://app:S3cretPw@db.internal:5432/talky");
    assert.ok(!out.includes("S3cretPw"));
    assert.ok(out.includes("postgresql://[redacted]@db.internal:5432/talky"));
});

test("URL query keeps keys, scrubs values", () => {
    const out = msg("GET /cb?token=abc123&page=2&email=a@b.com&name=bob");
    assert.ok(out.includes("token=[redacted]"));
    assert.ok(!out.includes("abc123"));
    assert.ok(out.includes("page=2"));
    assert.ok(!out.includes("a@b.com"));
    assert.ok(out.includes("name=bob"));
});

test("quoted free speech after a marker is redacted", () => {
    const texts = [
        "stt failed text='my card number is 4111' to=x",
        "stt failed text=\"it's me, call 5551234567\" to=x",
        "got transcript='hello there' ok",
        "utterance='hi' ok",
        "said='something private' ok",
        "user_text='private words' ok",
        "reply='private words' ok",
    ];
    for (const t of texts) {
        const out = msg(t);
        for (const s of ["card number", "call 5551234567", "hello there", "'hi'", "something private", "private words"]) {
            assert.ok(!out.includes(s), `${t} -> ${out}`);
        }
        assert.ok(out.includes("=[redacted]"));
    }
    assert.equal(msg("stt failed text='abc def' to=x"), "stt failed text=[redacted] to=x");
});

test("ordinary values survive unchanged", () => {
    const texts = [
        "call 123e4567-e89b-12d3-a456-426614174000 ended",
        "call 12345678-1234-1234-1234-123456789012 ended",
        "at 2026-11-16T10:20:30.123456Z done",
        "at 2026-11-16 10:20:30 done",
        "commit 3f786850e387550fdab836ed7e6dc881de23001b built",
        "status=404 for path",
        "attempt 3 of 5",
        "line 1234 in module",
        "HTTP 503 returned",
        "version 1.2.3 ok",
        "peer 192.168.100.200 connected",
        "An ordinary sentence with nothing private in it.",
        "env context=production ok",
        "go next=page2 ok",
        "subtext=abc def stays",
    ];
    for (const t of texts) assert.equal(msg(t), t);
});

test("key names are redacted at depth, case-insensitively; metadata keys survive", () => {
    const out = scrub({
        event_id: "e1",
        extra: {
            a: { b: { c: { API_KEY: "plain-secret-1", "x-api-key": "plain-secret-2", apiKey: "plain-secret-3" } } },
            Authorization: "Basic zzz",
            user_password: "pw",
            transcript: "hello i am bob",
            prompt: "you are an agent",
            messages: [{ role: "user", content: "hi" }],
            recording_url: "https://s3/x.mp3",
            session_id: "abc",
            otp: "123456",
            keep: "fine",
            max_tokens: 256,
            content_type: "application/json",
        },
    });
    const blob = JSON.stringify(out);
    for (const s of ["plain-secret-1", "plain-secret-2", "plain-secret-3", "Basic zzz", "hello i am bob", "you are an agent", "s3/x.mp3", "123456"]) {
        assert.ok(!blob.includes(s), s);
    }
    const extra = out.extra as Bag;
    const c = ((extra.a as Bag).b as Bag).c as Bag;
    assert.equal(c.API_KEY, "[redacted]");
    assert.equal(extra.messages, "[redacted]");
    assert.equal(extra.keep, "fine");
    assert.equal(extra.max_tokens, 256);
    assert.equal(extra.content_type, "application/json");
});

test("structure keys survive", () => {
    const event = {
        event_id: "e1",
        timestamp: 1763287200,
        level: "error",
        logger: "app.x",
        release: "abc123def456",
        environment: "production",
        transaction: "/api/v1/calls",
        fingerprint: ["a", "b"],
        message: "boom",
        exception: { values: [{ type: "ValueError", value: "bad", stacktrace: { frames: [{ filename: "a.js", function: "f", lineno: 12, in_app: true }] } }] },
    };
    const out = scrub(event);
    for (const k of ["event_id", "timestamp", "level", "logger", "release", "environment", "transaction", "message"]) {
        assert.equal(out[k], (event as Bag)[k], k);
    }
    assert.deepEqual(out.fingerprint, ["a", "b"]);
    const v = ((out.exception as Bag).values as Bag[])[0];
    assert.equal(v.type, "ValueError");
    assert.equal(v.value, "bad");
    assert.equal((((v.stacktrace as Bag).frames as Bag[])[0]).lineno, 12);
});

test("stack frame vars, logentry params and exception values are scrubbed", () => {
    const out = scrub({
        event_id: "e1",
        logentry: { message: "to=%s key=%s", params: ["+442071234567", "sk-abcdefghijklmnop12345"] },
        exception: {
            values: [
                {
                    type: "E",
                    value: "bad number +14155552671 for a@b.com",
                    stacktrace: { frames: [{ function: "f", vars: { phone: "+442071234567", api_key: "whatever", note: "mail me at a@b.com", n: 3 } }] },
                },
            ],
        },
    });
    const blob = JSON.stringify(out);
    assert.ok(!blob.includes("4155552671") && !blob.includes("a@b.com") && !blob.includes("abcdefghijklmnop"));
    assert.deepEqual((out.logentry as Bag).params, ["[phone]", "[key]"]);
    const vars = ((((out.exception as Bag).values as Bag[])[0].stacktrace as Bag).frames as Bag[])[0].vars as Bag;
    assert.equal(vars.phone, "[phone]");
    assert.equal(vars.api_key, "[redacted]");
    assert.equal(vars.n, 3);
});

test("request: url, query, headers allow-list; cookies and data removed", () => {
    const out = scrub({
        event_id: "e1",
        request: {
            url: "https://user:pw@app.example.com/x?token=abc&page=2",
            query_string: "token=abc&page=2&email=a@b.com",
            method: "POST",
            headers: {
                "User-Agent": "ua", "Content-Type": "application/json", Accept: "*/*", "X-Request-Id": "rid-1",
                Authorization: "Bearer abc", Cookie: "s=1", Referer: "https://x/?token=zz",
            },
            cookies: { s: "1" },
            data: { transcript: "hello" },
        },
    });
    const req = out.request as Bag;
    assert.ok(!("cookies" in req) && !("data" in req));
    assert.deepEqual(Object.keys(req.headers as Bag).map((k) => k.toLowerCase()).sort(), ["accept", "content-type", "user-agent", "x-request-id"]);
    const url = req.url as string;
    assert.ok(!url.includes("pw@") && !url.includes("token=abc") && url.includes("page=2") && url.includes("app.example.com"));
    const qs = req.query_string as string;
    assert.ok(!qs.includes("abc") && !qs.includes("a@b.com") && qs.includes("page=2"));
    assert.equal(req.method, "POST");
});

test("user keeps only id", () => {
    const out = scrub({ event_id: "e1", user: { id: "u-1", email: "a@b.com", ip_address: "1.2.3.4", username: "bob" } });
    assert.deepEqual(out.user, { id: "u-1" });
});

test("breadcrumbs: in an event and standalone (fetch/navigation urls)", () => {
    const out = scrub({
        event_id: "e1",
        breadcrumbs: [
            { category: "fetch", level: "info", data: { url: "/api/x?token=t&a=1", status_code: 404 } },
            { category: "navigation", data: { from: "/a?email=a@b.com", to: "/b?page=2" } },
        ],
    });
    const [c1, c2] = out.breadcrumbs as Bag[];
    assert.equal(c1.category, "fetch");
    assert.equal((c1.data as Bag).url, "/api/x?token=[redacted]&a=1");
    assert.equal((c1.data as Bag).status_code, 404);
    assert.ok(!JSON.stringify(c2).includes("a@b.com"));
    assert.equal((c2.data as Bag).to, "/b?page=2");

    const c3 = scrubSentryBreadcrumb({ category: "x", message: "mail a@b.com", data: { secret: "s" } } as Breadcrumb) as Bag;
    assert.equal(c3.message, "mail [email]");
    assert.equal((c3.data as Bag).secret, "[redacted]");
});

test("a breadcrumb that cannot be scrubbed is dropped", () => {
    const bad = {
        get message(): string {
            throw new Error("boom");
        },
    };
    // Object.entries invokes the getter, so scrubbing throws.
    assert.equal(scrubSentryBreadcrumb(bad as unknown as Breadcrumb), null);
    assert.equal(scrubSentryBreadcrumb("nope" as unknown as Breadcrumb), null);
});

test("depth and size caps", () => {
    const deep: Bag = {};
    let cur = deep;
    for (let i = 0; i < 40; i++) {
        const next: Bag = {};
        cur.n = next;
        cur = next;
    }
    cur.leak = "a@b.com";
    const out = scrub({
        event_id: "e1",
        extra: { deep, many: Array.from({ length: 1000 }, (_, i) => i), long: `${"x".repeat(10000)} a@b.com` },
    });
    assert.ok(!JSON.stringify(out).includes("a@b.com"));
    const extra = out.extra as Bag;
    assert.ok((extra.many as unknown[]).length <= 200);
    assert.ok((extra.long as string).length <= 2100);
    const tail = scrub({ event_id: "e1", message: `${"x".repeat(1990)} text='private words here and more private words` }).message as string;
    assert.ok(!tail.includes("private"));
});

test("an event that throws when read yields the minimal event, never the original", () => {
    const original: Bag = {
        event_id: "e1",
        timestamp: 1763287200,
        level: "error",
        release: "r1",
        environment: "prod",
        message: "secret +442071234567",
        exception: { values: [{ type: "ValueError", value: "a@b.com" }] },
        extra: { password: "p" },
    };
    Object.defineProperty(original, "extra", {
        enumerable: true,
        get() {
            throw new Error("boom");
        },
    });
    const out = scrubSentryEvent(original as SentryEvent);
    assert.notEqual(out, original);
    assert.deepEqual(out, {
        event_id: "e1",
        timestamp: 1763287200,
        level: "error",
        release: "r1",
        environment: "prod",
        exception: { values: [{ type: "ValueError" }] },
        message: "event withheld: scrubbing failed",
    });
    assert.ok(!JSON.stringify(out).includes("442071234567"));
});

test("input is not mutated", () => {
    const event = { event_id: "e1", message: "a@b.com", user: { id: "1", email: "a@b.com" } };
    scrubSentryEvent(event as SentryEvent);
    assert.equal(event.message, "a@b.com");
    assert.ok("email" in event.user);
});

test("unquoted value after a marker is redacted up to the next key", () => {
    assert.equal(msg("stt failed text=hello there to=+442071234567"), "stt failed text=[redacted] to=[phone]");
    assert.equal(msg("reply=ok my number is 07911 123456"), "reply=[redacted]");
});

function le(template: unknown, params: unknown, extra: Bag = {}): Bag {
    const out = scrub({ event_id: "e1", logentry: { message: template, params, ...extra } });
    return out.logentry as Bag;
}

test("logentry: argument filling a placeholder after a marker is redacted", () => {
    const a = le("stt failed text=%r to=%s", ["my card number is 4111", "+442071234567"]);
    assert.equal(a.message, "stt failed text=%r to=%s");
    assert.deepEqual(a.params, ["[redacted]", "[phone]"]);
    assert.deepEqual(le("x text=%(text)s to=%(to)s", { text: "hello", to: "+442071234567" }).params, { text: "[redacted]", to: "[phone]" });
    assert.deepEqual(le("100%% done text=%s", ["secret words"]).params, ["[redacted]"]);
    assert.deepEqual(le("a=%5.2f text=%-10s", [3.14159, "hello"]).params, [3.14159, "[redacted]"]);
    assert.deepEqual(le("text='%s' n=%d", ["hello", 3]).params, ["[redacted]", 3]);
    assert.deepEqual(le("text=%s", [12345678901]).params, ["[redacted]"]);
    assert.deepEqual(le("text=%s", [{ a: "b" }]).params, ["[redacted]"]);
    assert.deepEqual(le("to=%s mail=%s", ["+442071234567", "a@b.com"]).params, ["[phone]", "[email]"]);
});

test("logentry: a template that does not match the arguments redacts every string", () => {
    assert.deepEqual(le("text=%s to=%s", ["only one secret"]).params, ["[redacted]"]);
    assert.deepEqual(le("x %s", ["a b", 5]).params, ["[redacted]", 5]);
    assert.deepEqual(le("x %s", { k: "v w" }).params, { k: "[redacted]" });
    assert.deepEqual(le("x %(k)s", ["v w"]).params, ["[redacted]"]);
    assert.deepEqual(le("x %*d %s", [3, "v w"]).params, [3, "[redacted]"]);
    assert.deepEqual(le(null, ["v w"]).params, ["[redacted]"]);
});

test("logentry: formatted goes through the string rules; garbage never throws", () => {
    const out = le("text=%s to=%s", ["hi there", "+442071234567"], { formatted: "text=hi there to=+442071234567" });
    assert.equal(out.formatted, "text=[redacted] to=[phone]");
    assert.ok(scrubSentryEvent({ event_id: "e1", logentry: { message: null, params: 5 } } as unknown as SentryEvent));
});

test("a transaction that cannot be scrubbed is dropped", () => {
    const bad: Bag = { type: "transaction" };
    Object.defineProperty(bad, "extra", {
        enumerable: true,
        get() {
            throw new Error("boom");
        },
    });
    assert.equal(scrubSentryTransaction(bad as unknown as TransactionEvent), null);
    assert.equal(scrubSentryTransaction("nope" as unknown as TransactionEvent), null);
    const ok = scrubSentryTransaction({ type: "transaction", transaction: "/x", request: { url: "/a?token=t" } } as unknown as TransactionEvent) as unknown as Bag;
    assert.equal(ok.type, "transaction");
    assert.equal((ok.request as Bag).url, "/a?token=[redacted]");
});

// The real SDK is not started here: started with a DSN inside a test process it
// opens network and timer handles that can keep the process alive.
test("wiring: one object carries personal data off and the three scrub hooks", () => {
    assert.equal(SENTRY_SCRUB_OPTIONS.sendDefaultPii, false);
    assert.equal(SENTRY_SCRUB_OPTIONS.beforeSend, scrubSentryEvent);
    assert.equal(SENTRY_SCRUB_OPTIONS.beforeSendTransaction, scrubSentryTransaction);
    assert.equal(SENTRY_SCRUB_OPTIONS.beforeBreadcrumb, scrubSentryBreadcrumb);
});

// A guard over the source, not a behaviour test: every Sentry.init call in the
// two instrumentation files spreads that object, and none sets a hook by hand.
test("wiring: every Sentry.init call spreads the scrub options", () => {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const expected: Record<string, number> = { "instrumentation.ts": 2, "instrumentation-client.ts": 1 };
    for (const [file, inits] of Object.entries(expected)) {
        const source = readFileSync(path.join(here, "..", file), "utf8");
        assert.equal(source.split("Sentry.init({").length - 1, inits, `${file}: number of Sentry.init calls changed`);
        assert.equal(source.split("...SENTRY_SCRUB_OPTIONS,").length - 1, inits, `${file}: an init does not spread the scrub options`);
        for (const hook of ["beforeSend:", "beforeSendTransaction:", "beforeBreadcrumb:", "sendDefaultPii:"]) {
            assert.equal(source.includes(hook), false, `${file}: ${hook} must come from SENTRY_SCRUB_OPTIONS`);
        }
    }
});
