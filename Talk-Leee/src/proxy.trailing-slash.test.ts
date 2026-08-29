import assert from "node:assert/strict";
import { test } from "node:test";
import { trailingSlashRedirectPath } from "@/proxy";

/**
 * Every page route in src/app. These must all redirect to the trailing-slashed
 * form. Dynamic segments are spelled with a concrete value, which is what the
 * proxy actually sees at runtime.
 */
const PAGE_ROUTES = [
    "/403",
    "/admin",
    "/admin/abuse-detection",
    "/admin/api-keys",
    "/admin/audit-logs",
    "/admin/billing",
    "/admin/billing/tenants",
    "/admin/rate-limiting",
    "/admin/reviews",
    "/admin/secrets",
    "/admin/voice-security",
    "/admin/webhooks",
    "/ai-assist",
    "/ai-options",
    "/ai-voice-agent",
    "/ai-voice-dialer",
    "/ai-voices",
    "/analytics",
    "/assistant",
    "/assistant/actions",
    "/assistant/meetings",
    "/assistant/reminders",
    "/auth/callback",
    "/auth/forgot-password",
    "/auth/login",
    "/auth/register",
    "/billing",
    "/billing/invoices",
    "/billing/invoices/inv_123",
    "/billing/plans",
    "/calls",
    "/calls/call_123",
    "/campaigns",
    "/campaigns/camp_123",
    "/campaigns/camp_123/edit",
    "/campaigns/new",
    "/connectors",
    "/connectors/callback",
    "/connectors/google/callback",
    "/contact",
    "/contacts",
    "/dashboard",
    "/email",
    "/industries/education",
    "/industries/financial-services",
    "/industries/healthcare",
    "/industries/marketing-automation",
    "/industries/professional-services",
    "/industries/real-estate",
    "/industries/recruitment",
    "/industries/retail-ecommerce",
    "/industries/software-tech-support",
    "/industries/travel-industry",
    "/meetings",
    "/notifications",
    "/privacy",
    "/recordings",
    "/reminders",
    "/reviews",
    "/security",
    "/settings",
    "/terms",
    "/use-cases/automated-lead-qualification",
    "/use-cases/customer-services-support",
    "/white-label/dashboard",
    "/white-label/acme/analytics",
    "/white-label/acme/billing",
    "/white-label/acme/dashboard",
    "/white-label/acme/preview",
    "/white-label/acme/tenants",
    "/white-label/acme/tenants/t_1/agent-settings",
];

test("every page route redirects to its trailing-slashed form", () => {
    for (const route of PAGE_ROUTES) {
        assert.equal(trailingSlashRedirectPath(route), route + "/", `expected ${route} -> ${route}/`);
    }
});

test("page route count matches the app router page count", () => {
    // 72 pages: 71 listed above plus "/" itself, which is already canonical.
    assert.equal(PAGE_ROUTES.length + 1, 72);
});

test("already-canonical paths are not redirected again", () => {
    assert.equal(trailingSlashRedirectPath("/"), null);
    for (const route of PAGE_ROUTES) {
        assert.equal(trailingSlashRedirectPath(route + "/"), null, `${route}/ should be left alone`);
    }
});

test("api routes are never redirected", () => {
    // The Stripe webhook is the reason skipTrailingSlashRedirect is enabled:
    // Stripe treats a 3xx as a failed delivery and does not follow it.
    assert.equal(trailingSlashRedirectPath("/api/v1/billing/webhooks/stripe"), null);
    assert.equal(trailingSlashRedirectPath("/api/v1/billing/webhooks/stripe/"), null);
    assert.equal(trailingSlashRedirectPath("/api/voices"), null);
    assert.equal(trailingSlashRedirectPath("/api/white-label/branding/acme"), null);
    assert.equal(trailingSlashRedirectPath("/api/v1/auth/login"), null);
    assert.equal(trailingSlashRedirectPath("/api"), null);
});

test("build assets and static files are never redirected", () => {
    assert.equal(trailingSlashRedirectPath("/_next/static/chunks/main.js"), null);
    assert.equal(trailingSlashRedirectPath("/favicon.ico"), null);
    assert.equal(trailingSlashRedirectPath("/favicon-192.png"), null);
    assert.equal(trailingSlashRedirectPath("/openapi.json"), null);
    assert.equal(trailingSlashRedirectPath("/site.webmanifest"), null);
    assert.equal(trailingSlashRedirectPath("/images/hero-navbar-video.mp4"), null);
    assert.equal(trailingSlashRedirectPath("/robots.txt"), null);
});

test("a dot in a non-final segment does not exempt the path", () => {
    assert.equal(trailingSlashRedirectPath("/white-label/acme.co/dashboard"), "/white-label/acme.co/dashboard/");
});

test("non-absolute input is ignored", () => {
    assert.equal(trailingSlashRedirectPath("ai-voice-dialer"), null);
    assert.equal(trailingSlashRedirectPath(""), null);
});
