import { z } from "zod";

const AppEnvironmentSchema = z.enum(["development", "staging", "production"]);
const VercelEnvironmentSchema = z.enum(["development", "preview", "production"]);

const EnvSchema = z.object({
    NODE_ENV: z.string().optional(),
    NEXT_PUBLIC_APP_ENV: AppEnvironmentSchema.optional(),
    VERCEL_ENV: VercelEnvironmentSchema.optional(),
    NEXT_PUBLIC_API_BASE_URL: z.string().url().optional(),
    NEXT_PUBLIC_COMMIT_SHA: z.string().min(7).optional(),
    VERCEL_GIT_COMMIT_SHA: z.string().min(7).optional(),
    COMMIT_SHA: z.string().min(7).optional(),
    NEXT_PUBLIC_SENTRY_DSN: z.string().url().optional(),
    NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE: z.coerce.number().min(0).max(1).optional(),
    NEXT_PUBLIC_SENTRY_PROFILES_SAMPLE_RATE: z.coerce.number().min(0).max(1).optional(),
    NEXT_PUBLIC_SENTRY_ENABLED: z.coerce.boolean().optional(),
});

function parseEnv() {
    const raw = {
        NODE_ENV: process.env.NODE_ENV,
        NEXT_PUBLIC_APP_ENV: process.env.NEXT_PUBLIC_APP_ENV,
        VERCEL_ENV: process.env.VERCEL_ENV,
        NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
        NEXT_PUBLIC_COMMIT_SHA: process.env.NEXT_PUBLIC_COMMIT_SHA,
        VERCEL_GIT_COMMIT_SHA: process.env.VERCEL_GIT_COMMIT_SHA,
        COMMIT_SHA: process.env.COMMIT_SHA,
        NEXT_PUBLIC_SENTRY_DSN: process.env.NEXT_PUBLIC_SENTRY_DSN,
        NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE: process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE,
        NEXT_PUBLIC_SENTRY_PROFILES_SAMPLE_RATE: process.env.NEXT_PUBLIC_SENTRY_PROFILES_SAMPLE_RATE,
        NEXT_PUBLIC_SENTRY_ENABLED: process.env.NEXT_PUBLIC_SENTRY_ENABLED,
    };

    const parsed = EnvSchema.safeParse(raw);
    if (!parsed.success) {
        if (process.env.NODE_ENV !== "production") {
            throw new Error(`Invalid environment configuration: ${parsed.error.message}`);
        }
        return raw;
    }
    return parsed.data;
}

export const env = parseEnv();

export function appEnvironment(): "development" | "staging" | "production" {
    const appEnv = env.NEXT_PUBLIC_APP_ENV;
    if (appEnv === "development" || appEnv === "staging" || appEnv === "production") return appEnv;
    if (env.VERCEL_ENV === "production") return "production";
    if (env.VERCEL_ENV === "preview") return "staging";
    return "development";
}

export function apiBaseUrl(): string {
    if (env.NEXT_PUBLIC_API_BASE_URL) return env.NEXT_PUBLIC_API_BASE_URL.replace(/\/+$/, "");

    if (process.env.NODE_ENV !== "production") {
        if (typeof window !== "undefined") return `${window.location.origin}/api/v1`;
        return "http://127.0.0.1:3100/api/v1";
    }

    if (typeof window !== "undefined") return `${window.location.origin}/api/v1`;
    if (process.env.VERCEL_URL) return `https://${process.env.VERCEL_URL}/api/v1`;
    return "http://127.0.0.1:3100/api/v1";
}

export function commitSha(): string | undefined {
    return env.NEXT_PUBLIC_COMMIT_SHA ?? env.VERCEL_GIT_COMMIT_SHA ?? env.COMMIT_SHA;
}

export function sentryEnabled(): boolean {
    if (typeof env.NEXT_PUBLIC_SENTRY_ENABLED === "boolean") return env.NEXT_PUBLIC_SENTRY_ENABLED;
    return appEnvironment() !== "development";
}

export function sentryTracesSampleRate(): number {
    if (typeof env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE === "number") return env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE;
    return appEnvironment() === "production" ? 0.1 : 0.25;
}

export function sentryProfilesSampleRate(): number {
    if (typeof env.NEXT_PUBLIC_SENTRY_PROFILES_SAMPLE_RATE === "number") return env.NEXT_PUBLIC_SENTRY_PROFILES_SAMPLE_RATE;
    return appEnvironment() === "production" ? 0.05 : 0.1;
}
