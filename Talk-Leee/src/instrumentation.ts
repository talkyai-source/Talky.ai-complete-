import { scrubSentryBreadcrumb, scrubSentryEvent, scrubSentryTransaction } from "./lib/sentry-scrub";
import { appEnvironment, commitSha, sentryEnabled, sentryProfilesSampleRate, sentryTracesSampleRate } from "./lib/env";

export async function register() {
    const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
    const enabled = Boolean(dsn) && sentryEnabled();
    if (!enabled || !dsn) return;

    const runtime = process.env.NEXT_RUNTIME;
    const Sentry = await import("@sentry/nextjs");

    if (runtime === "nodejs") {
        Sentry.init({
            dsn,
            enabled,
            environment: appEnvironment(),
            release: commitSha(),
            tracesSampleRate: sentryTracesSampleRate(),
            profilesSampleRate: sentryProfilesSampleRate(),
            sendDefaultPii: false,
            beforeSend: scrubSentryEvent,
            beforeSendTransaction: scrubSentryTransaction,
            beforeBreadcrumb: scrubSentryBreadcrumb,
        });
        return;
    }

    if (runtime === "edge") {
        Sentry.init({
            dsn,
            enabled,
            environment: appEnvironment(),
            release: commitSha(),
            tracesSampleRate: sentryTracesSampleRate(),
            sendDefaultPii: false,
            beforeSend: scrubSentryEvent,
            beforeSendTransaction: scrubSentryTransaction,
            beforeBreadcrumb: scrubSentryBreadcrumb,
        });
    }
}

export async function onRequestError(...args: unknown[]) {
    const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
    const enabled = Boolean(dsn) && sentryEnabled();
    if (!enabled) return;

    const Sentry = await import("@sentry/nextjs");
    const capture = (Sentry as unknown as { captureRequestError?: (...a: unknown[]) => void }).captureRequestError;
    if (typeof capture !== "function") return;
    capture(...args);
}
