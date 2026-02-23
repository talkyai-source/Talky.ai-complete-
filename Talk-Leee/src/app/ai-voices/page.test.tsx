import { test, afterEach, beforeEach, describe } from "node:test";
import assert from "node:assert/strict";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import AiVoicesPage from "./page";
import { ThemeProvider } from "@/components/providers/theme-provider";

// Mock global fetch
const originalFetch = global.fetch;

describe("AiVoicesPage", () => {
  // We need to handle the fact that Navbar uses Next.js Link which might fail outside Next.js
  // But let's try.
  
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

    render(
      <ThemeProvider>
        <AiVoicesPage />
      </ThemeProvider>
    );

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

    render(
      <ThemeProvider>
        <AiVoicesPage />
      </ThemeProvider>
    );

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

    render(
      <ThemeProvider>
        <AiVoicesPage />
      </ThemeProvider>
    );

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
