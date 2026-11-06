import assert from "node:assert/strict";
import { test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HealthIndicator } from "./health-indicator";

for (const [status, label] of [
    ["healthy", "Healthy"], ["ok", "Healthy"], ["degraded", "Degraded"],
    ["unknown", "Degraded"],
] as const) test(`health badge renders the ${status} API status as ${label}`, () => {
    const client = new QueryClient({ defaultOptions: { queries: {
        retry: false, staleTime: Infinity, gcTime: Infinity, refetchOnMount: false,
    } } });
    try {
        // The real /api/v1/health payload and the legacy spelling share one contract.
        client.setQueryData(["health"], { status });
        const html = renderToStaticMarkup(
            <QueryClientProvider client={client}><HealthIndicator /></QueryClientProvider>,
        );
        assert.ok(html.includes(`aria-label="Health: ${label}"`), html);
    } finally { client.clear(); }
});

test("a failed health probe is Down even when the last payload was healthy", async () => {
    const client = new QueryClient({ defaultOptions: { queries: {
        retry: false, gcTime: Infinity, refetchOnMount: false,
    } } });
    try {
        client.setQueryData(["health"], { status: "healthy" });
        await assert.rejects(client.fetchQuery({ queryKey: ["health"], queryFn: async () => {
            throw new Error("synthetic probe failure");
        } }));
        const html = renderToStaticMarkup(
            <QueryClientProvider client={client}><HealthIndicator /></QueryClientProvider>,
        );
        assert.ok(html.includes('aria-label="Health: Down"'), html);
    } finally { client.clear(); }
});
