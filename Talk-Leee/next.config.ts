import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const configFilePath = fileURLToPath(import.meta.url);
const configDir = path.dirname(configFilePath);

const nextConfig: NextConfig = {
    outputFileTracingRoot: configDir,
    compress: true,
    poweredByHeader: false,
    images: {
        formats: ["image/avif", "image/webp"],
        minimumCacheTTL: 86400,
    },
    experimental: {
        optimizePackageImports: ["lucide-react"],
    },
    async headers() {
        return [
            {
                source: "/images/:path*",
                headers: [
                    { key: "Cache-Control", value: "public, max-age=86400, stale-while-revalidate=604800" },
                ],
            },
            {
                source: "/:path*.mp4",
                headers: [
                    { key: "Cache-Control", value: "public, max-age=86400, stale-while-revalidate=604800" },
                ],
            },
            {
                source: "/:path*.svg",
                headers: [{ key: "Cache-Control", value: "public, max-age=604800, stale-while-revalidate=2592000" }],
            },
            {
                source: "/openapi.json",
                headers: [{ key: "Cache-Control", value: "public, max-age=3600, stale-while-revalidate=86400" }],
            },
            {
                source: "/site.webmanifest",
                headers: [{ key: "Cache-Control", value: "public, max-age=3600, stale-while-revalidate=86400" }],
            },
        ];
    },
    webpack: (config, { dev }) => {
        if (dev) {
            const systemRootIgnored = /^[A-Z]:\\(?:DumpStack\.log\.tmp|hiberfil\.sys|pagefile\.sys|swapfile\.sys|System Volume Information)(?:\\.*)?$/i;
            const extraIgnoredGlobs = [
                "**/DumpStack.log.tmp",
                "**/hiberfil.sys",
                "**/pagefile.sys",
                "**/swapfile.sys",
                "**/System Volume Information",
                "**/System Volume Information/**",
            ];

            const existingIgnored = config.watchOptions?.ignored;
            const mergedIgnored =
                existingIgnored instanceof RegExp
                    ? new RegExp(
                          `${existingIgnored.source}|${systemRootIgnored.source}`,
                          Array.from(new Set(`${existingIgnored.flags}${systemRootIgnored.flags}`.split(""))).join("")
                      )
                    : Array.isArray(existingIgnored)
                      ? [...existingIgnored, ...extraIgnoredGlobs]
                      : typeof existingIgnored === "string"
                        ? [existingIgnored, ...extraIgnoredGlobs]
                        : systemRootIgnored;

            config.watchOptions = { ...(config.watchOptions ?? {}), ignored: mergedIgnored };
        }
        return config;
    },
    // allowedDevOrigins: ["http://127.0.0.1:3100"],
};

const authToken = process.env.SENTRY_AUTH_TOKEN;
const releaseName = process.env.NEXT_PUBLIC_COMMIT_SHA || process.env.VERCEL_GIT_COMMIT_SHA || process.env.COMMIT_SHA;

export default withSentryConfig(
    nextConfig,
    {
        authToken,
        org: authToken ? process.env.SENTRY_ORG : undefined,
        project: authToken ? process.env.SENTRY_PROJECT : undefined,
        silent: true,
        webpack: {
            treeshake: {
                removeDebugLogging: true,
            },
        },
        release: authToken && releaseName ? { name: releaseName } : undefined,
        bundleSizeOptimizations: {
            excludeDebugStatements: true,
        },
        sourcemaps: authToken ? undefined : { disable: true },
    }
);
