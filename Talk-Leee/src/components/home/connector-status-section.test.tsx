import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
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

test("W5-T3 the section says what Limited means", () => {
    const { container } = render(<ConnectorStatusSection />);
    assert.match(container.textContent ?? "", /Limited means the connector works only as its card describes\./);
    assert.doesNotMatch(container.textContent ?? "", /connectors in the dashboard/i, "the dashboard may hide a card, so the lead sentence says what it can show");
});

// HomeLazySections loads each section through next/dynamic and mounts the hero video, so it cannot
// render in the test runner: this is a source guard on the one file that mounts the section.
test("W5-T3 the home page mounts the connector section (source guard)", () => {
    const file = readFileSync(path.join(process.cwd(), "src/components/home/home-lazy-sections.tsx"), "utf8");
    assert.match(file, /import\("@\/components\/home\/connector-status-section"\)/, "the section is imported");
    const mounted = file.slice(file.indexOf("export function HomeLazySections"));
    assert.match(mounted, /<ConnectorStatusSection \/>/, "the section is rendered by HomeLazySections");
});
