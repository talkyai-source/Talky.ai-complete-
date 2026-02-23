import * as Sentry from "@sentry/nextjs";
import { sentryEnabled } from "@/lib/env";

export type MonitoringContext = Record<string, unknown>;

export function captureException(error: unknown, context?: MonitoringContext) {
    try {
        if (!sentryEnabled()) return;
        if (!process.env.NEXT_PUBLIC_SENTRY_DSN) return;
        Sentry.withScope((scope) => {
            if (context) scope.setExtras(context);
            Sentry.captureException(error);
        });
    } catch {}
}

export function captureMessage(message: string, context?: MonitoringContext) {
    try {
        if (!sentryEnabled()) return;
        if (!process.env.NEXT_PUBLIC_SENTRY_DSN) return;
        Sentry.withScope((scope) => {
            if (context) scope.setExtras(context);
            Sentry.captureMessage(message);
        });
    } catch {}
}
