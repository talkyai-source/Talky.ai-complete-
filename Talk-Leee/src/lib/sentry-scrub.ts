/**
 * Strip customer data from error reports before they leave the browser or
 * the Next.js server. Wired into every `Sentry.init` as `beforeSend`,
 * `beforeSendTransaction` and `beforeBreadcrumb`.
 *
 * Mirror of backend/app/core/sentry_scrub.py: same key list, same patterns,
 * same caps, same never-throw rule. No lookbehind regexes (older Safari
 * throws a SyntaxError at load time on them, which would break the app).
 */
import type { Breadcrumb, Event as SentryEvent } from "@sentry/nextjs";

const MAX_DEPTH = 10;
const MAX_ITEMS = 200;
const MAX_STR = 2000;

const REDACTED = "[redacted]";
const WITHHELD = "event withheld: scrubbing failed";

// Matched on the whole key or as a word run inside it, after camelCase and
// punctuation are normalised to underscores (`x-api-key` -> `x_api_key`).
const SENSITIVE_TERMS: readonly string[] = [
    "password", "passwords", "passwd", "secret", "secrets", "token", "tokens",
    "authorization", "cookie", "cookies", "set_cookie", "api_key", "apikey",
    "dsn", "signature", "credential", "credentials", "session_id", "sessionid",
    "otp",
    "transcript", "transcripts", "utterance", "utterances", "user_text",
    "assistant_text", "prompt", "prompts", "system_prompt", "messages",
    "content", "body", "recording_url",
    "statement",
];
const SAFE_KEYS = new Set(["content_type", "content_length", "content_encoding", "content_disposition"]);
const NUMERIC_OK_TERMS = new Set(["token", "tokens"]); // max_tokens, input_tokens

function normaliseKey(key: unknown): string {
    return String(key)
        .slice(0, 200)
        .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "");
}

function sensitiveKeyTerm(key: unknown): string | null {
    const k = normaliseKey(key);
    if (!k || SAFE_KEYS.has(k)) return null;
    const padded = `_${k}_`;
    for (const term of SENSITIVE_TERMS) {
        if (padded.includes(`_${term}_`)) return term;
    }
    return null;
}

const MARKERS = "user_text|assistant_text|transcript|utterance|text|said|reply";
const SPEECH =
    /(^|[^A-Za-z0-9])(user_text|assistant_text|transcript|utterance|text|said|reply)=(?:'(?:[^'\\]|\\[\s\S])*(?:'|$)|"(?:[^"\\]|\\[\s\S])*(?:"|$))/g;
// Unquoted value: up to the next " word=" token or the end of the string. A
// leading "%" is a logging placeholder in a template, not speech.
const SPEECH_UNQUOTED = new RegExp(
    String.raw`(^|[^A-Za-z0-9])(${MARKERS})=(?![\s%'"]|\[redacted\])[\s\S]*?(?= [A-Za-z_][A-Za-z0-9_]*=|$)`,
    "g",
);
// text just before a placeholder ends with "marker=" (optionally "marker='")
const AFTER_MARKER = new RegExp(`(?:^|[^A-Za-z0-9])(?:${MARKERS})=['"]?$`);
const PLACEHOLDER = /%(?:\(([^)]*)\))?[#0\- +]*(\*|\d+)?(?:\.(\*|\d+))?[hlL]?([a-zA-Z%])/g;
const URL_USERINFO = /([A-Za-z][A-Za-z0-9+.-]*:\/\/)[^\s/?#@]+@/g;
const URL_QUERY = /([?&])([A-Za-z0-9_.\-[\]%]+)=([^&#\s'"]*)/g;
const JWT = /\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*/g;
const BEARER = /\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}/gi;
const KEYS =
    /(^|[^A-Za-z0-9])(?:(?:sk|pk|rk)[-_](?:live_|test_|proj-)?[A-Za-z0-9_-]{12,}|whsec_[A-Za-z0-9_-]{12,}|xox[abprs]-[A-Za-z0-9-]{12,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})/g;
const EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+/g;

const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const ISO = "\\d{4}-\\d{2}-\\d{2}(?:[T ]\\d{2}:\\d{2}(?::\\d{2}(?:\\.\\d+)?)?(?:Z|[+-]\\d{2}:?\\d{2})?)?";
const IPV4 = "\\d{1,3}(?:\\.\\d{1,3}){3}";
// keep-alternatives are consumed first so their digits are never read as a phone
const PHONE = new RegExp(
    `(^|[^A-Za-z0-9_])(?:(${UUID}|${ISO}|${IPV4})(?![A-Za-z0-9_])|(\\+\\(?\\d(?:[ \\-.()]{0,2}\\d){6,14})(?!\\d)|(\\(?\\d(?:[ \\-.()]{0,2}\\d){8,})(?![A-Za-z0-9_]))`,
    "g",
);

function scrubPlain(input: string): string {
    let s = input.replace(JWT, "[token]");
    s = s.replace(BEARER, "[token]");
    s = s.replace(KEYS, "$1[key]");
    s = s.replace(EMAIL, "[email]");
    return s.replace(PHONE, (m: string, pre: string, keep: string | undefined) => (keep ? m : `${pre}[phone]`));
}

function scrubString(input: string): string {
    let s = input.length > MAX_STR ? input.slice(0, MAX_STR) : input;
    s = s.replace(SPEECH, (_m: string, pre: string, marker: string) => `${pre}${marker}=${REDACTED}`);
    s = s.replace(SPEECH_UNQUOTED, (_m: string, pre: string, marker: string) => `${pre}${marker}=${REDACTED}`);
    s = s.replace(URL_USERINFO, (_m: string, scheme: string) => `${scheme}${REDACTED}@`);
    s = s.replace(URL_QUERY, (_m: string, sep: string, key: string, value: string) =>
        sensitiveKeyTerm(key) !== null ? `${sep}${key}=${REDACTED}` : `${sep}${key}=${scrubPlain(value)}`,
    );
    return scrubPlain(s);
}

type Bag = Record<string, unknown>;

function isBag(value: unknown): value is Bag {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

function setKey(out: Bag, key: string, value: unknown): void {
    if (key === "__proto__") return;
    out[key.slice(0, 200)] = value;
}

function walk(value: unknown, depth: number): unknown {
    if (typeof value === "string") return scrubString(value);
    if (value === null || value === undefined || typeof value === "number" || typeof value === "boolean") return value;
    if (typeof value === "bigint") return String(value);
    if (typeof value === "function" || typeof value === "symbol") return "[unreadable]";
    if (value instanceof Date) return value;
    if (depth >= MAX_DEPTH) return "[truncated]";
    if (value instanceof Error) return scrubString(`${value.name}: ${value.message}`);
    if (Array.isArray(value)) return value.slice(0, MAX_ITEMS).map((v) => walk(v, depth + 1));
    if (isBag(value)) return walkBag(value, depth);
    return "[unreadable]";
}

function walkBag(bag: Bag, depth: number): Bag {
    const out: Bag = {};
    for (const [k, v] of Object.entries(bag).slice(0, MAX_ITEMS)) {
        const term = sensitiveKeyTerm(k);
        const numericOk = term !== null && NUMERIC_OK_TERMS.has(term) && typeof v === "number";
        setKey(out, k, term !== null && !numericOk ? REDACTED : walk(v, depth + 1));
    }
    return out;
}

/** Fallback when placeholders cannot be matched to arguments: every string is redacted. */
function redactStrings(value: unknown): unknown {
    if (typeof value === "string") return REDACTED;
    if (Array.isArray(value)) return value.slice(0, MAX_ITEMS).map(redactStrings);
    if (isBag(value)) {
        const out: Bag = {};
        for (const [k, v] of Object.entries(value).slice(0, MAX_ITEMS)) setKey(out, k, redactStrings(v));
        return out;
    }
    return walk(value, 2);
}

/**
 * Sentry sends the template in `message` and the raw arguments in `params`.
 * An argument that fills a placeholder right after a speech marker (`text=%s`)
 * is redacted whatever its type; the rest are pattern-scrubbed. If the
 * template cannot be matched to the arguments, every string argument is
 * redacted.
 */
function scrubParams(template: unknown, params: unknown): unknown {
    if (typeof template !== "string") return redactStrings(params);
    const positional: boolean[] = [];
    const named: Array<[string, boolean]> = [];
    let unparseable = false;
    for (const m of template.matchAll(PLACEHOLDER)) {
        if (m[4] === "%") continue;
        const before = template.slice(Math.max(0, (m.index ?? 0) - 40), m.index ?? 0);
        const marker = AFTER_MARKER.test(before);
        if (m[2] === "*" || m[3] === "*") unparseable = true;
        if (m[1] !== undefined) named.push([m[1], marker]);
        else positional.push(marker);
    }
    if (unparseable || (named.length > 0 && positional.length > 0)) return redactStrings(params);
    if (Array.isArray(params)) {
        if (named.length > 0 || params.length !== positional.length) return redactStrings(params);
        return params.map((v, i) => (positional[i] ? REDACTED : walk(v, 2)));
    }
    if (isBag(params)) {
        if (positional.length > 0 || named.length === 0) return redactStrings(params);
        const marked = new Set(named.filter(([, isMarker]) => isMarker).map(([n]) => n));
        const out: Bag = {};
        for (const [k, v] of Object.entries(params).slice(0, MAX_ITEMS)) {
            setKey(out, k, marked.has(k) || sensitiveKeyTerm(k) !== null ? REDACTED : walk(v, 2));
        }
        return out;
    }
    return redactStrings(params);
}

function scrubLogentry(le: unknown): unknown {
    if (!isBag(le)) return walk(le, 1);
    const out: Bag = {};
    for (const [k, v] of Object.entries(le).slice(0, MAX_ITEMS)) {
        if (k === "params") setKey(out, k, scrubParams(le.message, v));
        else setKey(out, k, sensitiveKeyTerm(k) !== null ? REDACTED : walk(v, 2));
    }
    return out;
}

const HEADER_ALLOW = new Set(["user-agent", "content-type", "accept", "x-request-id"]);

function scrubRequest(req: unknown): unknown {
    if (!isBag(req)) return walk(req, 1);
    const out: Bag = {};
    for (const [k, v] of Object.entries(req).slice(0, MAX_ITEMS)) {
        const lk = k.toLowerCase();
        if (lk === "cookies" || lk === "data" || lk === "env") continue;
        if (lk === "headers") {
            if (isBag(v)) {
                const headers: Bag = {};
                for (const [hk, hv] of Object.entries(v).slice(0, MAX_ITEMS)) {
                    if (HEADER_ALLOW.has(hk.toLowerCase())) setKey(headers, hk, walk(hv, 3));
                }
                setKey(out, k, headers);
            }
            continue;
        }
        if (lk === "query_string" && typeof v === "string") {
            setKey(out, k, scrubString(`?${v}`).slice(1));
            continue;
        }
        setKey(out, k, sensitiveKeyTerm(k) !== null ? REDACTED : walk(v, 2));
    }
    return out;
}

function scrubUser(user: unknown): Bag {
    return isBag(user) && "id" in user ? { id: walk(user.id, 2) } : {};
}

function safeScalar(value: unknown): string | number | undefined {
    if (typeof value === "string") return value.slice(0, 200);
    if (typeof value === "number") return value;
    return undefined;
}

function get(source: unknown, key: string): unknown {
    try {
        return (source as Bag)[key];
    } catch {
        return undefined;
    }
}

/** Minimal event built from scratch; reads only a few scalar fields. */
function withheldEvent(event: unknown): Bag {
    const out: Bag = {};
    for (const key of ["event_id", "timestamp", "level", "release", "environment"]) {
        const v = safeScalar(get(event, key));
        if (v !== undefined) out[key] = v;
    }
    try {
        const values = get(get(event, "exception"), "values");
        if (Array.isArray(values)) {
            const names: Bag[] = [];
            for (const item of values.slice(0, 20)) {
                const t = get(item, "type");
                if (typeof t === "string") names.push({ type: t.slice(0, 200) });
            }
            if (names.length > 0) out.exception = { values: names };
        }
    } catch {
        // keep what we have
    }
    out.message = WITHHELD;
    return out;
}

function scrubEventStrict(event: unknown): Bag {
    if (!isBag(event)) throw new TypeError("event is not an object");
    const out: Bag = {};
    for (const [k, v] of Object.entries(event).slice(0, MAX_ITEMS)) {
        if (k === "request") setKey(out, k, scrubRequest(v));
        else if (k === "user") setKey(out, k, scrubUser(v));
        else if (k === "logentry") setKey(out, k, scrubLogentry(v));
        else if (sensitiveKeyTerm(k) !== null) setKey(out, k, REDACTED);
        else setKey(out, k, walk(v, 1));
    }
    return out;
}

/** `beforeSend`. Never throws, never returns the original event. */
export function scrubSentryEvent<T extends SentryEvent>(event: T): T | null {
    try {
        return scrubEventStrict(event) as unknown as T;
    } catch {
        try {
            return withheldEvent(event) as unknown as T;
        } catch {
            return { message: WITHHELD } as unknown as T;
        }
    }
}

/** `beforeSendTransaction`. A transaction that cannot be scrubbed is dropped:
 *  a minimal error-shaped event would not be a valid one. */
export function scrubSentryTransaction<T extends SentryEvent>(event: T): T | null {
    try {
        return scrubEventStrict(event) as unknown as T;
    } catch {
        return null;
    }
}

/** `beforeBreadcrumb`. A breadcrumb that cannot be scrubbed is dropped. */
export function scrubSentryBreadcrumb(crumb: Breadcrumb): Breadcrumb | null {
    try {
        if (!isBag(crumb)) return null;
        return walkBag(crumb, 0) as Breadcrumb;
    } catch {
        return null;
    }
}
