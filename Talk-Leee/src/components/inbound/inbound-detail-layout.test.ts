import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

// Structural guards supplement the browser overflow/duplicate-editor checks.
// They do not claim that jsdom or an AST can measure a rendered layout.
const source = readFileSync(new URL("../../app/inbound-campaigns/[id]/page.tsx", import.meta.url), "utf8");
const page = ts.createSourceFile("page.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const elements: Array<ts.JsxOpeningElement | ts.JsxSelfClosingElement> = [];
function collect(node: ts.Node) {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) elements.push(node);
    ts.forEachChild(node, collect);
}
collect(page);

test("inbound detail mounts exactly one campaign-owned knowledge editor", () => {
    const editors = elements.filter(node => node.tagName.getText(page) === "KnowledgePanel");
    assert.equal(editors.length, 1);
    assert.match(editors[0].getText(page), /campaignId=\{campaign\.campaign_id\}/);
    assert.match(editors[0].getText(page), /readOnly=\{!canManageKnowledge\}/);
});

test("free-form inbound purpose wraps unbroken references instead of bleeding into other fields", () => {
    const purpose = source.match(/<p[^>]+>\{campaign\.purpose\}<\/p>/)?.[0];
    assert.ok(purpose);
    assert.match(purpose, /\[overflow-wrap:anywhere\]/);
});

test("inbound identifiers keep their original casing and test guidance is available", () => {
    assert.doesNotMatch(source.slice(source.indexOf("function Info(")), /\bcapitalize\b/);
    assert.match(source, /How to test inbound calling/);
    assert.match(source, /separate mobile or landline/);
    assert.match(source, /do not prove a successful telephone call/);
});
