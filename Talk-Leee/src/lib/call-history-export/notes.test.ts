import assert from "node:assert/strict";
import test from "node:test";
import { fetchWorkflowForCalls, type WorkflowHttpClient } from "./notes";
import type { CallExportProgress } from "./columns";

const saved = (notes: string) => ({
    leadType: "hot", notes, form: { contact: "c", interest: "i", nextStep: "n", completed: true }, updatedAt: "2026-11-21T00:00:00Z",
});
const httpError = (status: number) => Object.assign(new Error(`http ${status}`), { status });
const ids = (n: number) => Array.from({ length: n }, (_, i) => `id-${i}`);
const tick = () => new Promise<void>((resolve) => setTimeout(resolve, 1));

test("bulk success goes in chunks of 200 and parses what is saved", async () => {
    const calls: Array<{ path: string; method?: string; size: number }> = [];
    const client: WorkflowHttpClient = {
        async request(opts) {
            const body = opts.body as { call_ids: string[] };
            calls.push({ path: opts.path, method: opts.method, size: body.call_ids.length });
            return { items: { [body.call_ids[0]]: saved(`note for ${body.call_ids[0]}`) } };
        },
    };
    const progress: CallExportProgress[] = [];
    const result = await fetchWorkflowForCalls(ids(450), { onProgress: (p) => progress.push(p) }, client);
    assert.deepEqual(calls.map((c) => c.size), [200, 200, 50]);
    assert.ok(calls.every((c) => c.path === "/calls/workflow/batch" && c.method === "POST"));
    assert.deepEqual(Object.keys(result).sort(), ["id-0", "id-200", "id-400"]);
    assert.equal(result["id-200"]?.notes, "note for id-200");
    assert.equal(result["id-200"]?.form.completed, true);
    assert.deepEqual(progress[progress.length - 1], { phase: "notes", done: 450, total: 450 });
    assert.ok(progress.every((p) => p.phase === "notes" && p.total === 450));
});

test("bulk 404 falls back to per-call requests, never more than 6 at once; a per-call 404 is nothing saved", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    let bulkTries = 0;
    const perCall: string[] = [];
    const client: WorkflowHttpClient = {
        async request(opts) {
            if (opts.path === "/calls/workflow/batch") {
                bulkTries += 1;
                throw httpError(404);
            }
            inFlight += 1;
            maxInFlight = Math.max(maxInFlight, inFlight);
            await tick();
            inFlight -= 1;
            perCall.push(opts.path);
            const id = decodeURIComponent(opts.path.split("/")[2]);
            if (id === "id-3") throw httpError(404);
            if (id === "id-4") return { workflow: null };
            return { workflow: saved(`n-${id}`) };
        },
    };
    const result = await fetchWorkflowForCalls(ids(30), {}, client);
    assert.equal(bulkTries, 1); // one failed bulk try, then per-call for the rest of the export
    assert.equal(perCall.length, 30);
    assert.ok(maxInFlight > 1 && maxInFlight <= 6, `maxInFlight=${maxInFlight}`);
    assert.equal(result["id-3"], undefined);
    assert.equal(result["id-4"], undefined);
    assert.equal(result["id-5"]?.notes, "n-id-5");
    assert.equal(Object.keys(result).length, 28);
});

test("405 also falls back", async () => {
    const client: WorkflowHttpClient = {
        async request(opts) {
            if (opts.path === "/calls/workflow/batch") throw httpError(405);
            return { workflow: saved("x") };
        },
    };
    const result = await fetchWorkflowForCalls(ids(3), {}, client);
    assert.equal(Object.keys(result).length, 3);
});

test("a 500 is thrown, from bulk and from per-call", async () => {
    await assert.rejects(
        fetchWorkflowForCalls(ids(2), {}, { request: async () => { throw httpError(500); } }),
        /http 500/,
    );
    await assert.rejects(
        fetchWorkflowForCalls(ids(2), {}, {
            async request(opts) {
                if (opts.path === "/calls/workflow/batch") throw httpError(404);
                throw httpError(500);
            },
        }),
        /http 500/,
    );
});

test("an aborted signal stops with AbortError before any request", async () => {
    const controller = new AbortController();
    controller.abort();
    let requests = 0;
    await assert.rejects(
        fetchWorkflowForCalls(ids(5), { signal: controller.signal }, { request: async () => { requests += 1; return { items: {} }; } }),
        (err: unknown) => err instanceof DOMException && err.name === "AbortError",
    );
    assert.equal(requests, 0);
});
