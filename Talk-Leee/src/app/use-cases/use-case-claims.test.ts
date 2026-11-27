import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { createElement as h, type ComponentType, type ContextType } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import Assist, { metadata as assistMeta } from "@/app/ai-assist/page";
import Agent, { metadata as agentMeta } from "@/app/ai-voice-agent/page";
import Dialer, { metadata as dialerMeta } from "@/app/ai-voice-dialer/page";
import Support, { metadata as supportMeta } from "@/app/use-cases/customer-services-support/page";
import Qualification, { metadata as qualificationMeta } from "@/app/use-cases/automated-lead-qualification/page";
import Products from "@/app/products/page";
import UseCases from "@/app/use-cases/page";
import { ThemeProvider } from "@/components/providers/theme-provider";
import { AuthProvider } from "@/lib/auth-context";
import { productNavItems, useCaseNavItems } from "@/lib/products-use-cases";

const pages = [
    { route: "/ai-assist", Page: Assist, metadata: assistMeta },
    { route: "/ai-voice-agent", Page: Agent, metadata: agentMeta },
    { route: "/ai-voice-dialer", Page: Dialer, metadata: dialerMeta },
    { route: "/use-cases/customer-services-support", Page: Support, metadata: supportMeta },
    { route: "/use-cases/automated-lead-qualification", Page: Qualification, metadata: qualificationMeta },
];
const navFile = "src/lib/products-use-cases.ts";
const files = [...pages.map(({ route }) => `src/app${route}/page.tsx`), navFile];
const read = (file: string) => readFileSync(file, "utf8").replace(/&(?:rsquo|apos|#39);|’/g, "'").replace(/\s+/g, " ");

function sourceCheck(rule: RegExp, paths = files) {
    const matches = paths.flatMap((file) => {
        const hit = read(file).match(rule);
        return hit ? [`${file}: ${hit[0]}`] : [];
    });
    assert.deepEqual(matches, []);
}

test("public copy discloses AI identity without suggesting callers can be misled", () => {
    const paths = ["src/app", "src/components/home", "src/Industries"].flatMap((dir) =>
        readdirSync(dir, { recursive: true, encoding: "utf8" })
            .filter((file) => /\.(?:[jt]sx?|mdx?)$/.test(file) && !/\.(?:test|spec)\./.test(file))
            .map((file) => path.join(dir, file)));
    sourceCheck(/don't reali[sz]e|not speaking (?:with|to) a human|indistinguishable from (?:a |an? actual )?human|sounds? human\b/i, paths);
});

test("product and use-case pages describe voice as the customer channel", () =>
    sourceCheck(/\b(?:chat|sms|live chat|web support|email support|every channel|omnichannel)\b/i));

test("product and use-case pages describe available call actions", () =>
    sourceCheck(/\b(?:refunds?|nurtured automatically|scored and routed automatically|sync automatically|help[ -]?desks?|messaging platforms|automation tools)\b/i));

test("marketing results are free of unmeasured rates and time guarantees", () =>
    sourceCheck(/(?:68|40)%|\b9\s*(?:sec(?:onds?)?|s)\b|(?:<|&lt;)\s*10\s*sec|\b6\s*(?:hrs?|hours?)\b|100%[^}]{0,60}inbound leads|100%["'][^}]{0,60}inbound leads/i));

test("the customer support page omits the graphic promising 68% ticket resolution", () => {
    assert.doesNotMatch(read("src/app/use-cases/customer-services-support/page.tsx"),
        /\/images\/use-cases\/customer-services-support\/1\.png/);
});

test("call pages avoid appointment and payment reminder claims", () =>
    sourceCheck(/\b(?:appointment|payment|service) reminders?\b/i));

test("the product noun for a connected service is connector", () => {
    for (const { route, sentence } of pageStatements()) {
        assert.doesNotMatch(sentence, /\bintegrations?\b/i, route);
    }
});

test("titles describe each page and use the marketing brand", () => {
    for (const { route, metadata } of pages) {
        assert.equal(typeof metadata.title, "string", route);
        assert.match(String(metadata.title), /^[^?|]+ \| Talk-Lee AI$/, route);
    }
});

test("calls offer follow-up without promising a meeting booking", () =>
    sourceCheck(/\b(?:books?|booked|booking|schedules?|scheduled|scheduling)\s+(?:(?:a|an|the|your|client|sales)\s+)*(?:meetings?|appointments?|consultations?|viewings?|interviews?)\b|(?:booked|scheduled) (?:straight |directly )?(?:in)?to (?:your|a rep.s|the client.s) calendar/i));

test("the voice agent page omits the graphic promising an appointment and confirmation", () => {
    assert.doesNotMatch(read("src/app/ai-voice-agent/page.tsx"), /\/images\/ai-voice-agent\/see-it-in-action\.png/);
});

test("public prices are published without custom quotes", () =>
    sourceCheck(/custom quote|pricing depends on|pricing reflects your usage|tailored quote/i));

test("AI Assist describes the tool in the dashboard", () => {
    const source = read("src/app/ai-assist/page.tsx");
    const nav = productNavItems.find((item) => item.href === "/ai-assist");
    assert.ok(nav);
    for (const copy of [source, nav.description]) {
        assert.match(copy, /dashboard/i);
        assert.doesNotMatch(copy, /CRM updates|real.time guidance|call insights|automated follow.ups|follows up automatically/i);
    }
    assert.doesNotMatch(read(navFile), /seamless handoffs?/i);
});

// Render as a signed-out visitor. Server rendering keeps auth and currency
// effects from making requests, while real Link and Button components emit the DOM.
const renderedPages = new Map<string, HTMLElement>();
function renderPage(route: string, Page: ComponentType) {
    const cached = renderedPages.get(route);
    if (cached) return cached;
    const router = {
        bfcacheId: "public-page",
        push() {}, replace() {}, refresh() {}, back() {}, forward() {}, prefetch() {},
    } satisfies NonNullable<ContextType<typeof AppRouterContext>>;
    const html = renderToStaticMarkup(h(AppRouterContext.Provider, { value: router },
        h(PathnameContext.Provider, { value: route }, h(AuthProvider, { children:
            h(ThemeProvider, { children: h(Page) }) }))));
    const container = document.createElement("div");
    container.innerHTML = html;
    for (const shared of container.querySelectorAll("nav,footer")) shared.remove();
    renderedPages.set(route, container);
    return container;
}

function plain(element: Element) {
    return (element.textContent ?? "").replace(/\s+/g, " ").trim();
}

function pageStatements() {
    return [
        ...pages.flatMap(({ route, Page, metadata }) => {
            const blocks = [...renderPage(route, Page).querySelectorAll("p,li,h1,h2,h3,h4,span")];
            return [...blocks.filter((block) => !block.querySelector("p,li,h1,h2,h3,h4")).map(plain),
                String(metadata.title), metadata.description ?? ""].flatMap((text) =>
                text.split(/(?<=[.!?])\s+/).map((sentence) => ({ route, sentence })));
        }),
        ...[...productNavItems, ...useCaseNavItems].map(({ href, description }) => ({ route: href, sentence: description })),
    ];
}

test("claims about CRM and calendar activity name the connector or its connection requirement", () => {
    for (const { route, sentence } of pageStatements()) {
        if (sentence.endsWith("?") || !/\b(?:sync\w*|book\w*|schedul\w*|send\w*|connect\w*|log\w*|import\w*|updat\w*|sav\w*|read\w*)\b/i.test(sentence)) continue;
        if (!/\b(?:CRM|calendar|booking|book(?:s|ed)? (?:a |an )?(?:appointment|meeting))\b/i.test(sentence)) continue;
        assert.match(sentence, /HubSpot|Salesforce|Google Calendar|(?:when|once|after)\b.*\bconnect/i, `${route}: ${sentence}`);
    }
});

test("claims about transfers name the campaign setup", () => {
    for (const { route, sentence } of pageStatements()) {
        if (sentence.endsWith("?") || /^(?:human )?(?:transfers?|handoffs?)$/i.test(sentence)) continue;
        if (!/\b(?:transfers?|hand[ -]?offs?|hand(?:s|ing)? off|escalates?|routes? to (?:the )?right team)\b/i.test(sentence)) continue;
        assert.match(sentence, /(?:set up|configured|approved|enabled|switched on)/i, `${route}: ${sentence}`);
        assert.doesNotMatch(sentence, /\bevery call|\balways\b|\binstantly\b/i, route);
    }
});

test("product pricing statements lead to the public pricing page", () => {
    for (const { route, Page } of pages) {
        for (const p of renderPage(route, Page).querySelectorAll("p,li")) {
            if (!/\b(?:our pricing|public pricing|subscription plans?|pay as you go|fixed prices|view pricing|pricing page|pricing depends|pricing reflects)\b/i.test(plain(p))) continue;
            assert.ok(p.querySelector('a[href="/pricing"],a[href="/pricing/"]'), `${route}: ${plain(p)}`);
        }
    }
});

test("rendered product and use-case links deliver what their labels offer", () => {
    for (const { route, Page } of pages) {
        const content = renderPage(route, Page);
        const links = [...content.querySelectorAll("a")];
        assert.ok(links.length, route);
        for (const link of links) {
            const label = plain(link).replace(/[→›]+$/, "").trim();
            const href = link.getAttribute("href")?.replace(/\/$/, "");
            assert.ok(label && href, `${route}: unnamed link or missing target`);
            if (href.startsWith("/")) {
                const pathname = new URL(href, "https://site.invalid").pathname;
                assert.ok(existsSync(path.join("src/app", pathname, "page.tsx")), `${route}: ${label} -> ${href}`);
            }
            if (/talk to sales|contact sales/i.test(label)) assert.equal(href, "/contact", label);
            if (/demo|see (?:how|it|AI)|see a live|tour/i.test(label)) {
                assert.equal(href, "/book-a-demo", label);
                assert.equal(label, "Book a Demo", route);
            }
            if (/pric(?:e|ing)|plans?/i.test(label)) assert.equal(href, "/pricing", label);
            if (/documentation|explore every feature/i.test(label)) assert.equal(href, "/docs", label);
            if (/get started|sign up/i.test(label)) assert.equal(href, "/auth/register", label);
            assert.doesNotMatch(label, /custom quote|tailored quote/i);
        }
        assert.equal(content.querySelectorAll("button:not(a button)").length, 0, `${route}: unlinked action`);
    }
    for (const [route, Hub, entries] of [["/products", Products, productNavItems], ["/use-cases", UseCases, useCaseNavItems]] as const) {
        const content = renderPage(route, Hub);
        for (const item of entries) {
            const link = [...content.querySelectorAll("a")].find((anchor) => anchor.querySelector("h2")?.textContent === item.label);
            assert.ok(link, item.label);
            assert.equal(link.getAttribute("href")?.replace(/\/$/, ""), item.href);
            assert.ok(pages.some((page) => page.route === item.href), item.href);
        }
    }
});
