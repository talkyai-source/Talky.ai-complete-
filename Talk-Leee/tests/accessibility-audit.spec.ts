import { test, expect } from "@playwright/test";

test.describe("Accessibility Audit", () => {
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
  ];

  const pages = [
    { path: "/", name: "Home" },
    { path: "/ai-voices", name: "AI Voices" },
    { path: "/auth/login", name: "Login" },
    { path: "/auth/register", name: "Register" },
    { path: "/auth/callback", name: "Auth Callback" },
    { path: "/connectors/callback?ok=1&type=email", name: "Connectors Callback" },
    { path: "/connectors/email/callback?ok=1", name: "Connectors Typed Callback" },
    { path: "/dashboard", name: "Dashboard", auth: true },
    { path: "/campaigns", name: "Campaigns", auth: true },
    { path: "/campaigns/new", name: "Campaigns New", auth: true },
    { path: "/campaigns/camp-001", name: "Campaign Details", auth: true },
    { path: "/calls", name: "Calls", auth: true },
    { path: "/calls/call-001", name: "Call Details", auth: true },
    { path: "/contacts", name: "Contacts", auth: true },
    { path: "/analytics", name: "Analytics", auth: true },
    { path: "/recordings", name: "Recordings", auth: true },
    { path: "/ai-options", name: "AI Options", auth: true },
    { path: "/assistant", name: "Assistant", auth: true },
    { path: "/assistant/actions", name: "Assistant Actions", auth: true },
    { path: "/assistant/meetings", name: "Assistant Meetings", auth: true },
    { path: "/assistant/reminders", name: "Assistant Reminders", auth: true },
    { path: "/email", name: "Email", auth: true },
    { path: "/meetings", name: "Meetings", auth: true },
    { path: "/reminders", name: "Reminders", auth: true },
    { path: "/settings", name: "Settings", auth: true },
    { path: "/settings/connectors", name: "Settings Connectors", auth: true },
    { path: "/notifications", name: "Notifications", auth: true },
  ];

  for (const vp of viewports) {
    for (const { path, name, auth } of pages) {
      test(`${name} ${vp.name}: Accessibility Checks`, async ({ page }) => {
        if (auth) {
          await page.addInitScript(() => {
            localStorage.setItem("talklee.auth.token", "dev-token");
          });
        }

        await page.setViewportSize({ width: vp.width, height: vp.height });
        await page.goto(path);
        await page.waitForLoadState("domcontentloaded");
        await page.evaluate(() => document.fonts?.ready);

        const violations = await page.evaluate(() => {
          const issues: string[] = [];
          const MAX_FINDINGS = 80;

          const isVisible = (el: Element) => {
            const style = window.getComputedStyle(el);
            if (style.visibility === "hidden" || style.display === "none") return false;
            const rect = (el as HTMLElement).getBoundingClientRect();
            if (rect.width < 2 || rect.height < 2) return false;
            if (rect.bottom <= 0 || rect.right <= 0) return false;
            if (rect.top >= window.innerHeight || rect.left >= window.innerWidth) return false;
            return true;
          };

          document.querySelectorAll("img").forEach((img) => {
            if (issues.length >= MAX_FINDINGS) return;
            if ((img as HTMLElement).offsetParent === null) return;
            if (!img.hasAttribute("alt")) {
              issues.push(`Image missing alt attribute: ${img.src.substring(0, 50)}...`);
            }
          });

          document.querySelectorAll("button").forEach((btn) => {
            if (issues.length >= MAX_FINDINGS) return;
            if ((btn as HTMLElement).offsetParent === null) return;
            const name = (btn as HTMLElement).innerText || btn.getAttribute("aria-label") || btn.getAttribute("title");
            if (!name || name.trim() === "") {
              const srOnly = btn.querySelector(".sr-only");
              if (srOnly && srOnly.textContent?.trim()) return;
              issues.push(`Button missing accessible name: ${btn.outerHTML.substring(0, 100)}...`);
            }
          });

          document.querySelectorAll('input:not([type="hidden"])').forEach((input) => {
            if (issues.length >= MAX_FINDINGS) return;
            if ((input as HTMLElement).offsetParent === null) return;
            const id = input.id;
            let hasLabel = false;
            if (id && document.querySelector(`label[for="${id}"]`)) hasLabel = true;
            if (input.closest("label")) hasLabel = true;
            if (input.hasAttribute("aria-label") || input.hasAttribute("aria-labelledby")) hasLabel = true;
            if (!hasLabel) {
              issues.push(`Input missing label: ${input.getAttribute("name") || input.id || input.outerHTML.substring(0, 50)}...`);
            }
          });

          const parseColor = (value: string) => {
            const m = value.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([0-9.]+))?\)/);
            if (!m) return null;
            return { r: Number(m[1]), g: Number(m[2]), b: Number(m[3]), a: m[4] ? Number(m[4]) : 1 };
          };
          const luminance = (c: { r: number; g: number; b: number }) => {
            const toLin = (v: number) => {
              const s = v / 255;
              return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
            };
            return 0.2126 * toLin(c.r) + 0.7152 * toLin(c.g) + 0.0722 * toLin(c.b);
          };
          const contrast = (fg: { r: number; g: number; b: number }, bg: { r: number; g: number; b: number }) => {
            const l1 = luminance(fg) + 0.05;
            const l2 = luminance(bg) + 0.05;
            return l1 > l2 ? l1 / l2 : l2 / l1;
          };
          const getEffectiveBackground = (el: Element) => {
            let node: Element | null = el;
            while (node) {
              const style = window.getComputedStyle(node);
              const color = parseColor(style.backgroundColor);
              if (color && color.a > 0.02) return color;
              node = node.parentElement;
            }
            const bodyStyle = window.getComputedStyle(document.body);
            const bodyColor = parseColor(bodyStyle.backgroundColor);
            return bodyColor ?? { r: 255, g: 255, b: 255, a: 1 };
          };

          const textNodes = Array.from(document.querySelectorAll<HTMLElement>("h1,h2,h3,h4,h5,h6,p,span,label,button,a")).filter(
            (el) => isVisible(el) && (el.textContent ?? "").trim().length > 0,
          );
          for (const el of textNodes) {
            if (issues.length >= MAX_FINDINGS) break;
            const style = window.getComputedStyle(el);
            const fgRaw = parseColor(style.color);
            if (!fgRaw) continue;
            const bgRaw = getEffectiveBackground(el);
            const ratio = contrast({ r: fgRaw.r, g: fgRaw.g, b: fgRaw.b }, { r: bgRaw.r, g: bgRaw.g, b: bgRaw.b });
            const fontSize = parseFloat(style.fontSize || "16");
            const fontWeight = parseInt(style.fontWeight || "400", 10);
            const isLarge = fontSize >= 24 || (fontSize >= 18.66 && fontWeight >= 600);
            const minRatio = isLarge ? 3 : 4.5;
            if (ratio < minRatio) {
              issues.push(`Low contrast (${ratio.toFixed(2)}) for text: ${(el.textContent ?? "").trim().slice(0, 80)}`);
            }
          }

          return issues;
        });

        expect(violations, `Accessibility violations on ${path}`).toEqual([]);

        const h1Count = await page.locator("h1").count();
        expect(h1Count).toBeGreaterThan(0);

        const focusableCount = await page.evaluate(() => {
          const selector =
            'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
          const all = Array.from(document.querySelectorAll<HTMLElement>(selector));
          return all.filter((el) => {
            const style = window.getComputedStyle(el);
            if (style.visibility === "hidden" || style.display === "none") return false;
            const rect = el.getBoundingClientRect();
            if (rect.width < 2 || rect.height < 2) return false;
            if (rect.bottom <= 0 || rect.right <= 0) return false;
            if (rect.top >= window.innerHeight || rect.left >= window.innerWidth) return false;
            return true;
          }).length;
        });
        expect(focusableCount).toBeGreaterThan(0);

        const focusResults: Array<{ outlineStyle: string; outlineWidth: string; boxShadow: string }> = [];
        for (let i = 0; i < 8; i += 1) {
          await page.keyboard.press("Tab");
          const res = await page.evaluate(() => {
            const el = document.activeElement as HTMLElement | null;
            if (!el) return null;
            const style = window.getComputedStyle(el);
            return { outlineStyle: style.outlineStyle, outlineWidth: style.outlineWidth, boxShadow: style.boxShadow };
          });
          if (res) focusResults.push(res);
        }
        const focusVisible = focusResults.some((r) => (r.outlineStyle !== "none" && r.outlineWidth !== "0px") || r.boxShadow !== "none");
        expect(focusVisible).toBe(true);
      });
    }
  }
});
