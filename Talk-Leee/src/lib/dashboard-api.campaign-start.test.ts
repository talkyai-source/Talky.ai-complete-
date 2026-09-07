import assert from "node:assert/strict";
import { test } from "node:test";

import { campaignStartErrorMessage } from "@/lib/dashboard-api";

/**
 * The out-of-minutes gate answers 402 with a structured detail whose `message`
 * already names the tenant's allocation and usage:
 *   backend/app/api/v1/endpoints/campaigns.py:657-671
 * The http client exposes that object as `.details`.
 */
function refusal(details: unknown): Error & { details?: unknown } {
    const error = new Error("Request failed with status 402") as Error & { details?: unknown };
    error.details = details;
    return error;
}

test("the server's own out-of-minutes sentence is what the user is shown", () => {
    const message =
        "You've used all 500 of your plan minutes this month (500 used). " +
        "Add minutes or upgrade your plan to start campaigns.";
    const shown = campaignStartErrorMessage(
        refusal({
            error: "out_of_minutes",
            message,
            allocated: 500,
            used_minutes: 500,
            remaining_minutes: 0,
        }),
    );
    assert.equal(shown, message);
    // The figures beside it are the server's to phrase; nothing is recomputed
    // from allocated/used_minutes/remaining_minutes here.
    assert.ok(shown.includes("500"));
});

test("a refusal carrying no server message falls back to the transport error", () => {
    assert.equal(
        campaignStartErrorMessage(refusal({ error: "out_of_minutes" })),
        "Request failed with status 402",
    );
    assert.equal(campaignStartErrorMessage(refusal(undefined)), "Request failed with status 402");
    // A blank or non-string message is not a message.
    assert.equal(campaignStartErrorMessage(refusal({ message: "   " })), "Request failed with status 402");
    assert.equal(campaignStartErrorMessage(refusal({ message: 42 })), "Request failed with status 402");
});

test("a failure carrying nothing at all still states what failed, and invents no reason", () => {
    assert.equal(campaignStartErrorMessage(null), "Failed to start campaign");
    assert.equal(campaignStartErrorMessage(undefined), "Failed to start campaign");
    assert.equal(campaignStartErrorMessage("boom"), "Failed to start campaign");
    assert.equal(campaignStartErrorMessage(new Error("   ")), "Failed to start campaign");
});
