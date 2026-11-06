import assert from "node:assert/strict";
import { test } from "node:test";

import {
    campaignDisplayStatus,
    campaignStatusFilterOptions,
    campaignStatusLabel,
    migrateStatusFilterLabels,
} from "@/lib/campaign-status";

test("the five statuses the backend uses map to their exact labels", () => {
    assert.equal(campaignStatusLabel("draft"), "Draft");
    assert.equal(campaignStatusLabel("running"), "Running");
    assert.equal(campaignStatusLabel("paused"), "Paused");
    assert.equal(campaignStatusLabel("completed"), "Completed");
    assert.equal(campaignStatusLabel("stopped"), "Stopped");
});

test("running is never Active and stopped is never Failed", () => {
    assert.notEqual(campaignStatusLabel("running"), "Active");
    assert.notEqual(campaignStatusLabel("stopped"), "Failed");
});

test("any other backend value shows its own name with a capital first letter, never Draft", () => {
    assert.equal(campaignStatusLabel("scheduled"), "Scheduled");
    assert.equal(campaignStatusLabel("cancelled"), "Cancelled");
    assert.equal(campaignStatusLabel("archived"), "Archived");
    assert.notEqual(campaignStatusLabel("scheduled"), "Draft");
    assert.equal(campaignStatusLabel(""), "");
    assert.equal(campaignStatusLabel(undefined), "");
});

test("Failed is shown only when the backend sends failed", () => {
    assert.equal(campaignStatusLabel("failed"), "Failed");
    for (const status of ["draft", "running", "paused", "completed", "stopped", "scheduled", "cancelled"]) {
        assert.notEqual(campaignStatusLabel(status), "Failed");
    }
});

test("a start in flight reads Preparing and a stop in flight reads Stopping", () => {
    assert.equal(campaignDisplayStatus("paused", { starting: true }), "Preparing");
    assert.equal(campaignDisplayStatus("running", { stopping: true }), "Stopping");
    assert.equal(campaignDisplayStatus("running", {}), "Running");
    assert.equal(campaignDisplayStatus("stopped"), "Stopped");
});

test("the status filter offers the five labels plus any other status the backend sent", () => {
    assert.deepEqual(campaignStatusFilterOptions([]), ["Draft", "Running", "Paused", "Completed", "Stopped"]);
    assert.deepEqual(
        campaignStatusFilterOptions(["running", "scheduled", "stopped", "scheduled", "failed"]),
        ["Draft", "Running", "Paused", "Completed", "Stopped", "Scheduled", "Failed"],
    );
});

test("a status filter saved under the old labels keeps matching the same campaigns", () => {
    assert.deepEqual(migrateStatusFilterLabels(["Active", "Paused", "Failed"]), ["Running", "Paused", "Stopped"]);
    assert.deepEqual(migrateStatusFilterLabels(["Running", "Active"]), ["Running"]);
    assert.deepEqual(migrateStatusFilterLabels(undefined), []);
});
