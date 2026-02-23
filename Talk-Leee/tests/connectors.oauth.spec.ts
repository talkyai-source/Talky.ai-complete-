import { expect, test } from "@playwright/test";

test.setTimeout(120_000);

test("connectors oauth flow updates status and shows feedback", async ({ page }) => {
    await page.context().addCookies([{ name: "talklee_auth_token", value: "e2e-token", url: "http://127.0.0.1:3100" }]);

    const statuses: Record<"calendar" | "email" | "crm" | "drive", "disconnected" | "connected"> = {
        calendar: "disconnected",
        email: "disconnected",
        crm: "disconnected",
        drive: "disconnected",
    };

    await page.route(/\/(?:api\/v1\/)?connectors\/status\/?(\?.*)?$/, async (route) => {
        if (route.request().method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                items: [
                    { type: "calendar", status: statuses.calendar },
                    { type: "email", status: statuses.email },
                    { type: "crm", status: statuses.crm },
                    { type: "drive", status: statuses.drive },
                ],
            }),
        });
    });

    await page.route(/\/(?:api\/v1\/)?connectors\/(calendar|email|crm|drive)\/authorize/i, async (route) => {
        if (route.request().method() !== "GET") return route.continue();
        const m = route.request().url().match(/\/(?:api\/v1\/)?connectors\/(calendar|email|crm|drive)\/authorize/i);
        const type = (m?.[1] ?? "calendar") as "calendar" | "email" | "crm" | "drive";
        statuses[type] = "connected";
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                authorization_url: `http://127.0.0.1:3100/connectors/${type}/callback?status=success`,
            }),
        });
    });

    await page.route(/\/(?:api\/v1\/)?connectors\/(calendar|email|crm|drive)\/disconnect/i, async (route) => {
        if (route.request().method() !== "POST") return route.continue();
        const m = route.request().url().match(/\/(?:api\/v1\/)?connectors\/(calendar|email|crm|drive)\/disconnect/i);
        const type = (m?.[1] ?? "calendar") as "calendar" | "email" | "crm" | "drive";
        statuses[type] = "disconnected";
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
    });

    await page.goto("/settings/connectors", { waitUntil: "domcontentloaded", timeout: 60_000 });
    await expect(page.getByRole("heading", { name: "Connectors" })).toBeVisible({ timeout: 20_000 });

    await expect(page.getByText("Google Calendar")).toBeVisible();
    await expect(page.getByTestId("connector-card-calendar")).toBeVisible();

    for (const type of ["calendar", "email", "crm", "drive"] as const) {
        const popupPromise = page.waitForEvent("popup");
        await page.getByTestId(`connector-${type}-connect`).click();
        const popup = await popupPromise;
        await popup.waitForLoadState("domcontentloaded");
        await expect(page.getByRole("status", { name: "Success notification" }).first()).toBeVisible({ timeout: 20_000 });
        await expect(page.getByTestId(`connector-${type}-disconnect`)).toBeVisible({ timeout: 20_000 });
    }

    for (const type of ["calendar", "email", "crm", "drive"] as const) {
        await page.getByTestId(`connector-${type}-disconnect`).click();
        const dialog = page.getByRole("dialog");
        await dialog.getByRole("button", { name: "Disconnect" }).click();
        await expect(page.getByRole("status", { name: "Success notification" }).first()).toBeVisible({ timeout: 20_000 });
        await expect(page.getByTestId(`connector-${type}-connect`)).toBeVisible({ timeout: 20_000 });
    }
});
