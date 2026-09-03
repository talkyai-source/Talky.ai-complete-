import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { KnowledgePanel } from "@/components/campaigns/knowledge-panel";
import { api, type CampaignKnowledge } from "@/lib/api";

const originalGet = api.getCampaignKnowledge;

afterEach(() => {
    cleanup();
    api.getCampaignKnowledge = originalGet;
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
