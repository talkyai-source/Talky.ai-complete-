/**
 * Structural guard — the frozen state register and the TypeScript unions
 * say the same thing, in both directions.
 *
 * The 1.0.0 work proved the 18 named states matched the code by diffing
 * them once, by hand, at freeze time. That check does not survive the next
 * edit. This test makes the diff continuous: adding a state to
 * `inbound-types.ts` without recording it in the spec fails, and recording
 * one in the spec without declaring it in code fails too.
 *
 * Register: docs/inbound/SPEC-inbound-v2.0.0-FROZEN.md §5.5
 * Unions:   src/lib/inbound/inbound-types.ts
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
    INBOUND_MAPPED_ERROR_CODES,
    INBOUND_OPERATION_STATES,
    inboundStateForError,
} from "@/lib/inbound/inbound-types";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.join(HERE, "..", "..");
const SPEC = path.join(
    HERE,
    "..",
    "..",
    "..",
    "docs",
    "inbound",
    "SPEC-inbound-v2.0.0-FROZEN.md",
);

/** The union names the register is allowed to reference. */
const UNIONS = [
    "InboundSectionState",
    "CallHistoryState",
    "WizardStepState",
    "InboundOperationState",
] as const;

type UnionName = (typeof UNIONS)[number];

/** `union: value` lines inside the ```state-register fence. */
function registerStates(): Map<UnionName, Set<string>> {
    const doc = readFileSync(SPEC, "utf8");
    const fence = /```state-register\r?\n([\s\S]*?)```/.exec(doc);
    assert.ok(fence, "SPEC-inbound-v2.0.0-FROZEN.md §5.5 must hold a state-register block");

    const out = new Map<UnionName, Set<string>>(UNIONS.map((name) => [name, new Set<string>()]));
    for (const line of fence[1].split(/\r?\n/)) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        const match = /^([A-Za-z]+):\s*([a-z-]+)$/.exec(trimmed);
        assert.ok(match, `state-register line is malformed: "${trimmed}"`);
        const union = match[1] as UnionName;
        assert.ok(out.has(union), `state-register names an unknown union: "${union}"`);
        out.get(union)!.add(match[2]);
    }
    return out;
}

/**
 * The members of one string-literal union in inbound-types.ts.
 *
 * Read from source rather than imported because a type has no runtime
 * representation — the whole point is to catch a union edited without the
 * register, and only the text can show that.
 */
function unionMembers(name: UnionName): Set<string> {
    const source = readFileSync(path.join(SRC, "lib/inbound/inbound-types.ts"), "utf8");
    const declaration = new RegExp(`export type ${name} =([\\s\\S]*?);`).exec(source);
    assert.ok(declaration, `inbound-types.ts must declare "export type ${name}"`);

    const members = new Set<string>();
    // Strip comments first: several members carry a trailing `// -> state`
    // note, and those arrows must not be read as union members.
    const body = declaration[1]
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/\/\/[^\n]*/g, "");
    for (const quoted of body.matchAll(/"([a-z-]+)"/g)) {
        members.add(quoted[1]);
    }
    assert.ok(members.size > 0, `no members parsed from ${name}`);
    return members;
}

test("every state in the frozen register is declared in inbound-types.ts", () => {
    const register = registerStates();
    const missing: string[] = [];

    for (const union of UNIONS) {
        const declared = unionMembers(union);
        for (const state of register.get(union)!) {
            if (!declared.has(state)) missing.push(`${union}: ${state}`);
        }
    }

    assert.deepEqual(missing, [], "states frozen in the spec but absent from the union");
});

test("every state declared in inbound-types.ts appears in the frozen register", () => {
    const register = registerStates();
    const unrecorded: string[] = [];

    for (const union of UNIONS) {
        const recorded = register.get(union)!;
        for (const state of unionMembers(union)) {
            if (!recorded.has(state)) unrecorded.push(`${union}: ${state}`);
        }
    }

    assert.deepEqual(
        unrecorded,
        [],
        "states declared in code but not frozen in SPEC-inbound-v2.0.0-FROZEN.md §5.5",
    );
});

test("the register holds exactly 23 states: 18 carried from 1.0.0, 5 added at 2.0.0", () => {
    const register = registerStates();
    const total = UNIONS.reduce((sum, union) => sum + register.get(union)!.size, 0);
    assert.equal(total, 23);
    assert.equal(register.get("InboundOperationState")!.size, 5);
});

test("INBOUND_OPERATION_STATES matches the InboundOperationState union", () => {
    assert.deepEqual(
        [...INBOUND_OPERATION_STATES].sort(),
        [...unionMembers("InboundOperationState")].sort(),
    );
});

test("every mapped backend code resolves to exactly one state", () => {
    const seen = new Set<string>();
    for (const code of INBOUND_MAPPED_ERROR_CODES) {
        assert.ok(!seen.has(code), `"${code}" is mapped more than once`);
        seen.add(code);
        // Status is deliberately withheld: a code must be sufficient on its
        // own, so that a response whose status is masked by a proxy still
        // reaches the right state.
        const state = inboundStateForError(undefined, code);
        assert.notEqual(state, "error", `"${code}" fell through to the generic error state`);
    }
    assert.equal(seen.size, INBOUND_MAPPED_ERROR_CODES.length);
});

test("conflict, readiness and authorization codes are not conflated", () => {
    // 503 means the permission LOOKUP failed. Reporting it as no-permission
    // tells the user something false about their own access.
    assert.equal(inboundStateForError(503, "authorization_unavailable"), "authorization-unavailable");
    assert.equal(inboundStateForError(403, "permission_denied"), "no-permission");

    // not_ready carries a readiness payload and is a gate, not a collision,
    // even though the server sends it with a 409.
    assert.equal(inboundStateForError(409, "not_ready"), "activation-blocked");
    assert.equal(inboundStateForError(409, "version_conflict"), "conflict");

    // Field rejection is not a version problem.
    assert.equal(inboundStateForError(422, "invalid_did"), "rejected-by-server");
});

test("an unrecognised failure falls through to error rather than being guessed", () => {
    assert.equal(inboundStateForError(500, null), "error");
    assert.equal(inboundStateForError(undefined, "something_new"), "error");
    assert.equal(inboundStateForError(undefined, undefined), "error");
});

test("status alone is enough when the body carries no code", () => {
    assert.equal(inboundStateForError(403, null), "no-permission");
    assert.equal(inboundStateForError(404, null), "not-found");
    assert.equal(inboundStateForError(409, null), "conflict");
    assert.equal(inboundStateForError(412, null), "conflict");
    assert.equal(inboundStateForError(422, null), "rejected-by-server");
});
