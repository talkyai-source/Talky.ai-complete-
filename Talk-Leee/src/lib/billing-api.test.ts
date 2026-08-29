import { test, afterEach, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createElement, type ReactElement } from "react";
import { cleanup, screen, waitFor } from "@testing-library/react";
import {
    useBillingInvoice,
    useBillingInvoices,
    useBillingUsage,
} from "@/lib/billing-api";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();

/**
 * `billingFetch` used to end in `catch { return null }`, so a 403, an expired
 * session or a backend outage arrived at the page as an ordinary empty result.
 * The billing page then rendered "0 of 0 minutes used" and "No invoices yet" —
 * a confident statement about someone's money that nothing had verified.
 *
 * These tests pin the contract that replaced it: a failure reaches the hook as
 * `isError`, an actually-empty success stays an ordinary empty result, and the
 * one endpoint where 404 is a real answer (a single invoice by id) keeps
 * returning null rather than erroring.
 */

const originalFetch = globalThis.fetch;

function jsonResponse(body: unknown, status = 200) {
    return new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
    });
}

function probe(useHook: () => { isLoading: boolean; isError: boolean; data: unknown }): ReactElement {
    function Probe() {
        const q = useHook();
        if (q.isLoading) return createElement("div", null, "state:loading");
        if (q.isError) return createElement("div", null, "state:error");
        return createElement("div", null, `state:ok:${JSON.stringify(q.data ?? null)}`);
    }
    return createElement(Probe);
}

beforeEach(() => {
    globalThis.fetch = originalFetch;
});

afterEach(() => {
    cleanup();
    globalThis.fetch = originalFetch;
});

test("a 403 on the usage endpoint surfaces as isError, not as empty data", async () => {
    globalThis.fetch = async () => jsonResponse({ detail: "Forbidden" }, 403);

    renderWithQueryClient(probe(useBillingUsage as never));

    await waitFor(() => {
        assert.ok(screen.getByText("state:error"));
    });
});

test("a 500 on the invoices endpoint surfaces as isError, not as an empty list", async () => {
    globalThis.fetch = async () => jsonResponse({ detail: "boom" }, 500);

    renderWithQueryClient(probe(useBillingInvoices as never));

    await waitFor(() => {
        assert.ok(screen.getByText("state:error"));
    });
});

test("a network failure on the invoices endpoint surfaces as isError", async () => {
    globalThis.fetch = async () => {
        throw new TypeError("Failed to fetch");
    };

    renderWithQueryClient(probe(useBillingInvoices as never));

    await waitFor(() => {
        assert.ok(screen.getByText("state:error"));
    });
});

test("a successful empty invoice list stays an ordinary empty result", async () => {
    globalThis.fetch = async () => jsonResponse([]);

    renderWithQueryClient(probe(useBillingInvoices as never));

    await waitFor(() => {
        assert.ok(screen.getByText("state:ok:[]"));
    });
});

test("a 404 for one invoice by id is 'not found', not a load failure", async () => {
    globalThis.fetch = async () => jsonResponse({ detail: "Not found" }, 404);

    renderWithQueryClient(probe((() => useBillingInvoice("missing-id")) as never));

    await waitFor(() => {
        assert.ok(screen.getByText("state:ok:null"));
    });
});

test("a 403 for one invoice by id is still a load failure", async () => {
    globalThis.fetch = async () => jsonResponse({ detail: "Forbidden" }, 403);

    renderWithQueryClient(probe((() => useBillingInvoice("some-id")) as never));

    await waitFor(() => {
        assert.ok(screen.getByText("state:error"));
    });
});
