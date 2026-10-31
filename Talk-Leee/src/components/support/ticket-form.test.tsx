import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render } from "@testing-library/react";
import type { SupportConfig } from "@/lib/support-api";
import type { TicketDraft } from "@/lib/support-ticket-draft";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/link") return { __esModule: true, default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> };
    if (id.includes("providers/theme-provider")) return { useTheme: () => ({ theme: "light", setTheme() {}, toggleTheme() {} }) };
    return originalLoad.call(this, id, ...args);
};
let TicketForm: typeof import("./ticket-form").TicketForm;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    TicketForm = require("./ticket-form").TicketForm;
} finally {
    loader._load = originalLoad;
}

afterEach(() => cleanup());

const config: SupportConfig = {
    enabled: true, bot_enabled: true, permissions: { report: true, read_team: false },
    categories: [{ id: "billing", label: "Billing and payments" }, { id: "other", label: "Something else" }],
    suggestions: [], attachments: { max_mb: 10, max_per_message: 5, types: ["png"] },
};
const existing = { id: "t-9", number: "SUP-000009", subject: "Old one", status: "open" as const };

function mount(draft: TicketDraft, over: Partial<React.ComponentProps<typeof TicketForm>> = {}) {
    const calls = { submit: 0, append: 0, choice: [] as string[], draft: draft };
    const view = render(
        <TicketForm
            config={config}
            draft={draft}
            onDraftChange={(next) => { calls.draft = next; }}
            attachments={[]}
            onAttachmentsChange={() => undefined}
            existingTicket={null}
            existingChoice="new"
            onChooseExisting={(choice) => calls.choice.push(choice)}
            onSubmit={() => { calls.submit += 1; }}
            onAppend={() => { calls.append += 1; }}
            busy={false}
            error={null}
            {...over}
        />,
    );
    return { ...view, calls };
}

test("an empty draft shows the subject and description rules and does not submit", () => {
    const { container, calls } = mount({ subject: "", category: "billing", description: "", relatedCampaignId: null, relatedCallId: null });
    fireEvent.submit(container.querySelector("form")!);
    assert.equal(calls.submit, 0);
    assert.match(container.textContent ?? "", /subject needs at least 3 characters/);
    assert.match(container.textContent ?? "", /at least 10 characters/);
    const subject = container.querySelector<HTMLInputElement>("input[aria-invalid='true']");
    assert.ok(subject, "the subject input is marked invalid");
    assert.ok(subject.getAttribute("aria-describedby"), "the error is linked for screen readers");
});

test("a valid draft submits once", () => {
    const { container, calls } = mount({ subject: "Calls fail", category: "billing", description: "Every call drops after ten seconds.", relatedCampaignId: null, relatedCallId: null });
    fireEvent.submit(container.querySelector("form")!);
    assert.equal(calls.submit, 1);
    assert.doesNotMatch(container.textContent ?? "", /at least/);
});

test("a related campaign is shown as a removable chip", () => {
    const { container, calls } = mount({ subject: "Calls fail", category: "billing", description: "Every call drops after ten seconds.", relatedCampaignId: "camp-1", relatedCallId: null });
    const remove = container.querySelector<HTMLButtonElement>("button[aria-label='Remove Campaign']");
    assert.ok(remove, "remove button rendered");
    fireEvent.click(remove);
    assert.equal(calls.draft.relatedCampaignId, null);
});

test("an existing open ticket must be chosen before the form appears, and the append path posts a message", () => {
    const draft = { subject: "Calls fail", category: "billing", description: "Every call drops after ten seconds.", relatedCampaignId: null, relatedCallId: null };
    const choice = mount(draft, { existingTicket: existing, existingChoice: null });
    // Boolean checks only: assert.equal on a DOM node serialises it and never returns.
    assert.ok(!choice.container.querySelector("form"), "no form until a choice is made");
    assert.match(choice.container.textContent ?? "", /SUP-000009/);
    fireEvent.click(choice.getByText("Add this to SUP-000009 instead"));
    assert.deepEqual(choice.calls.choice, ["append"]);
    cleanup();
    const append = mount(draft, { existingTicket: existing, existingChoice: "append" });
    assert.ok(!append.container.querySelector("input:not([type='file'])"), "no subject field when appending; only the file picker's input remains");
    fireEvent.submit(append.container.querySelector("form")!);
    assert.equal(append.calls.append, 1);
    assert.equal(append.calls.submit, 0);
});
