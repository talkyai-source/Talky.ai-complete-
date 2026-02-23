import { expect, test } from "@playwright/test";

test.setTimeout(120_000);

async function stabilizePage(page: import("@playwright/test").Page) {
    await page.addInitScript(() => {
        const fixedNow = 1700000000000;
        Date.now = () => fixedNow;
        const rand = (() => {
            let s = 123456789 >>> 0;
            return () => {
                s = (s * 1664525 + 1013904223) >>> 0;
                return s / 0xffffffff;
            };
        })();
        Math.random = rand;
    });
}

const viewports = [
    { name: "desktop-1920x1080", width: 1920, height: 1080 },
    { name: "desktop-1440x900", width: 1440, height: 900 },
    { name: "laptop-1366x768", width: 1366, height: 768 },
    { name: "tablet-1024x768", width: 1024, height: 768 },
    { name: "tablet-768x1024", width: 768, height: 1024 },
    { name: "mobile-414x896", width: 414, height: 896 },
    { name: "mobile-390x844", width: 390, height: 844 },
    { name: "mobile-375x812", width: 375, height: 812 },
    { name: "mobile-320x568", width: 320, height: 568 },
] as const;

for (const vp of viewports) {
    test(`home SecondaryHero video is seamless and pixel-stable (${vp.name})`, async ({ page }) => {
        const pageErrors: string[] = [];
        page.on("pageerror", (err) => pageErrors.push(err.message));

        await stabilizePage(page);
        await page.emulateMedia({ reducedMotion: "reduce" });
        await page.setViewportSize({ width: vp.width, height: vp.height });
        await page.goto("/", { waitUntil: "domcontentloaded", timeout: 60_000 });
        await page.evaluate(() => document.fonts?.ready);

        await page.addStyleTag({
            content: `
                *, *::before, *::after {
                    transition-duration: 0s !important;
                    transition-delay: 0s !important;
                    animation-duration: 0s !important;
                    animation-delay: 0s !important;
                    caret-color: transparent !important;
                }
                .secondaryHeroPlayer video.secondaryHeroVideoLayer {
                    opacity: 0 !important;
                }
                .secondaryHeroPlayer video.secondaryHeroVideoLayer:nth-of-type(1) {
                    opacity: 1 !important;
                }
                @media (hover: hover) and (pointer: fine) {
                    .secondaryHeroImageWrap:hover {
                        transform: none !important;
                        box-shadow: none !important;
                    }
                    .secondaryHeroContent:hover {
                        transform: none !important;
                    }
                }
            `,
        });

        const section = page.locator("section.secondaryHeroSection");
        await expect(section).toBeVisible();
        await section.scrollIntoViewIfNeeded();

        const heading = section.getByRole("heading", { name: /AI Voice Calling/i });
        await expect(heading).toBeVisible();

        const player = section.locator(".secondaryHeroPlayer");
        const videos = player.locator("video.secondaryHeroVideo");
        await expect(player).toBeVisible();
        await expect(videos).toHaveCount(2);
        await expect(videos.first()).toBeVisible();

        await page.evaluate(async () => {
            const els = Array.from(document.querySelectorAll("video.secondaryHeroVideo")) as HTMLVideoElement[];
            if (els.length !== 2) throw new Error(`Expected 2 videos, found ${els.length}`);
            for (const el of els) el.load();

            await Promise.race([
                Promise.all(
                    els.map(
                        (el) =>
                            new Promise<void>((resolve) => {
                                if (el.readyState >= 2) return resolve();
                                const onLoaded = () => {
                                    el.removeEventListener("loadeddata", onLoaded);
                                    resolve();
                                };
                                el.addEventListener("loadeddata", onLoaded);
                            }),
                    ),
                ).then(() => undefined),
                new Promise<void>((resolve) => setTimeout(resolve, 2000)),
            ]);

            try {
                await els[0].play();
            } catch {
                // Ignore: some environments may still block play() despite muted autoplay
            }
            els[0].pause();

            const start = Date.now();
            while (Date.now() - start < 6000) {
                const rates = els.map((el) => el.playbackRate);
                if (rates.every((r) => Number.isFinite(r) && r >= 0.75 && r <= 0.85)) break;
                await new Promise<void>((resolve) => setTimeout(resolve, 50));
            }
            for (const [idx, el] of els.entries()) {
                const rate = el.playbackRate;
                if (!Number.isFinite(rate) || rate < 0.75 || rate > 0.85) throw new Error(`Unexpected playbackRate for video ${idx}: ${rate}`);
            }

            for (const [idx, el] of els.entries()) {
                const ev = new MouseEvent("contextmenu", { bubbles: true, cancelable: true });
                const dispatchResult = el.dispatchEvent(ev);
                if (dispatchResult !== false && ev.defaultPrevented !== true) {
                    throw new Error(`Video context menu is not prevented for video ${idx}`);
                }
            }

            const activeEl = () => (document.querySelector("video.secondaryHeroVideoLayer.active") as HTMLVideoElement | null);
            const inactiveEl = () => {
                const all = Array.from(document.querySelectorAll("video.secondaryHeroVideoLayer")) as HTMLVideoElement[];
                const active = activeEl();
                return all.find((v) => v !== active) ?? null;
            };

            const active = activeEl();
            const inactive = inactiveEl();
            if (!active || !inactive) throw new Error("Unable to resolve active/inactive videos");

            const duration = active.duration;
            if (Number.isFinite(duration) && duration > 0.5) {
                try {
                    await active.play();
                } catch {}

                try {
                    active.currentTime = Math.max(0, duration - 0.12);
                } catch {}

                const startSwap = Date.now();
                while (Date.now() - startSwap < 1500) {
                    const newActive = activeEl();
                    if (newActive && newActive !== active) break;
                    await new Promise<void>((resolve) => setTimeout(resolve, 50));
                }

                const swappedActive = activeEl();
                if (!swappedActive || swappedActive === active) throw new Error("Active video did not swap near loop boundary");
                if (swappedActive.paused) throw new Error("Swapped active video is paused");

                const oldActive = inactiveEl();
                if (!oldActive) throw new Error("Old active video missing after swap");
                await new Promise<void>((resolve) => setTimeout(resolve, 220));
                if (!oldActive.paused) throw new Error("Old active video should be paused after crossfade");
            }

            for (const el of els) el.pause();
        });

        await page.evaluate(async () => {
            const els = Array.from(document.querySelectorAll("video.secondaryHeroVideo")) as HTMLVideoElement[];
            if (els.length !== 2) return;

            const v1 = els[0];
            const v2 = els[1];
            v1.muted = true;
            v2.muted = true;

            await Promise.race([
                new Promise<void>((resolve) => {
                    if (v1.readyState >= 2) return resolve();
                    const onLoaded = () => {
                        v1.removeEventListener("loadeddata", onLoaded);
                        resolve();
                    };
                    v1.addEventListener("loadeddata", onLoaded);
                }),
                new Promise<void>((resolve) => setTimeout(resolve, 1000)),
            ]);

            const duration = v1.duration;
            const target = Number.isFinite(duration) && duration > 0.3 ? Math.min(0.2, duration - 0.05) : 0.2;
            try {
                v1.currentTime = target;
                await Promise.race([
                    new Promise<void>((resolve) => {
                        const onSeeked = () => {
                            v1.removeEventListener("seeked", onSeeked);
                            resolve();
                        };
                        v1.addEventListener("seeked", onSeeked);
                    }),
                    new Promise<void>((resolve) => setTimeout(resolve, 800)),
                ]);
            } catch {
                // ignore seek errors in environments without proper media decoding
            }

            v1.pause();
            v2.pause();
            await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
        });

        const sectionMetrics = await section.evaluate((el) => {
            const rect = (el as HTMLElement).getBoundingClientRect();
            return { top: rect.top, bottom: rect.bottom, height: rect.height };
        });
        const expectedSectionHeight = vp.height * 0.7;
        expect(Math.abs(sectionMetrics.height - expectedSectionHeight)).toBeLessThanOrEqual(3);
        const layoutMetrics = await page.evaluate(() => {
            const section = document.querySelector("section.secondaryHeroSection") as HTMLElement | null;
            const card = document.querySelector(".secondaryHeroCard") as HTMLElement | null;
            if (!section || !card) return null;
            const s = section.getBoundingClientRect();
            const c = card.getBoundingClientRect();
            return {
                section: { top: s.top, bottom: s.bottom, height: s.height },
                card: { top: c.top, bottom: c.bottom, height: c.height },
            };
        });
        if (!layoutMetrics) throw new Error("Missing SecondaryHero layout elements");
        expect(layoutMetrics.card.top).toBeGreaterThanOrEqual(layoutMetrics.section.top - 1);
        expect(layoutMetrics.card.bottom).toBeLessThanOrEqual(layoutMetrics.section.bottom + 1);

        const overflow = await page.evaluate(() => ({
            docScrollWidth: document.documentElement.scrollWidth,
            docClientWidth: document.documentElement.clientWidth,
        }));
        expect(overflow.docScrollWidth).toBeLessThanOrEqual(overflow.docClientWidth + 1);

        await expect(player).toHaveScreenshot(`home-secondary-hero-player-${vp.name}.png`, { animations: "disabled", mask: [videos.nth(0), videos.nth(1)] });

        expect(pageErrors).toEqual([]);
    });
}
