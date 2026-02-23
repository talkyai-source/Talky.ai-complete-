import { expect, test } from "@playwright/test";

test("reminders page lists groups and supports cancel flow", async ({ page }) => {
    await page.context().addCookies([{ name: "talklee_auth_token", value: "e2e-token", url: "http://127.0.0.1:3100" }]);
    await page.route(/connectors\/status/i, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ items: [{ type: "calendar", status: "connected" }, { type: "email", status: "connected" }] }),
        });
    });
    await page.route(/\/(?:api\/v1\/)?reminders\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                items: [
                    {
                        id: "rem-1",
                        content: "Email reminder",
                        status: "scheduled",
                        channel: "email",
                        scheduled_at: "2026-01-14T10:00:00Z",
                        meeting_id: "mtg-1",
                        meeting_title: "Weekly sync",
                        contact_name: "Alex",
                        to_email: "alex@example.com",
                    },
                    {
                        id: "rem-2",
                        content: "Failed reminder",
                        status: "failed",
                        channel: "email",
                        scheduled_at: "2026-01-14T09:00:00Z",
                        meeting_id: "mtg-1",
                        meeting_title: "Weekly sync",
                        retry_count: 2,
                        max_retries: 5,
                        failure_reason: "<b>SMTP</b> timeout",
                        next_retry_at: "2026-01-14T09:05:00Z",
                    },
                ],
            }),
        });
    });

    await page.route(/\/(?:api\/v1\/)?calendar\/events\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                items: [
                    {
                        id: "mtg-1",
                        title: "Weekly sync",
                        start_time: "2099-01-15T10:00:00Z",
                        lead_name: "Alex",
                        lead_id: "lead-1",
                    },
                ],
            }),
        });
    });

    let cancelCalled = false;
    let canceledId: string | null = null;
    await page.route(/\/(?:api\/v1\/)?reminders\/[^/]+\/cancel\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "POST") return route.continue();
        const url = new URL(route.request().url());
        const parts = url.pathname.split("/").filter(Boolean);
        const id = parts[parts.length - 2] ?? "unknown";
        cancelCalled = true;
        canceledId = id;
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                id,
                content: id === "rem-2" ? "Failed reminder" : "Email reminder",
                status: "canceled",
                channel: "email",
                scheduled_at: "2026-01-14T10:00:00Z",
                canceled_at: "2026-01-14T08:00:00Z",
                meeting_id: "mtg-1",
                meeting_title: "Weekly sync",
            }),
        });
    });

    await page.goto("/reminders", { waitUntil: "domcontentloaded" });

    await expect(page.getByRole("heading", { name: "Reminders" })).toBeVisible();
    await expect(page.getByText("Weekly sync")).toBeVisible();
    await expect(page.getByText("Failed reminder")).toBeVisible();
    await expect(page.getByText("Retries: 2/5")).toBeVisible();
    await expect(page.getByText("‹b›SMTP‹/b› timeout")).toBeVisible();

    const cancelRow = page.locator("div.rounded-xl").filter({ has: page.getByText("Email reminder") });
    await cancelRow.getByRole("button", { name: "Cancel", exact: true }).click();
    const cancelDialog = page.getByRole("dialog", { name: "Cancel reminder" });
    await expect(cancelDialog).toBeVisible();
    await cancelDialog.getByRole("button", { name: "Cancel reminder" }).click();
    await expect.poll(() => cancelCalled).toBeTruthy();
    await expect.poll(() => canceledId).toBe("rem-1");
});

test("create reminder modal validates and submits", async ({ page }) => {
    await page.context().addCookies([{ name: "talklee_auth_token", value: "e2e-token", url: "http://127.0.0.1:3100" }]);
    await page.route(/connectors\/status/i, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ items: [{ type: "calendar", status: "connected" }, { type: "email", status: "connected" }] }),
        });
    });
    await page.route(/\/(?:api\/v1\/)?reminders\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() === "GET") {
            return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
        }
        if (req.method() === "POST") {
            const body = req.postDataJSON() as unknown as {
                content: string;
                channel: string;
                scheduled_at: string;
                to_email?: string;
            };
            return route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({
                    id: "rem-new",
                    content: body.content,
                    status: "scheduled",
                    channel: body.channel,
                    scheduled_at: body.scheduled_at,
                    to_email: body.to_email,
                }),
            });
        }
        return route.continue();
    });

    await page.route(/\/(?:api\/v1\/)?calendar\/events\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                items: [
                    {
                        id: "mtg-1",
                        title: "Weekly sync",
                        start_time: "2099-01-15T10:00:00Z",
                    },
                ],
            }),
        });
    });

    await page.goto("/reminders", { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "Create reminder" }).click();

    await page.getByRole("button", { name: /^Schedule$/ }).click();
    await expect(page.getByText("Message is required.")).toBeVisible();

    await page.getByLabel("Message").fill("Reminder: meeting soon");
    await page.getByLabel("Recipient email").fill("not-an-email");
    await page.getByRole("button", { name: /^Schedule$/ }).click();
    await expect(page.getByText("Recipient email is invalid.")).toBeVisible();

    await page.getByLabel("Recipient email").fill("alex@example.com");
    await page.getByLabel("Select meeting").click();
    await page.getByRole("option", { name: "Weekly sync" }).click();
    await page.getByLabel("Schedule option").click();
    await page.getByRole("option", { name: "T-10m" }).click();
    await page.getByRole("button", { name: /^Schedule$/ }).click();

    const confirmDialog = page.getByRole("dialog", { name: "Confirm reminder" });
    await expect(confirmDialog).toBeVisible();
    await confirmDialog.getByRole("button", { name: "Confirm" }).click();
    await expect(page.getByText("No reminders yet.")).not.toBeVisible();
});
