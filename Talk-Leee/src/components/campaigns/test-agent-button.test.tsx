import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { sharedHttpClient } from "@/lib/api";

// The real component runs. Only auth identity and the portal Modal are stubbed;
// WebSocket, microphone and AudioContext are fakes the test controls.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("lib/auth-context")) return { useAuth: () => ({ status: "authenticated" }) };
    if (id.includes("lib/auth-hooks")) return { useAccessToken: () => null };
    if (id.includes("components/ui/modal")) {
        return {
            Modal: ({ open, children, footer }: { open: boolean; children: React.ReactNode; footer: React.ReactNode }) =>
                open ? <div>{children}{footer}</div> : null,
        };
    }
    return originalLoad.call(this, id, ...args);
};
let TestAgentButton: React.ComponentType<{ campaignId: string }>;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    TestAgentButton = require("./test-agent-button").TestAgentButton;
} finally {
    loader._load = originalLoad;
}

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (error: Error) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

class FakeWebSocket {
    static OPEN = 1;
    static instances: FakeWebSocket[] = [];
    readyState = 1;
    binaryType = "";
    sent: unknown[] = [];
    closed = false;
    onopen: (() => void) | null = null;
    onmessage: ((e: { data: unknown }) => void) | null = null;
    onerror: (() => void) | null = null;
    onclose: ((e: { code: number }) => void) | null = null;
    constructor(public url: string) { FakeWebSocket.instances.push(this); }
    send(data: unknown) { this.sent.push(data); }
    close() { this.closed = true; this.readyState = 3; }
    serverSends(frame: Record<string, unknown>) { this.onmessage?.({ data: JSON.stringify(frame) }); }
}

class FakeTrack { stopped = false; stop() { this.stopped = true; } }
const streams: { getTracks: () => FakeTrack[]; tracks: FakeTrack[] }[] = [];
function fakeStream() {
    const tracks = [new FakeTrack()];
    const stream = { tracks, getTracks: () => tracks };
    streams.push(stream);
    return stream;
}

class FakeAudioContext {
    static instances: FakeAudioContext[] = [];
    state = "running";
    closed = false;
    destination = {};
    constructor() { FakeAudioContext.instances.push(this); }
    suspends = 0;
    resumes = 0;
    resume() { this.resumes += 1; this.state = "running"; return Promise.resolve(); }
    suspend() { this.suspends += 1; this.state = "suspended"; return Promise.resolve(); }
    close() { this.closed = true; return Promise.resolve(); }
    createMediaStreamSource() { return { connect() {} }; }
    createScriptProcessor() { return { connect() {}, disconnect() {}, onaudioprocess: null }; }
    createGain() { return { gain: { value: 1 }, connect() {} }; }
}

const g = globalThis as unknown as Record<string, unknown>;
const saved: Record<string, unknown> = {};
let getUserMedia: () => Promise<unknown>;

beforeEach(() => {
    FakeWebSocket.instances = [];
    FakeAudioContext.instances = [];
    streams.length = 0;
    for (const key of ["WebSocket", "AudioContext", "AudioWorkletNode"]) saved[key] = g[key];
    g.WebSocket = FakeWebSocket;
    g.AudioContext = FakeAudioContext;
    g.AudioWorkletNode = class {};
    getUserMedia = async () => fakeStream();
    Object.defineProperty(globalThis.navigator, "mediaDevices", {
        configurable: true,
        value: { getUserMedia: () => getUserMedia() },
    });
});

afterEach(() => {
    cleanup();
    mock.restoreAll();
    for (const key of Object.keys(saved)) g[key] = saved[key];
});

function startTest() {
    fireEvent.click(screen.getByRole("button", { name: /Test agent/ }));
    fireEvent.click(screen.getByRole("button", { name: /Start test/ }));
}

// Generous: the full suite runs many files in parallel on a loaded machine.
const SLOW = 15_000;

const READY = { type: "ready", pipeline_mode: "cascaded", sample_rate: 24000, input_sample_rate: 16000 };

test("unmounting during the /auth/me pre-flight never opens a socket", async () => {
    const preflight = deferred<unknown>();
    mock.method(sharedHttpClient(), "request", () => preflight.promise);
    const view = render(<TestAgentButton campaignId="camp-1" />);
    startTest();
    await screen.findByRole("button", { name: /Connecting/ }, { timeout: SLOW });
    view.unmount();
    await act(async () => { preflight.resolve({}); await preflight.promise; });
    assert.equal(FakeWebSocket.instances.length, 0, "a socket was opened after the component unmounted");
});

test("End test during the /auth/me pre-flight does not start a hidden session", async () => {
    const preflight = deferred<unknown>();
    mock.method(sharedHttpClient(), "request", () => preflight.promise);
    render(<TestAgentButton campaignId="camp-1" />);
    startTest();
    fireEvent.click(await screen.findByRole("button", { name: /Connecting/ }, { timeout: SLOW }));
    await act(async () => { preflight.resolve({}); await preflight.promise; });
    assert.equal(FakeWebSocket.instances.length, 0, "a socket was opened after End test");
    assert.ok(screen.getByRole("button", { name: /Test agent/ }));
});

for (const how of ["End test", "unmount"] as const) {
    test(`${how} while the microphone permission prompt is open releases the microphone`, async () => {
        mock.method(sharedHttpClient(), "request", async () => ({}));
        const prompt = deferred<unknown>();
        getUserMedia = () => prompt.promise;
        const view = render(<TestAgentButton campaignId="camp-1" />);
        startTest();
        await waitFor(() => assert.equal(FakeWebSocket.instances.length, 1), { timeout: SLOW });
        const ws = FakeWebSocket.instances[0];
        await act(async () => { ws.serverSends(READY); });
        if (how === "End test") fireEvent.click(screen.getByRole("button", { name: /Agent speaking|Listening|End test/ }));
        else view.unmount();
        const stream = fakeStream();
        await act(async () => { prompt.resolve(stream); await prompt.promise; });
        assert.ok(stream.tracks.every((t) => t.stopped), "microphone track is still live after the session ended");
        assert.ok(FakeAudioContext.instances.every((c) => c.closed), "microphone AudioContext left open");
    });
}

test("a non-closing server error frame ends the session and releases the microphone", async () => {
    mock.method(sharedHttpClient(), "request", async () => ({}));
    render(<TestAgentButton campaignId="camp-1" />);
    startTest();
    await waitFor(() => assert.equal(FakeWebSocket.instances.length, 1), { timeout: SLOW });
    const ws = FakeWebSocket.instances[0];
    await act(async () => { ws.serverSends(READY); });
    await waitFor(() => assert.equal(streams.length, 1), { timeout: SLOW });
    await act(async () => { ws.serverSends({ type: "error", message: "pipeline crashed" }); });
    assert.ok(ws.closed, "socket stayed open after an error frame");
    assert.ok(streams[0].tracks.every((t) => t.stopped), "microphone stayed live after an error frame");
    assert.ok(screen.getByRole("button", { name: /Test agent/ }));
    assert.match(screen.getByRole("alert").textContent || "", /pipeline crashed/);
});

test("the auth_required retry still opens the second socket after the refresh", async () => {
    mock.method(sharedHttpClient(), "request", async () => ({}));
    render(<TestAgentButton campaignId="camp-1" />);
    startTest();
    await waitFor(() => assert.equal(FakeWebSocket.instances.length, 1), { timeout: SLOW });
    await act(async () => { FakeWebSocket.instances[0].serverSends({ type: "error", code: "auth_required" }); });
    await waitFor(() => assert.equal(FakeWebSocket.instances.length, 2), { timeout: SLOW });
    await act(async () => { FakeWebSocket.instances[1].serverSends(READY); });
    await waitFor(() => assert.equal(streams.length, 1), { timeout: SLOW });
    assert.ok(streams[0].tracks.every((t) => !t.stopped), "retry session must keep its microphone");
});

test("the agent holds on tts_pause, continues on tts_resume, and an interruption clears the hold", async () => {
    // Pause-then-decide barge-in (test call 850b885e, 2026-10-13): the server
    // holds the agent the moment the caller starts talking.
    mock.method(sharedHttpClient(), "request", async () => ({}));
    render(<TestAgentButton campaignId="camp-1" />);
    startTest();
    await waitFor(() => assert.equal(FakeWebSocket.instances.length, 1), { timeout: SLOW });
    const ws = FakeWebSocket.instances[0];
    await act(async () => { ws.serverSends(READY); });
    // The player starts with the agent's first audio (a few ms, below the jitter buffer).
    await act(async () => { ws.onmessage?.({ data: new ArrayBuffer(160) }); });
    const total = (key: "suspends" | "resumes") => FakeAudioContext.instances.reduce((n, c) => n + c[key], 0);
    const resumesBefore = total("resumes");
    await act(async () => { ws.serverSends({ type: "tts_pause" }); });
    assert.equal(total("suspends"), 1);
    await act(async () => { ws.serverSends({ type: "tts_resume" }); });
    assert.equal(total("resumes"), resumesBefore + 1);
    await act(async () => { ws.serverSends({ type: "tts_pause" }); });
    await act(async () => { ws.serverSends({ type: "tts_interrupted", reason: "barge_in" }); });
    assert.equal(total("suspends"), 2);
    assert.equal(total("resumes"), resumesBefore + 2, "an interruption must leave the player able to play the next reply");
});

test("each finished turn shows how fast the agent replied", async () => {
    mock.method(sharedHttpClient(), "request", async () => ({}));
    render(<TestAgentButton campaignId="camp-1" />);
    startTest();
    await waitFor(() => assert.equal(FakeWebSocket.instances.length, 1), { timeout: SLOW });
    const ws = FakeWebSocket.instances[0];
    await act(async () => { ws.serverSends(READY); });
    await act(async () => {
        ws.serverSends({
            type: "turn_complete", turn_id: 1,
            latency: { reply_ms: 1240, end_of_turn_ms: 500, ai_first_word_ms: 300, voice_first_audio_ms: 200, other_ms: 240 },
        });
    });
    assert.match(screen.getByTestId("test-reply-time").textContent || "", /Last reply: 1\.24 s/);
});
