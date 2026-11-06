import { mock, test } from "node:test";
import assert from "node:assert/strict";
import {
    connectorCardActionFromStatus,
    connectorCardActionLabel,
    connectorOAuthErrorSentence,
    extractAuthorizationUrl,
    parseConnectorsCallback,
    formatLastSync,
    watchOAuthPopup,
} from "@/lib/connectors-utils";

test("connectorCardActionFromStatus maps all states", () => {
    assert.equal(connectorCardActionFromStatus("connected"), "disconnect");
    assert.equal(connectorCardActionFromStatus("disconnected"), "connect");
    assert.equal(connectorCardActionFromStatus("expired"), "reconnect");
    assert.equal(connectorCardActionFromStatus("error"), "reconnect");
    assert.equal(connectorCardActionFromStatus("unknown"), "connect");
});

test("connectorCardActionLabel renders user-facing labels", () => {
    assert.equal(connectorCardActionLabel("connect"), "Connect");
    assert.equal(connectorCardActionLabel("reconnect"), "Reconnect");
    assert.equal(connectorCardActionLabel("disconnect"), "Disconnect");
});

test("extractAuthorizationUrl accepts multiple response shapes", () => {
    assert.equal(extractAuthorizationUrl(" https://example.com/auth "), "https://example.com/auth");
    assert.equal(extractAuthorizationUrl({ authorization_url: "https://example.com/a" }), "https://example.com/a");
    assert.equal(extractAuthorizationUrl({ url: "https://example.com/b" }), "https://example.com/b");
    assert.throws(() => extractAuthorizationUrl({}), /Authorization failed/i);
});

test("parseConnectorsCallback handles success and failure", () => {
    const ok = parseConnectorsCallback(new URLSearchParams("status=success&type=calendar"));
    assert.equal(ok.ok, true);
    assert.equal(ok.providerType, "calendar");
    assert.match(ok.message, /connected successfully/i);

    const fail = parseConnectorsCallback(new URLSearchParams("status=error&type=email&error=denied"));
    assert.equal(fail.ok, false);
    assert.equal(fail.providerType, "email");
    assert.equal(fail.message, "The connection couldn't be completed. Try again. (denied)");
});

const GENERIC = "The connection couldn't be completed. Try again.";
const EXPIRED_ATTEMPT = "This connection attempt expired. Start again from Connectors.";
const PERMISSIONS = "Not all permissions were granted. Reconnect and allow everything requested.";

test("a return with no status is not a success and shows the plain sentence", () => {
    for (const query of ["", "type=calendar", "provider=google_calendar&type=calendar"]) {
        const bare = parseConnectorsCallback(new URLSearchParams(query));
        assert.equal(bare.ok, false, `"${query}" must not read as connected`);
        assert.equal(bare.message, GENERIC);
    }
});

test("only the backend's status=success is a success", () => {
    assert.equal(parseConnectorsCallback(new URLSearchParams("status=success&type=crm")).ok, true);
    for (const query of ["ok=1", "success=true", "ok=yes", "status=ok", "status=pending", "status=connected", "error=access_denied"]) {
        const res = parseConnectorsCallback(new URLSearchParams(query));
        assert.equal(res.ok, false, `"${query}" must not read as connected`);
        assert.equal(res.message, GENERIC);
    }
});

test("every backend return code maps to its sentence", () => {
    const expected: Record<string, string> = {
        access_denied: "Access wasn't granted. Try again and allow access.",
        invalid_state: EXPIRED_ATTEMPT,
        authorization_changed: EXPIRED_ATTEMPT,
        connector_mismatch: EXPIRED_ATTEMPT,
        connector_not_found: EXPIRED_ATTEMPT,
        missing_code: EXPIRED_ATTEMPT,
        insufficient_scope: PERMISSIONS,
        missing_refresh_token: PERMISSIONS,
        capability_check_failed: `${GENERIC} (capability_check_failed)`,
        token_store_failed: `${GENERIC} (token_store_failed)`,
        callback_failed: `${GENERIC} (callback_failed)`,
        // Any provider error value the backend passes through.
        server_error: `${GENERIC} (server_error)`,
        temporarily_unavailable: `${GENERIC} (temporarily_unavailable)`,
    };
    for (const [code, sentence] of Object.entries(expected)) {
        assert.equal(connectorOAuthErrorSentence(code), sentence, code);
        const parsed = parseConnectorsCallback(new URLSearchParams({ status: "error", type: "email", error: code }));
        assert.equal(parsed.ok, false, code);
        assert.equal(parsed.message, sentence, code);
    }
});

test("a failure with no code, or a value that is not a code, shows the sentence without brackets", () => {
    assert.equal(parseConnectorsCallback(new URLSearchParams("status=error")).message, GENERIC);
    assert.equal(connectorOAuthErrorSentence(""), GENERIC);
    assert.equal(connectorOAuthErrorSentence(null), GENERIC);
    assert.equal(connectorOAuthErrorSentence("Call 0800 000 000 to fix your account"), GENERIC);
    assert.equal(connectorOAuthErrorSentence("<b>x</b>"), GENERIC);
    assert.equal(connectorOAuthErrorSentence("toString"), `${GENERIC} (toString)`);
});

type FakePopup = { closed: boolean; where: "blank" | "provider" | "return-page" };

function fakePopup(): { popup: FakePopup; win: Window } {
    const popup: FakePopup = { closed: false, where: "blank" };
    const win = {
        get closed() {
            return popup.closed;
        },
        location: {
            get href() {
                if (popup.where === "provider") throw new Error("SecurityError: cross-origin");
                return popup.where === "blank" ? "about:blank" : "http://localhost/connectors/callback?status=error";
            },
        },
    } as unknown as Window;
    return { popup, win };
}

test("watchOAuthPopup reports a window closed while on the provider's pages, once", () => {
    mock.timers.enable({ apis: ["setInterval"] });
    try {
        const { popup, win } = fakePopup();
        let calls = 0;
        watchOAuthPopup(win, () => { calls += 1; }, 500);
        mock.timers.tick(500);
        popup.where = "provider";
        mock.timers.tick(500);
        assert.equal(calls, 0, "an open window is not a closed one");
        popup.closed = true;
        mock.timers.tick(500);
        assert.equal(calls, 1);
        mock.timers.tick(5000);
        assert.equal(calls, 1, "the watch ends after the close");
    } finally {
        mock.timers.reset();
    }
});

test("watchOAuthPopup stays silent when the handle reads closed without ever being seen on the provider", () => {
    // What the opener sees under Cross-Origin-Opener-Policy: same-origin while
    // the window is in fact still open (measured in Chromium).
    mock.timers.enable({ apis: ["setInterval"] });
    try {
        const { popup, win } = fakePopup();
        let calls = 0;
        watchOAuthPopup(win, () => { calls += 1; }, 500);
        mock.timers.tick(500);
        popup.closed = true;
        mock.timers.tick(5000);
        assert.equal(calls, 0);
    } finally {
        mock.timers.reset();
    }
});

test("watchOAuthPopup stays silent when the window is closed on the return page, or after the watch is stopped", () => {
    mock.timers.enable({ apis: ["setInterval"] });
    try {
        const finished = fakePopup();
        let calls = 0;
        watchOAuthPopup(finished.win, () => { calls += 1; }, 500);
        finished.popup.where = "provider";
        mock.timers.tick(500);
        finished.popup.where = "return-page";
        mock.timers.tick(500);
        finished.popup.closed = true;
        mock.timers.tick(500);
        assert.equal(calls, 0, "the attempt finished; it was not closed before finishing");

        const stopped = fakePopup();
        const stop = watchOAuthPopup(stopped.win, () => { calls += 1; }, 500);
        stopped.popup.where = "provider";
        mock.timers.tick(500);
        stop();
        stopped.popup.closed = true;
        mock.timers.tick(5000);
        assert.equal(calls, 0, "a return message stops the watch");
    } finally {
        mock.timers.reset();
    }
});

test("parseConnectorsCallback uses default providerType when missing from query", () => {
    const ok = parseConnectorsCallback(new URLSearchParams("status=success"), "drive");
    assert.equal(ok.ok, true);
    assert.equal(ok.providerType, "drive");
});

test("formatLastSync is stable for empty and invalid values", () => {
    assert.equal(formatLastSync(undefined), "—");
    assert.equal(formatLastSync(null), "—");
    assert.equal(formatLastSync("not-a-date"), "not-a-date");
    assert.ok(formatLastSync("2025-01-01T00:00:00Z").length > 0);
});
