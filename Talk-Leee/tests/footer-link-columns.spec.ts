import { test, expect } from "@playwright/test";

// The footer's two link columns share one row rhythm: row N of the navigation
// list and row N of the secondary links must sit on the same horizontal line,
// at every width where the two columns are side by side (which is every width).
// The unpaired fifth and sixth navigation rows sit below the last secondary row.
const WIDTHS = [1280, 768, 375];

for (const width of WIDTHS) {
  test(`footer link columns align row for row at ${width}px`, async ({ page }) => {
    // No backend in this run: the footer only needs the anonymous shell.
    await page.route(/\/api\//, (route) =>
      route.fulfill({ status: 401, contentType: "application/json", body: "{}" }),
    );
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/contact", { waitUntil: "domcontentloaded" });
    await page.locator("footer a[href='/auth/login']").waitFor();
    await page.evaluate(() => document.fonts.ready);

    const rows = await page.evaluate(() => {
      const centre = (el: Element) => {
        const r = el.getBoundingClientRect();
        return r.top + r.height / 2;
      };
      const nav = Array.from(document.querySelectorAll("footer nav li a")).map(centre);
      const terms = document.querySelector("footer a[href='/terms']");
      const column = terms?.parentElement;
      if (!column) throw new Error("secondary link column not found");
      const secondary = Array.from(column.children).map(centre);
      return { nav, secondary };
    });

    expect(rows.nav).toHaveLength(6);
    expect(rows.secondary).toHaveLength(4);
    for (let i = 0; i < rows.secondary.length; i++) {
      expect(Math.abs(rows.nav[i] - rows.secondary[i]), `row ${i + 1} centre difference`).toBeLessThan(0.01);
    }
    expect(rows.nav[4]).toBeGreaterThan(rows.secondary[3]);
    expect(rows.nav[5]).toBeGreaterThan(rows.nav[4]);
  });
}
