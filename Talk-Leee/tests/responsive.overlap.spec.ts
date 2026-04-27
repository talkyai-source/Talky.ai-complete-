import { expect, test } from "@playwright/test";

test.setTimeout(600_000);

const viewports = [
    { name: "desktop-1920x1080", width: 1920, height: 1080 },
    { name: "desktop-1536x900", width: 1536, height: 900 },
    { name: "desktop-1440x900", width: 1440, height: 900 },
    { name: "laptop-1366x768", width: 1366, height: 768 },
    { name: "tablet-1024x768", width: 1024, height: 768 },
    { name: "tablet-768x1024", width: 768, height: 1024 },
    { name: "ipad-pro-1024x1366", width: 1024, height: 1366 },
    { name: "mobile-414x896", width: 414, height: 896 },
    { name: "mobile-390x844", width: 390, height: 844 },
    { name: "mobile-375x812", width: 375, height: 812 },
    { name: "mobile-375x667", width: 375, height: 667 },
    { name: "mobile-320x568", width: 320, height: 568 },
] as const;

const publicRoutes = [
    "/",
    "/ai-voices",
    "/auth/login",
    "/auth/register",
    "/auth/callback",
    "/connectors/callback?ok=1&type=email",
    "/connectors/email/callback?ok=1",
] as const;
const appRoutes = [
    "/dashboard",
    "/campaigns",
    "/campaigns/new",
    "/campaigns/camp-001",
    "/calls",
    "/calls/call-001",
    "/contacts",
    "/analytics",
    "/recordings",
    "/ai-options",
    "/assistant",
    "/assistant/actions",
    "/assistant/meetings",
    "/assistant/reminders",
    "/email",
    "/meetings",
    "/reminders",
    "/settings",
    "/settings/connectors",
    "/notifications",
] as const;

async function gotoWithRetry(page: Parameters<typeof test>[1]["page"], route: string, timeout: number) {
    let lastError: unknown = null;
    for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
            await page.goto(route, { waitUntil: "domcontentloaded", timeout });
            return;
        } catch (err) {
            lastError = err;
            await page.waitForTimeout(1500);
        }
    }
    throw lastError;
}

async function waitForFonts(page: Parameters<typeof test>[1]["page"]) {
    try {
        await page.evaluate(() => document.fonts?.ready, { timeout: 5000 });
    } catch {
        return;
    }
}

async function stubAppRoutes(page: Parameters<typeof test>[1]["page"]) {
    await page.addInitScript(() => {
        localStorage.setItem("talklee.auth.token", "e2e-token");
    });
    await page.context().addCookies([{ name: "talklee_auth_token", value: "e2e-token", url: "http://127.0.0.1:3100" }]);

    await page.route(/\/api\/v1\/health\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) });
    });

    await page.route(/\/(?:api\/v1\/)?connectors\/status\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ items: [{ type: "calendar", status: "connected" }, { type: "email", status: "connected" }] }),
        });
    });

    await page.route(/\/(?:api\/v1\/)?calendar\/events\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
    });

    await page.route(/\/(?:api\/v1\/)?meetings\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
    });

    await page.route(/\/(?:api\/v1\/)?reminders\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
    });

    await page.route(/\/(?:api\/v1\/)?email\/templates\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
    });

    await page.route(/\/(?:api\/v1\/)?assistant\/actions\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
    });

    await page.route(/\/(?:api\/v1\/)?assistant\/runs\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 50 }),
        });
    });

    await page.route(/\/ai\/options\/providers\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                llm: { providers: ["groq"], models: [] },
                stt: { providers: ["deepgram"], models: [] },
                tts: { providers: ["cartesia"], models: [] },
            }),
        });
    });

    await page.route(/\/ai\/providers\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                llm: { providers: ["groq"], models: [] },
                stt: { providers: ["deepgram"], models: [] },
                tts: { providers: ["cartesia"], models: [] },
            }),
        });
    });

    await page.route(/\/ai\/options\/voices\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify([
                {
                    id: "voice-001",
                    name: "Avery",
                    language: "en",
                    description: "Neutral support voice",
                    provider: "cartesia",
                    accent_color: "#64748B",
                    preview_text: "Hello, this is a preview voice.",
                    tags: ["neutral"],
                },
            ]),
        });
    });

    await page.route(/\/ai\/voices\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify([
                {
                    id: "voice-001",
                    name: "Avery",
                    language: "en",
                    description: "Neutral support voice",
                    provider: "cartesia",
                    accent_color: "#64748B",
                    preview_text: "Hello, this is a preview voice.",
                    tags: ["neutral"],
                },
            ]),
        });
    });

    await page.route(/\/voices\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify([
                {
                    id: "voice-001",
                    name: "Avery",
                    language: "en",
                    description: "Neutral support voice",
                    provider: "cartesia",
                    accent_color: "#64748B",
                    preview_text: "Hello, this is a preview voice.",
                    tags: ["neutral"],
                },
            ]),
        });
    });

    await page.route(/\/ai\/options\/config\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                llm_provider: "groq",
                llm_model: "llama-3.3-70b-versatile",
                llm_temperature: 0.6,
                llm_max_tokens: 150,
                stt_provider: "deepgram",
                stt_model: "nova-3",
                stt_language: "en",
                tts_provider: "cartesia",
                tts_model: "sonic-3",
                tts_voice_id: "voice-001",
                tts_sample_rate: 16000,
            }),
        });
    });

    await page.route(/\/ai\/config\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                llm_provider: "groq",
                llm_model: "llama-3.3-70b-versatile",
                llm_temperature: 0.6,
                llm_max_tokens: 150,
                stt_provider: "deepgram",
                stt_model: "nova-3",
                stt_language: "en",
                tts_provider: "cartesia",
                tts_model: "sonic-3",
                tts_voice_id: "voice-001",
                tts_sample_rate: 16000,
            }),
        });
    });
}

async function auditNoOccludedClicks(page: Parameters<typeof test>[1]["page"]) {
    const result = await page.evaluate(() => {
        const MAX_FINDINGS = 60;

        const isVisible = (el: Element) => {
            const style = window.getComputedStyle(el);
            if (style.visibility === "hidden" || style.display === "none") return false;
            const rect = (el as HTMLElement).getBoundingClientRect();
            if (rect.width < 4 || rect.height < 4) return false;
            if (rect.bottom <= 0 || rect.right <= 0) return false;
            if (rect.top >= window.innerHeight || rect.left >= window.innerWidth) return false;
            return true;
        };

        const getRect = (el: Element) => (el as HTMLElement).getBoundingClientRect();

        const candidates = Array.from(
            document.querySelectorAll<HTMLElement>(
                'a[href]:not([tabindex="-1"]), button:not([disabled]), [role="button"]:not([aria-disabled="true"]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])',
            ),
        )
            .filter((el) => isVisible(el))
            .slice(0, 600);

        const occluded: Array<{
            tag: string;
            role: string | null;
            name: string;
            center: { x: number; y: number };
            rect: { left: number; top: number; width: number; height: number };
            topTag: string;
            topName: string;
        }> = [];
        const clippedText: Array<{ tag: string; text: string; rect: { width: number; height: number } }> = [];

        const horizontalOverflow =
            document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
            document.body.scrollWidth > document.body.clientWidth + 1;

        for (const el of candidates) {
            if (occluded.length >= MAX_FINDINGS) break;

            const rect = getRect(el);
            const x = Math.max(0, Math.min(window.innerWidth - 1, rect.left + rect.width / 2));
            const y = Math.max(0, Math.min(window.innerHeight - 1, rect.top + rect.height / 2));

            const topEl = document.elementFromPoint(x, y);
            if (!topEl) continue;
            if (el === topEl) continue;
            if (el.contains(topEl)) continue;

            const topStyle = window.getComputedStyle(topEl);
            if (topStyle.pointerEvents === "none") continue;

            const name =
                (el.getAttribute("aria-label") || el.getAttribute("title") || el.textContent || "").trim().slice(0, 80) || "(unnamed)";
            const topName =
                (topEl.getAttribute("aria-label") || topEl.getAttribute("title") || topEl.textContent || "").trim().slice(0, 80) ||
                "(unnamed)";

            occluded.push({
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute("role"),
                name,
                center: { x: Math.round(x), y: Math.round(y) },
                rect: { left: Math.round(rect.left), top: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) },
                topTag: topEl.tagName.toLowerCase(),
                topName,
            });
        }

        const textCandidates = Array.from(
            document.querySelectorAll<HTMLElement>("h1,h2,h3,h4,h5,h6,p,span,label,button,a"),
        ).filter((el) => isVisible(el) && (el.textContent ?? "").trim().length > 0);
        for (const el of textCandidates) {
            if (clippedText.length >= MAX_FINDINGS) break;
            const style = window.getComputedStyle(el);
            if (style.overflow === "visible" && style.textOverflow !== "ellipsis") continue;
            const rect = el.getBoundingClientRect();
            if (el.scrollWidth > rect.width + 1 || el.scrollHeight > rect.height + 1) {
                clippedText.push({
                    tag: el.tagName.toLowerCase(),
                    text: (el.textContent ?? "").trim().slice(0, 120),
                    rect: { width: Math.round(rect.width), height: Math.round(rect.height) },
                });
            }
        }

        return { occluded, horizontalOverflow, candidateCount: candidates.length, clippedText };
    });

    expect(result.horizontalOverflow, "Horizontal overflow detected").toBe(false);
    expect(
        result.occluded,
        `Found ${result.occluded.length} occluded interactive elements (scanned ${result.candidateCount})`,
    ).toEqual([]);
    expect(result.clippedText, `Found ${result.clippedText.length} clipped text elements`).toEqual([]);
}

async function setupLayoutShiftObserver(page: Parameters<typeof test>[1]["page"]) {
    await page.addInitScript(() => {
        (window as unknown as { __cls?: number }).__cls = 0;
        const observer = new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
                const shift = entry as PerformanceEntry & { value?: number; hadRecentInput?: boolean };
                if (shift.hadRecentInput) continue;
                (window as unknown as { __cls?: number }).__cls =
                    ((window as unknown as { __cls?: number }).__cls ?? 0) + (shift.value ?? 0);
            }
        });
        observer.observe({ type: "layout-shift", buffered: true });
    });
}

async function auditLayoutShift(page: Parameters<typeof test>[1]["page"]) {
    const cls = await page.evaluate(() => (window as unknown as { __cls?: number }).__cls ?? 0);
    expect(cls, "Unexpected layout shift score").toBeLessThanOrEqual(0.05);
}

for (const vp of viewports) {
    test(`responsive: no overlap/misclick (${vp.name})`, async ({ page }) => {
        await setupLayoutShiftObserver(page);
        await page.setViewportSize({ width: vp.width, height: vp.height });

        for (const route of publicRoutes) {
            await gotoWithRetry(page, route, 120_000);
            await waitForFonts(page);
            await auditNoOccludedClicks(page);
            await auditLayoutShift(page);

            if (vp.width < 768) {
                const menuToggle = page.getByLabel("Open navigation menu");
                if ((await menuToggle.count()) > 0 && (await menuToggle.first().isVisible())) {
                    await menuToggle.first().click();

                    const panel = page.getByRole("menu", { name: "Mobile" });
                    await expect(panel).toBeVisible();

                    const box = await panel.boundingBox();
                    expect(box, "Mobile menu panel should have a bounding box").not.toBeNull();
                    if (box) {
                        expect(box.x, "Mobile menu panel should not overflow left").toBeGreaterThanOrEqual(0);
                        expect(box.x + box.width, "Mobile menu panel should not overflow right").toBeLessThanOrEqual(vp.width + 0.5);
                    }

                    await page.getByLabel("Close navigation menu").click();
                    await expect(panel).toBeHidden();
                }
            }
        }

        await stubAppRoutes(page);
        for (const route of appRoutes) {
            await gotoWithRetry(page, route, 180_000);
            await waitForFonts(page);
            await auditNoOccludedClicks(page);
        }
    });
}

