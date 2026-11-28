import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { PERSONAS } from "@/lib/campaign-personas";

// Forms audit B-F7: the required slots shown with a red * must be exactly the
// backend's REQUIRED_SLOTS for each persona.
const BACKEND: Record<string, string> = {
    lead_gen: "lead_gen.py",
    customer_support: "customer_support.py",
    receptionist: "receptionist.py",
};

function backendRequired(file: string): string[] {
    const src = readFileSync(
        path.join(process.cwd(), "..", "backend", "app", "services", "scripts", "prompts", "personas", file), "utf8",
    );
    const block = /^REQUIRED_SLOTS\s*=\s*\(([\s\S]*?)\)/m.exec(src)?.[1] ?? "";
    return [...block.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]!);
}

for (const [persona, file] of Object.entries(BACKEND)) {
    test(`${persona}: required slots equal the backend REQUIRED_SLOTS`, () => {
        const front = PERSONAS.find((p) => p.value === persona)!.slots.filter((s) => s.required).map((s) => s.key);
        assert.deepEqual([...front].sort(), [...backendRequired(file)].sort());
    });
}

test("lead_gen no longer requires pricing or differentiator", () => {
    const keys = PERSONAS.find((p) => p.value === "lead_gen")!.slots.filter((s) => s.required).map((s) => s.key);
    assert.ok(!keys.includes("pricing_info"));
    assert.ok(!keys.includes("company_differentiator"));
});

// The calling agent does not book into a calendar, and a callback is scheduled
// only on outgoing campaigns (backend action_execution.py), so no card or field
// label may say the agent books anything.
test("no persona summary or field label says the agent books something", () => {
    for (const p of PERSONAS) {
        assert.doesNotMatch(p.summary, /book/i, `${p.value} summary`);
        for (const s of p.slots) assert.doesNotMatch(s.label, /book/i, `${p.value}.${s.key} label`);
    }
});

test("the lead_gen next-step field keeps its backend key and shows the new label", () => {
    const slot = PERSONAS.find((p) => p.value === "lead_gen")!.slots.find((s) => s.key === "calendar_booking_type");
    assert.equal(slot?.label, "Next step you offer");
});
