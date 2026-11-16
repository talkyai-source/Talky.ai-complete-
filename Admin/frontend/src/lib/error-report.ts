import { api, type ClientErrorReport } from './api';

/**
 * The panel reports its own errors to POST /admin/client-errors. Only the fields of
 * ClientErrorReport are ever sent: never component props, page text, request or response
 * bodies, or a query string. Text is scrubbed first (same ideas as the customer website's
 * sentry-scrub.ts: email, phone-like digit runs, tokens, URL query strings).
 *
 * Errors on the sign-in page are NOT reported: the route answers 401 when signed out, so
 * nothing is sent while there is no session.
 */

export type ClientErrorKind = ClientErrorReport['kind'];

const NAME_MAX = 100;
const MESSAGE_MAX = 500;
const STACK_LINES = 10;
const STACK_LINE_MAX = 300;
const ROUTE_MAX = 200;
const BODY_MAX_BYTES = 8000; // the route refuses more than 8 KB
const WORK_MAX = 5000; // never run the patterns over a huge string
const PER_MINUTE = 5;
const MINUTE_MS = 60_000;
const SAME_ERROR_MS = 5 * MINUTE_MS;

// The panel has no build-time version value (package.json is "0.0.0" and is not exposed to
// the bundle, and no environment variable may be added), so no version is sent.
const APP_VERSION: string | null = null;

const JWT = /\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*/g;
const BEARER = /\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}/gi;
const EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+/g;
const URL_QUERY = /([A-Za-z0-9_\-./:%@]+)\?[^\s)'"\]]+/g;
const LONG_TOKEN = /\b[A-Za-z0-9]{24,}\b/g;
const PHONE = /\+?\d(?:[ -]?\d){6,}/g;

/** Remove customer data from one piece of text, then cap it. Never throws. */
export function scrubText(input: unknown, max = MESSAGE_MAX): string {
    try {
        let text = String(input ?? '').slice(0, WORK_MAX);
        // The query string goes first: its own values (an email, a token) must not survive in pieces.
        text = text.replace(JWT, '[token]').replace(BEARER, '[token]').replace(URL_QUERY, '$1');
        text = text.replace(EMAIL, '[email]').replace(LONG_TOKEN, '[token]').replace(PHONE, '[phone]');
        return text.slice(0, max);
    } catch {
        return '';
    }
}

/** The path only: never the query string or the fragment. */
export function currentRoutePath(location: { pathname: string; search?: string; hash?: string }): string {
    return scrubText(String(location.pathname ?? '/').split(/[?#]/)[0] || '/', ROUTE_MAX);
}

const bytes = (body: ClientErrorReport) => new TextEncoder().encode(JSON.stringify(body)).length;

export function buildErrorBody(kind: ClientErrorKind, error: unknown, route: string, now: number, appVersion: string | null): ClientErrorReport {
    const real = error instanceof Error ? error : null;
    const name = scrubText(real?.name || 'Error', NAME_MAX);
    const message = scrubText(real ? real.message : error, MESSAGE_MAX);
    const lines = String(real?.stack ?? '').slice(0, WORK_MAX).split('\n').map((line) => line.trim()).filter(Boolean);
    if (lines.length && lines[0].startsWith(`${real?.name}:`)) lines.shift();
    const stack = lines.slice(0, STACK_LINES).map((line) => scrubText(line, STACK_LINE_MAX));
    const body: ClientErrorReport = { kind, name, message, stack, route, app_version: appVersion, occurred_at: new Date(now).toISOString() };
    // Multi-byte text can push the body past the limit: drop stack lines, then shorten the message.
    while (bytes(body) > BODY_MAX_BYTES && body.stack.length > 0) body.stack.pop();
    if (bytes(body) > BODY_MAX_BYTES) body.message = body.message.slice(0, 100);
    return body;
}

export interface ErrorReporterDeps {
    send: (body: ClientErrorReport) => unknown;
    now: () => number;
    isSignedIn: () => boolean;
    route: () => string;
    appVersion?: string | null;
}

/** At most 5 reports a minute; the same name+message+route once per 5 minutes; none while signed out. */
export function createErrorReporter(deps: ErrorReporterDeps): (kind: ClientErrorKind, error: unknown) => void {
    const sentAt: number[] = [];
    const seen = new Map<string, number>();
    return (kind, error) => {
        try {
            if (!deps.isSignedIn()) return;
            const now = deps.now();
            const body = buildErrorBody(kind, error, deps.route(), now, deps.appVersion ?? null);
            const key = `${body.name}\n${body.message}\n${body.route}`;
            const last = seen.get(key);
            if (last !== undefined && now - last < SAME_ERROR_MS) return;
            while (sentAt.length && now - sentAt[0] >= MINUTE_MS) sentAt.shift();
            if (sentAt.length >= PER_MINUTE) return;
            sentAt.push(now);
            seen.set(key, now);
            // A failed report is dropped silently and never reported itself (no loop).
            void Promise.resolve(deps.send(body)).catch(() => undefined);
        } catch {
            /* Reporting must never break the page. */
        }
    };
}

export const reportClientError = createErrorReporter({
    send: (body) => api.reportClientError(body),
    now: () => Date.now(),
    isSignedIn: () => api.getToken() !== null,
    route: () => currentRoutePath(window.location),
    appVersion: APP_VERSION,
});

const registered = new WeakSet<object>();

/** Listen for uncaught errors and unhandled rejections; asking again on the same target does nothing. */
export function registerGlobalErrorListeners(
    target: Pick<Window, 'addEventListener'> = window,
    report: (kind: ClientErrorKind, error: unknown) => void = reportClientError,
): void {
    if (registered.has(target)) return;
    registered.add(target);
    target.addEventListener('error', (event) => {
        const failure = event as ErrorEvent;
        // A failed image or script load has neither an error nor a message.
        if (failure.error === undefined && !failure.message) return;
        report('error', failure.error ?? failure.message);
    });
    target.addEventListener('unhandledrejection', (event) => {
        report('unhandledrejection', (event as PromiseRejectionEvent).reason);
    });
}
