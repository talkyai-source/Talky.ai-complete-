import * as Sentry from "@sentry/nextjs";
import { scrubSentryBreadcrumb, scrubSentryEvent, scrubSentryTransaction } from "./lib/sentry-scrub";
import { appEnvironment, commitSha, sentryEnabled, sentryProfilesSampleRate, sentryTracesSampleRate } from "./lib/env";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
const enabled = Boolean(dsn) && sentryEnabled();

if (enabled && dsn) {
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
}

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
