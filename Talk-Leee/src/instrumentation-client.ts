import * as Sentry from "@sentry/nextjs";
import { SENTRY_SCRUB_OPTIONS } from "./lib/sentry-scrub";
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
        ...SENTRY_SCRUB_OPTIONS,
    });
}

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
