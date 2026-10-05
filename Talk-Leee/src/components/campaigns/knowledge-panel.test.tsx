import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";

import { KnowledgePanel } from "@/components/campaigns/knowledge-panel";
import { api, type CampaignKnowledge, type KnowledgeTestResponse } from "@/lib/api";

const originalGet = api.getCampaignKnowledge;
const originalTest = api.testCampaignKnowledge;
const originalUpdate = api.updateKnowledgeNode;

afterEach(() => {
    cleanup();
    api.getCampaignKnowledge = originalGet;
    api.testCampaignKnowledge = originalTest;
    api.updateKnowledgeNode = originalUpdate;
});

function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((done) => {
        resolve = done;
    });
    return { promise, resolve };
}

function knowledge(campaignId: string, filename: string): CampaignKnowledge {
    return {
        campaign_id: campaignId,
        knowledge_mode: "inline",
        sources: [{
            id: `source-${campaignId}`,
            filename,
            token_count: 10,
            version: 1,
            status: "ready",
            created_at: "2026-09-03T00:00:00Z",
        }],
        tree: [{
            id: `node-${campaignId}`,
            parent_id: null,
            depth: 0,
            path: "1",
            position: 1,
            heading: `Heading ${campaignId}`,
            content: "Content",
            summary: "Summary",
            voice_answer: "Answer",
            keywords: [],
            example_questions: [],
            priority: 0,
            hit_count: 0,
            enabled: true,
            children: [],
        }],
    };
}

test("read-only knowledge hides every mutation control", async () => {
    api.getCampaignKnowledge = async () => knowledge("campaign-a", "readonly.md");

    render(<KnowledgePanel campaignId="campaign-a" readOnly />);

    assert.ok(await screen.findByText("readonly.md"));
    assert.equal(screen.queryByRole("button", { name: /upload/i }), null);
    assert.equal(screen.queryByTitle("Edit"), null);
    assert.equal(screen.queryByTitle("Pin (prioritise)"), null);
    assert.equal(screen.queryByTitle("Disable"), null);
    assert.equal(screen.queryByTitle("Delete this source and its sections"), null);
});

test("a superseded knowledge request cannot replace the current campaign tree", async () => {
    const first = deferred<CampaignKnowledge>();
    api.getCampaignKnowledge = async (campaignId) => (
        campaignId === "campaign-a" ? first.promise : knowledge("campaign-b", "current.md")
    );

    const view = render(<KnowledgePanel campaignId="campaign-a" />);
    view.rerender(<KnowledgePanel campaignId="campaign-b" />);
    assert.ok(await screen.findByText("current.md"));

    await act(async () => {
        first.resolve(knowledge("campaign-a", "superseded.md"));
        await first.promise;
    });

    await waitFor(() => {
        assert.ok(screen.getByText("current.md"));
        assert.equal(screen.queryByText("superseded.md"), null);
    });
});

test("switching campaigns immediately removes already-loaded knowledge while the next request is pending", async () => {
    const second = deferred<CampaignKnowledge>();
    api.getCampaignKnowledge = async (campaignId) => (
        campaignId === "campaign-a" ? knowledge("campaign-a", "previous.md") : second.promise
    );

    const view = render(<KnowledgePanel campaignId="campaign-a" />);
    assert.ok(await screen.findByText("previous.md"));

    view.rerender(<KnowledgePanel campaignId="campaign-b" />);

    await waitFor(() => assert.equal(screen.queryByText("previous.md"), null));
    assert.ok(screen.getByText("Loading knowledge…"));

    await act(async () => {
        second.resolve(knowledge("campaign-b", "current.md"));
        await second.promise;
    });
    assert.ok(await screen.findByText("current.md"));
});

function result(status: "matched" | "weak_match" | "no_match" = "matched", query = "Starter price"): KnowledgeTestResponse {
    const text = "- Starter: Original price is $20 per month, excluding tax.";
    return {
        query,
        hits: [{ id: "node-a", heading: "Starter", voice_answer: "Invented price is $999.", summary: "Everything is free." }],
        evidence: { status, text: status === "no_match" ? "" : text, passages: status === "no_match" ? [] : [{
            node_id: "node-a", source_id: "source-a", source_version: 4,
            version: "2026-10-06T00:00:00+00:00", text, coverage: status === "matched" ? 1 : null,
        }] },
    };
}

async function openQuestion() {
    api.getCampaignKnowledge = async (id) => knowledge(id, `${id}.md`);
    const view = render(<KnowledgePanel campaignId="campaign-a" />);
    await screen.findByText("campaign-a.md");
    const input = screen.getByPlaceholderText(/Test a question/);
    await act(async () => { fireEvent.change(input, { target: { value: "Starter price" } }); });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Test" })); });
    return { view, input };
}

for (const status of ["matched", "weak_match", "no_match"] as const) {
    test(`question result preserves ${status} and never displays generated raw-hit phrasing`, async () => {
        api.testCampaignKnowledge = async () => result(status);
        await openQuestion();
        const region = within(await screen.findByRole("region", { name: "Knowledge test result" }));
        assert.ok(region.getByText(status === "matched" ? "Source passages found" : "No confirmed answer"));
        assert.equal(region.queryByText(/\$999|Everything is free/), null);
        assert.equal(region.queryByText(/follow.up/i), null);
        assert.ok(region.getByText(/does not test a generated answer or a call/));
        if (status === "no_match") {
            assert.ok(region.getByText(/cannot confirm this detail/));
            assert.equal(region.queryByText(/Original price/), null);
        } else {
            assert.ok(region.getByText(/Original price is \$20 per month, excluding tax/));
            assert.ok(region.getByText(/Source: source-a · Revision: 4/));
            assert.ok(region.getByText(/Section: node-a · Version: 2026-10-06/));
        }
        if (status === "weak_match") assert.ok(region.getByText(/insufficient to confirm/));
    });
}

test("absent enrichment and provenance still show original source with explicit unavailable revision", async () => {
    const response = result();
    response.hits = [{ id: "node-a", voice_answer: null, summary: null }];
    Object.assign(response.evidence.passages[0]!, { source_id: null, source_version: null, version: null });
    api.testCampaignKnowledge = async () => response;
    await openQuestion();
    const region = within(await screen.findByRole("region", { name: "Knowledge test result" }));
    assert.ok(region.getByText(/Original price is \$20/));
    assert.ok(region.getByText(/Source: unavailable · Revision: unavailable/));
    assert.ok(region.getByText(/Version: unavailable/));
});

test("editing the query clears evidence and ignores an older pending result", async () => {
    const pending = deferred<KnowledgeTestResponse>();
    api.testCampaignKnowledge = async () => pending.promise;
    const { input } = await openQuestion();
    await act(async () => { fireEvent.change(input, { target: { value: "Support hours" } }); });
    await act(async () => { pending.resolve(result()); await pending.promise; });
    assert.equal(screen.queryByRole("region", { name: "Knowledge test result" }), null);
    api.testCampaignKnowledge = async () => result("no_match", "Support hours");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Test" })); });
    assert.ok(await screen.findByText("No confirmed answer"));
    await act(async () => { fireEvent.change(input, { target: { value: "Another question" } }); });
    assert.equal(screen.queryByRole("region", { name: "Knowledge test result" }), null);
});

test("campaign switch and old request errors cannot replace current diagnostics", async () => {
    let fail!: (reason: Error) => void;
    api.testCampaignKnowledge = async () => new Promise((_, reject) => { fail = reject; });
    const { view } = await openQuestion();
    view.rerender(<KnowledgePanel campaignId="campaign-b" />);
    await screen.findByText("campaign-b.md");
    await act(async () => { fail(new Error("Old campaign diagnostic failure")); });
    assert.equal(screen.queryByText(/Old campaign diagnostic failure/), null);
    assert.equal(screen.queryByRole("region", { name: "Knowledge test result" }), null);
});

test("a source mutation clears reviewed evidence and blocks testing until it finishes", async () => {
    api.testCampaignKnowledge = async () => result();
    const pending = deferred<{ id: string; updated: string[] }>();
    api.updateKnowledgeNode = async () => pending.promise;
    await openQuestion();
    await screen.findByRole("region", { name: "Knowledge test result" });
    await act(async () => { fireEvent.click(screen.getByTitle("Disable")); });
    assert.equal(screen.queryByRole("region", { name: "Knowledge test result" }), null);
    assert.equal((screen.getByRole("button", { name: "Test" }) as HTMLButtonElement).disabled, true);
    await act(async () => { pending.resolve({ id: "node-campaign-a", updated: ["enabled"] }); await pending.promise; });
    assert.equal((screen.getByRole("button", { name: "Test" }) as HTMLButtonElement).disabled, false);
});

test("request errors stay errors rather than becoming no-match evidence", async () => {
    api.testCampaignKnowledge = async () => { throw new Error("Knowledge service unavailable"); };
    await openQuestion();
    assert.ok(await screen.findByText("Knowledge service unavailable"));
    assert.equal(screen.queryByRole("region", { name: "Knowledge test result" }), null);
});
