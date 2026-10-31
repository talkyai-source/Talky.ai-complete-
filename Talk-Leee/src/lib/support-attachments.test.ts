import assert from "node:assert/strict";
import { test } from "node:test";
import {
    acceptAttribute, attachmentExtension, formatBytes, hasUploadInFlight, rejectAttachment, uploadedAttachmentIds,
    type PickedAttachment,
} from "@/lib/support-attachments";

const limits = { max_mb: 10, max_per_message: 5, types: ["png", "jpg", "webp", "pdf", "txt", "log", "csv"] };

test("the extension is read case-insensitively from the last dot", () => {
    assert.equal(attachmentExtension("Report.FINAL.PDF"), "pdf");
    assert.equal(attachmentExtension("noext"), "");
});

test("files over the configured limits are refused before upload", () => {
    assert.equal(rejectAttachment({ name: "a.png", size: 1024 }, limits, 0), null);
    assert.match(rejectAttachment({ name: "a.exe", size: 10 }, limits, 0) ?? "", /not an accepted type/);
    assert.match(rejectAttachment({ name: "a.png", size: 11 * 1024 * 1024 }, limits, 0) ?? "", /limit is 10 MB/);
    assert.match(rejectAttachment({ name: "a.png", size: 0 }, limits, 0) ?? "", /empty/);
    assert.match(rejectAttachment({ name: "a.png", size: 10 }, limits, 5) ?? "", /up to 5 files/);
});

test("a config with zero limits still accepts a plain file (the server decides)", () => {
    assert.equal(rejectAttachment({ name: "a.png", size: 10 }, { max_mb: 0, max_per_message: 0, types: [] }, 3), null);
});

test("the file input's accept attribute mirrors the configured types", () => {
    assert.equal(acceptAttribute(limits), ".png,.jpg,.webp,.pdf,.txt,.log,.csv");
    assert.equal(acceptAttribute({ ...limits, types: [] }), undefined);
});

test("only finished uploads contribute attachment ids", () => {
    const items: PickedAttachment[] = [
        { localId: "1", name: "a.png", sizeBytes: 1, status: "done", attachmentId: "att-1", error: null },
        { localId: "2", name: "b.png", sizeBytes: 1, status: "uploading", attachmentId: null, error: null },
        { localId: "3", name: "c.png", sizeBytes: 1, status: "failed", attachmentId: null, error: "nope" },
    ];
    assert.deepEqual(uploadedAttachmentIds(items), ["att-1"]);
    assert.equal(hasUploadInFlight(items), true);
    assert.equal(hasUploadInFlight(items.filter((item) => item.status !== "uploading")), false);
});

test("sizes read in B, KB and MB", () => {
    assert.equal(formatBytes(900), "900 B");
    assert.equal(formatBytes(20 * 1024), "20 KB");
    assert.equal(formatBytes(2.5 * 1024 * 1024), "2.5 MB");
});
