import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render } from "@testing-library/react";
import { ConnectorStatusSection } from "./connector-status-section";
import { PUBLIC_CONNECTORS } from "@/lib/public-connector-status";

afterEach(() => cleanup());

test("W5-T3 the section shows one entry per data-file entry, each with its label", () => {
    const { container } = render(<ConnectorStatusSection />);
    const entries = [...container.querySelectorAll("li[data-connector]")];
    assert.equal(entries.length, PUBLIC_CONNECTORS.length);
    PUBLIC_CONNECTORS.forEach((c, i) => {
        const li = entries[i];
        assert.equal(li.getAttribute("data-connector"), c.name);
        assert.ok(li.textContent?.includes(c.name), `${c.name}: name`);
        assert.ok(li.textContent?.includes(c.status), `${c.name}: label ${c.status}`);
        assert.ok(li.textContent?.includes(c.does), `${c.name}: sentence`);
    });
});

test("W5-T3 an entry that is not Available has no button and no link", () => {
    const { container } = render(<ConnectorStatusSection />);
    let checked = 0;
    for (const li of container.querySelectorAll("li[data-connector]")) {
        const name = li.getAttribute("data-connector")!;
        if (PUBLIC_CONNECTORS.find((c) => c.name === name)!.status === "Available") continue;
        checked += 1;
        assert.equal(li.querySelectorAll("a, button, [role=button], [role=link]").length, 0, `${name} invites use`);
    }
    assert.ok(checked >= 4);
});
