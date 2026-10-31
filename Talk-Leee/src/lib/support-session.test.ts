import assert from "node:assert/strict";
import { test } from "node:test";
import { clearSupportConversations, readSupportConversation, supportSessionKey, writeSupportConversation } from "@/lib/support-session";

const ada = { tenantId: "tenant-1", userId: "user-ada" };
const bob = { tenantId: "tenant-1", userId: "user-bob" };

test("the key names the tenant and the user", () => {
    assert.equal(supportSessionKey(ada), "talklee.support.conversation.v1:tenant-1:user-ada");
});

test("a conversation is stored per identity and cleared on sign-out", () => {
    window.sessionStorage.clear();
    writeSupportConversation(ada, "conv-1");
    assert.equal(readSupportConversation(ada), "conv-1");
    assert.equal(readSupportConversation(bob), null, "another user in the same tab never resumes it");
    writeSupportConversation(ada, null);
    assert.equal(readSupportConversation(ada), null);
    writeSupportConversation(ada, "conv-2");
    writeSupportConversation(bob, "conv-3");
    window.sessionStorage.setItem("talklee.sidebar.state.v1", "{}");
    clearSupportConversations();
    assert.equal(readSupportConversation(ada), null);
    assert.equal(readSupportConversation(bob), null);
    assert.equal(window.sessionStorage.getItem("talklee.sidebar.state.v1"), "{}", "unrelated keys stay");
});
