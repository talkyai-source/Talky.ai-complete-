import { test, afterEach, beforeEach, mock } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render, waitFor } from "@testing-library/react";
import { NavbarHeroBackgroundVideo } from "./home-lazy-sections";

// QA F1-16 (QA-UI-165): with the OS "reduce motion" setting on, the home-page
// hero video still autoplayed. It now stays on its poster until motion is
// allowed; otherwise playback is unchanged.
type HeroWindow = Window & { __heroVideo?: unknown; __heroVideoUrl?: string };
const originalFetch = global.fetch;
const originalMatchMedia = window.matchMedia;

function setReducedMotion(reduce: boolean) {
    window.matchMedia = ((query: string) => ({
        matches: reduce && query.includes("reduce"),
        media: query,
        addEventListener() {},
        removeEventListener() {},
    })) as unknown as typeof window.matchMedia;
}

beforeEach(() => {
    delete (window as HeroWindow).__heroVideo;
    delete (window as HeroWindow).__heroVideoUrl;
    global.fetch = (async () => new Response(null, { status: 404 })) as typeof fetch;
});

afterEach(() => {
    cleanup();
    mock.restoreAll();
    global.fetch = originalFetch;
    window.matchMedia = originalMatchMedia;
});

test("with reduce motion on, the hero never autoplays or plays", async () => {
    setReducedMotion(true);
    const play = mock.method(window.HTMLMediaElement.prototype, "play", () => Promise.resolve());
    mock.method(window.HTMLMediaElement.prototype, "pause", () => {});
    const { container } = render(<NavbarHeroBackgroundVideo />);
    await waitFor(() => assert.ok(container.querySelector("video")?.getAttribute("src")));
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(container.querySelectorAll("video[autoplay]").length, 0, "no autoplay attribute");
    assert.equal(play.mock.callCount(), 0, "play() was called");
});

test("without reduce motion, the hero plays once it has its video", async () => {
    setReducedMotion(false);
    const play = mock.method(window.HTMLMediaElement.prototype, "play", () => Promise.resolve());
    mock.method(window.HTMLMediaElement.prototype, "pause", () => {});
    render(<NavbarHeroBackgroundVideo />);
    await waitFor(() => assert.ok(play.mock.callCount() >= 1, "play() was never called"));
});
