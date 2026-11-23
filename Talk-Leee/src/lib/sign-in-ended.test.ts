import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { forgetSignIn, noteSignInEnded, rememberSignedIn, takeSignInEnded } from "@/lib/sign-in-ended";

const KEY = "talky.signin.tab";
const realNow = Date.now;
afterEach(() => { Date.now = realNow; sessionStorage.clear(); window.history.pushState({}, "", "/"); });

test("a tab that never held a sign-in is not marked", () => {
    noteSignInEnded();
    assert.equal(sessionStorage.getItem(KEY), null);
    assert.equal(takeSignInEnded(), false);
});

test("a signed-in tab is marked once and the marker is consumed", () => {
    rememberSignedIn();
    noteSignInEnded();
    assert.equal(takeSignInEnded(), true);
    assert.equal(takeSignInEnded(), false);
    noteSignInEnded();
    assert.equal(takeSignInEnded(), false);
});

test("a signed-in value alone is never reported and is left in place", () => {
    rememberSignedIn();
    assert.equal(takeSignInEnded(), false);
    assert.equal(sessionStorage.getItem(KEY), "signed-in");
});

test("forgetting the sign-in first means a later end gives nothing", () => {
    rememberSignedIn();
    forgetSignIn();
    noteSignInEnded();
    assert.equal(takeSignInEnded(), false);
});

test("a 31-second-old marker gives nothing and is removed; 29 seconds still counts", () => {
    const start = realNow.call(Date);
    Date.now = () => start;
    rememberSignedIn();
    noteSignInEnded();
    Date.now = () => start + 31_000;
    assert.equal(takeSignInEnded(), false);
    assert.equal(sessionStorage.getItem(KEY), null);
    Date.now = () => start;
    rememberSignedIn();
    noteSignInEnded();
    Date.now = () => start + 29_000;
    assert.equal(takeSignInEnded(), true);
});

test("blocked storage gives no line and throws nothing", () => {
    const desc = Object.getOwnPropertyDescriptor(window, "sessionStorage");
    Object.defineProperty(window, "sessionStorage", { configurable: true, get() { throw new Error("blocked"); } });
    try {
        assert.doesNotThrow(() => { rememberSignedIn(); noteSignInEnded(); forgetSignIn(); });
        assert.equal(takeSignInEnded(), false);
    } finally {
        if (desc) Object.defineProperty(window, "sessionStorage", desc);
    }
});

test("a sign-in that ends while the tab is already on the sign-in page is forgotten, not marked", () => {
    // Found by review: the page has read the marker before the start-up check answers, so a mark
    // written then would only show on a later reload.
    window.history.pushState({}, "", "/auth/login");
    rememberSignedIn();
    noteSignInEnded();
    assert.equal(sessionStorage.getItem(KEY), null);
    assert.equal(takeSignInEnded(), false);
});

test("any other page still marks the tab", () => {
    for (const path of ["/dashboard", "/auth/register", "/auth/loginx"]) {
        window.history.pushState({}, "", path);
        rememberSignedIn();
        noteSignInEnded();
        assert.equal(takeSignInEnded(), true, path);
    }
});
