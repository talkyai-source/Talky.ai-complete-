import { test, afterEach, beforeEach, describe } from "node:test";
import assert from "node:assert/strict";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import AiVoicesPage from "./page";
import { ThemeProvider } from "@/components/providers/theme-provider";
import { AuthProvider } from "@/lib/auth-context";

// Mock global fetch
const originalFetch = global.fetch;

/**
 * The page renders <Navbar>, which calls usePathname(), useRouter() and
 * useAuth(). Outside a Next app the first two throw "invariant expected app
 * router to be mounted" and the third throws "useAuth must be used within an
 * AuthProvider", so every test here died before it reached a voice.
 *
 * That is why these three tests failed the first time anyone ran them: they
 * never had. `npm test` used to leave file discovery to `node --test`, whose
 * default patterns cover js/mjs/cjs/ts/mts/cts and NOT tsx, so this file was
 * silently skipped from the day it was written. package.json now lists the
 * patterns explicitly, which is what surfaced this.
 *
 * Supplying the two contexts is the smallest honest fix: the assertions below
 * are untouched, and nothing about the page under test is stubbed out.
 */
function renderPage() {
  const router = {
    push: () => {},
    replace: () => {},
    refresh: () => {},
    back: () => {},
    forward: () => {},
    prefetch: async () => {},
  } as unknown as React.ContextType<typeof AppRouterContext>;

  return render(
    <AppRouterContext.Provider value={router}>
      <PathnameContext.Provider value="/ai-voices">
        <AuthProvider>
          <ThemeProvider>
            <AiVoicesPage />
          </ThemeProvider>
        </AuthProvider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>,
  );
}

describe("AiVoicesPage", () => {

  beforeEach(() => {
    // Reset fetch mock
    global.fetch = async () => ({
      ok: true,
      json: async () => [],
    }) as unknown as Promise<Response>;
  });

  afterEach(() => {
    cleanup();
    global.fetch = originalFetch;
  });

  test("renders voices after fetching", async () => {
    const mockVoices = [
      {
        id: "sarah",
        name: "Sarah",
        description: "Professional female voice",
        initial: "S",
        color: "text-indigo-600",
        bg: "bg-indigo-50",
        previewUrl: "/audio/sarah.mp3",
      },
      {
        id: "michael",
        name: "Michael",
        description: "Confident male voice",
        initial: "M",
        color: "text-emerald-600",
        bg: "bg-emerald-50",
        previewUrl: "/audio/michael.mp3",
      },
    ];

    global.fetch = async () => ({
      ok: true,
      json: async () => mockVoices,
    }) as unknown as Promise<Response>;

    renderPage();

    // Wait for "Sarah" to appear
    await waitFor(() => {
      const element = screen.getByText("Sarah");
      assert.ok(element);
    });

    assert.ok(screen.getByText("Michael"));
    assert.ok(screen.getByText("Professional female voice"));
  });

  test("handles fetch error", async () => {
    global.fetch = async () => {
       throw new Error("API Error");
    };

    renderPage();

    await waitFor(() => {
      assert.ok(screen.getByText("Error: API Error"));
    });
    
    assert.ok(screen.getByText("Retry"));
  });

  // QA F1-07: Preview used to flip to "Stop Preview" without playing anything.
  // A real clip is now played and stopped; a voice without one has no control.
  test("plays and stops the voice's real sample", async () => {
    const played: { src: string; paused: boolean }[] = [];
    class FakeAudio {
      src: string;
      paused = true;
      onended: (() => void) | null = null;
      constructor(src: string) {
        this.src = src;
        played.push(this);
      }
      play() {
        this.paused = false;
        return Promise.resolve();
      }
      pause() {
        this.paused = true;
      }
    }
    const originalAudio = globalThis.Audio;
    globalThis.Audio = FakeAudio as unknown as typeof Audio;
    try {
      global.fetch = async () => ({
        ok: true,
        json: async () => [{
          id: "sarah", name: "Sarah", description: "Professional female voice",
          initial: "S", color: "text-indigo-600", bg: "bg-indigo-50", previewUrl: "/audio/sarah.mp3",
        }],
      }) as unknown as Promise<Response>;

      renderPage();
      await waitFor(() => assert.ok(screen.getByText("Sarah")));

      fireEvent.click(screen.getByText("Preview Voice"));
      await waitFor(() => assert.ok(screen.getByText("Stop Preview")));
      assert.equal(played.length, 1);
      assert.equal(played[0].src, "/audio/sarah.mp3");
      assert.equal(played[0].paused, false);

      fireEvent.click(screen.getByText("Stop Preview"));
      await waitFor(() => assert.ok(screen.getByText("Preview Voice")));
      assert.equal(played[0].paused, true);
    } finally {
      globalThis.Audio = originalAudio;
    }
  });

  test("a voice without a sample has no preview control", async () => {
    global.fetch = async () => ({
      ok: true,
      json: async () => [{
        id: "ava", name: "Ava", description: "Warm voice",
        initial: "A", color: "text-indigo-600", bg: "bg-indigo-50", previewUrl: "",
      }],
    }) as unknown as Promise<Response>;

    renderPage();
    await waitFor(() => assert.ok(screen.getByText("Ava")));
    assert.equal(screen.queryByText("Preview Voice"), null);
    assert.equal(screen.queryByRole("button", { name: /Preview voice of Ava/ }), null);
  });
});
