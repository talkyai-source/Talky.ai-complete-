import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { createElement } from "react";
import { cleanup, waitFor } from "@testing-library/react";

import { QualifiedLeadAlerts } from "@/components/notifications/qualified-lead-alerts";
import { backendApi } from "@/lib/backend-api";
import { notificationsStore } from "@/lib/notifications";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();

const SEEN_KEY = "talklee.qlead.seen.v1";
const originalEventsList = backendApi.events.list;
const originalCreate = notificationsStore.create;

type RawEvent = Awaited<ReturnType<typeof backendApi.events.list>>["items"][number];

function rawLead(id: string, ageMs = 0): RawEvent {
    return {
        id,
        category: "alert",
        title: `Qualified lead: ${id}`,
        description: "Wants a callback",
        severity: "info",
        related_campaign_id: null,
        related_call_id: null,
        actor_user_id: null,
        metadata: { kind: "qualified_lead", phone_number: "+15550001111" },
        created_at: new Date(Date.now() - ageMs).toISOString(),
    };
}

function stubEvents(items: RawEvent[]) {
    const created: string[] = [];
    backendApi.events.list = async () => ({ items }) as Awaited<ReturnType<typeof backendApi.events.list>>;
    notificationsStore.create = ((input: { title: string }) => {
        created.push(input.title);
        return "notification-id";
    }) as typeof notificationsStore.create;
    return created;
}

afterEach(() => {
    cleanup();
    backendApi.events.list = originalEventsList;
    notificationsStore.create = originalCreate;
    try {
        window.localStorage.removeItem(SEEN_KEY);
    } catch {
        /* ignore */
    }
});

// The de-dupe set used to be hydrated from localStorage DURING RENDER, guarded
// by a ref. Under React Compiler memoization that render can be skipped, and
// the component would then re-toast every lead the user has already seen. The
// hydration now runs in an effect and gates the data effect through state.
test("leads already recorded in localStorage do not toast again", async () => {
    window.localStorage.setItem(SEEN_KEY, JSON.stringify(["evt-seen"]));
    const created = stubEvents([rawLead("evt-seen"), rawLead("evt-fresh")]);

    renderWithQueryClient(createElement(QualifiedLeadAlerts));

    await waitFor(() => assert.deepEqual(created, ["Qualified lead: evt-fresh"]));

    const persisted = JSON.parse(window.localStorage.getItem(SEEN_KEY) ?? "[]") as string[];
    assert.deepEqual(persisted.slice().sort(), ["evt-fresh", "evt-seen"]);
});

test("the first load with no history seeds silently instead of toasting the backlog", async () => {
    const created = stubEvents([rawLead("evt-a"), rawLead("evt-b")]);

    renderWithQueryClient(createElement(QualifiedLeadAlerts));

    await waitFor(() => {
        const persisted = JSON.parse(window.localStorage.getItem(SEEN_KEY) ?? "[]") as string[];
        assert.equal(persisted.length, 2);
    });
    assert.deepEqual(created, []);
});

test("stale unseen leads are absorbed silently, fresh ones toast", async () => {
    window.localStorage.setItem(SEEN_KEY, JSON.stringify(["evt-seen"]));
    const created = stubEvents([
        rawLead("evt-old", 3 * 60 * 60 * 1000),
        rawLead("evt-new"),
    ]);

    renderWithQueryClient(createElement(QualifiedLeadAlerts));

    await waitFor(() => assert.deepEqual(created, ["Qualified lead: evt-new"]));
});
