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

  test("toggles play state on button click", async () => {
    const mockVoices = [
        {
          id: "sarah",
          name: "Sarah",
          description: "Professional female voice",
          initial: "S",
          color: "text-indigo-600",
          bg: "bg-indigo-50",
          previewUrl: "/audio/sarah.mp3",
        }
    ];

    global.fetch = async () => ({
      ok: true,
      json: async () => mockVoices,
    }) as unknown as Promise<Response>;

    renderPage();

    await waitFor(() => {
      assert.ok(screen.getByText("Sarah"));
    });

    const buttons = screen.getAllByText("Preview Voice");
    fireEvent.click(buttons[0]);

    await waitFor(() => {
      assert.ok(screen.getByText("Stop Preview"));
    });

    fireEvent.click(screen.getByText("Stop Preview"));

    await waitFor(() => {
      // It should revert to "Preview Voice"
      // Since there is only one button, getAllByText might return 1 or 0 depending on timing.
      // But we can check if "Preview Voice" exists.
      assert.ok(screen.getAllByText("Preview Voice").length > 0);
    });
  });
});
