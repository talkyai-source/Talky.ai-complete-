/**
 * FX3b2 Task 1.2 — the post-call form names the conversation "transcript",
 * the name Call History uses, not "AI script".
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React, { useState } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CallHistoryFormButton } from "@/components/calls/call-history-workflow-controls";
import type { CallHistoryFormData } from "@/lib/call-history-workflow";
import { EMPTY_CALL_HISTORY_FORM } from "@/lib/call-history-workflow";
import { ensureDom } from "@/test-utils/dom";

ensureDom();
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
afterEach(() => cleanup());

test("FX3b2 1.2: the post-call form says 'transcript', not 'AI script'", async () => {
    function Harness() {
        const [value, setValue] = useState<CallHistoryFormData>({ ...EMPTY_CALL_HISTORY_FORM });
        return <CallHistoryFormButton value={value} onChange={setValue} callLabel="+15550001111" showLabel />;
    }
    render(<Harness />);
    const user = userEvent.setup({ document });
    await user.click(screen.getByRole("button", { name: /open form for/i }));
    assert.equal(screen.queryAllByText("Capture the key details from the transcript for +15550001111.").length > 0, true);
    assert.equal(/AI script/i.test(document.body.textContent ?? ""), false);
});
