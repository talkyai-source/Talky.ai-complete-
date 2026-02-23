import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { StatusPill, type StatusPillState } from "@/components/ui/status-pill";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();

afterEach(() => cleanup());

const CASES: Array<{ state: StatusPillState; label: string; tooltip: RegExp }> = [
    { state: "connected", label: "Connected", tooltip: /active and syncing/i },
    { state: "disconnected", label: "Disconnected", tooltip: /not connected yet/i },
    { state: "expired", label: "Expired", tooltip: /credentials have expired/i },
    { state: "error", label: "Error", tooltip: /encountered an error/i },
];

for (const c of CASES) {
    test(`StatusPill renders ${c.state} with tooltip`, async () => {
        renderWithQueryClient(createElement(StatusPill, { state: c.state, tooltipDelayMs: 0 }));

        const pill = screen.getByLabelText(`Status: ${c.label}`);
        assert.ok(pill);

        fireEvent.focus(pill);
        const tooltips = await screen.findAllByText(c.tooltip);
        assert.ok(tooltips.length > 0);
    });
}
