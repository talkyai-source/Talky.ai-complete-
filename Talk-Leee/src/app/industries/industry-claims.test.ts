import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

// W4: the ten industry pages and the navigation lines in src/Industries/industries.ts say only
// what the product does. Voice is the only channel; on a call the agent can schedule a callback,
// send a follow-up email, submit a configured form, transfer to a person (only when set up) or
// end the call; prices are fixed and public. A guard over the literal copy, since the defect is
// the copy. One test per rule so a failure names the rule, the file and the phrase.
const root = process.cwd();
const industriesDir = path.join(root, "src", "app", "industries");
const pageFiles = readdirSync(industriesDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => path.join("src", "app", "industries", entry.name, "page.tsx"))
    .sort();
const navFile = path.join("src", "Industries", "industries.ts");
const files = [...pageFiles, navFile];

const normalise = (text: string) => text.replace(/&(?:rsquo|apos|#39);|’/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const read = (file: string) => normalise(readFileSync(path.join(root, file), "utf8"));

/** Sentence-like chunks of a source file: split at sentence ends, quotes, backticks and tags. */
function sentences(text: string): string[] {
    return text
        .split(/(?<=[.!?])\s+|[`"]|<[^>]*>|[{}]/)
        .map((part) => part.trim())
        .filter((part) => part.length > 0);
}

function hits(rule: RegExp, paths = files): string[] {
    return paths.flatMap((file) => {
        const hit = read(file).match(rule);
        return hit ? [`${file}: "${hit[0]}"`] : [];
    });
}
const forbid = (rule: RegExp, paths = files) => assert.deepEqual(hits(rule, paths), []);

function sentenceHits(trigger: RegExp, allowed: RegExp, paths = files): string[] {
    return paths.flatMap((file) =>
        sentences(read(file))
            .filter((sentence) => trigger.test(sentence) && !allowed.test(sentence))
            .map((sentence) => `${file}: "${sentence}"`));
}
const forbidSentences = (trigger: RegExp, allowed: RegExp, paths = files) => assert.deepEqual(sentenceHits(trigger, allowed, paths), []);

test("R1 no white-label or dedicated-workspace claim", () =>
    forbid(/white[- ]label|dedicated workspace/i));

// R2: a system with no connector may be named only to say the product does not connect to it.
const NO_CONNECTOR_SYSTEMS = /\bATS\b|applicant tracking|core banking|help ?desk|ticketing|support tickets?|\bEHR\b|\bEMR\b|\bPMS\b|property management system|booking engine|payment gateway/i;
const SAYS_NOT_CONNECTED = /\b(?:does not|doesn't|do not|don't|cannot|can't|is not|isn't|are not|not available|no connector|not connected|without)\b/i;
test("R2 no system without a connector is named as something the product connects to", () =>
    forbidSentences(NO_CONNECTOR_SYSTEMS, SAYS_NOT_CONNECTED));
test("R2 fixture: a plain negative passes, a promise fails", () => {
    const flagged = (sentence: string) => NO_CONNECTOR_SYSTEMS.test(sentence) && !SAYS_NOT_CONNECTED.test(sentence);
    assert.equal(flagged("A direct connection to ATS or HR software is not available."), false);
    assert.equal(flagged("Talk-Lee does not connect to your core banking system."), false);
    assert.equal(flagged("There is no connector for help desk software."), false);
    assert.equal(flagged("Integrates with popular Applicant Tracking Systems."), true);
    assert.equal(flagged("Trigger support tickets automatically."), true);
});

test("R3 no account-system action on financial-services", () =>
    forbid(/balance inquiries|card activation|lost or stolen card/i, [path.join("src", "app", "industries", "financial-services", "page.tsx")]));

test("R4 no other language, reminder, confirmation, omnichannel, availability or non-voice channel claim", () => {
    assert.deepEqual([
        ...hits(/multilingual|multi-language|any language/i),
        ...hits(/appointment reminders|payment reminders|sends? reminders|confirmation calls|automated confirmations/i),
        ...hits(/omnichannel|real[- ]time availability/i),
        ...hits(/\bsms\b|whatsapp|live chat|chatbot|email campaigns/i),
    ], []);
});

test("R5 no compliance promise", () =>
    forbid(/stay compliant|hipaa[- ]compliant|fully compliant|gdpr[- ]compliant|pci[- ]compliant/i));

test("R6 connector and Call History vocabulary", () => {
    assert.deepEqual([...hits(/\bintegrations?\b/i), ...hits(/call records/i)], []);
});

test("R7 every page title is pageTitle(), unique, and title and description promise nothing false", () => {
    const titles: string[] = [];
    const descriptions: string[] = [];
    const problems: string[] = [];
    for (const file of pageFiles) {
        const source = read(file);
        if (!/import \{[^}]*\bpageTitle\b[^}]*\} from "@\/lib\/site-metadata"/.test(source)) problems.push(`${file}: pageTitle is not imported from @/lib/site-metadata`);
        const block = source.slice(source.indexOf("export const metadata"), source.indexOf("export default"));
        const title = block.match(/title:\s*pageTitle\(\s*"([^"]+)"\s*\)/)?.[1];
        const description = block.match(/description:\s*"([^"]*)"/)?.[1];
        if (!title) { problems.push(`${file}: title is not built with pageTitle("...")`); continue; }
        if (!description || description.length < 40) problems.push(`${file}: description missing or under 40 characters`);
        titles.push(title);
        descriptions.push(description ?? "");
        for (const [field, text] of [["title", title], ["description", description ?? ""]] as const) {
            const hit = text.match(/call us today|enterprise|24\/7|white[- ]label|free trial|at scale|human-like|appointment|\bsms\b|\bchat/i);
            if (hit) problems.push(`${file}: ${field} contains "${hit[0]}"`);
        }
    }
    if (new Set(titles).size !== titles.length) problems.push("titles are not unique");
    if (new Set(descriptions).size !== descriptions.length) problems.push("descriptions are not unique");
    assert.deepEqual(problems, []);
});

test("R8 the calling agent does not book into a calendar, screen resumes or schedule interviews", () =>
    forbid(/books? appointments|appointment booking|appointment scheduling|schedules? interviews|books meetings|booked into your calendar|resume screening|screens resumes/i));

// R9: every sentence about transfer carries the set-up condition. "Never a person will answer".
const TRANSFER = /transfer|hand[- ]?off|handoff|escalat|routes? calls/i;
const SET_UP_CONDITION = /\b(?:set up|set-up|configured|approved|when your campaign|if your campaign|when the campaign|only when|only if|does not|not available|cannot|can't|is not)\b|\?$/i;
test("R9 every transfer sentence names its set-up condition, and the banned phrases are gone", () => {
    assert.deepEqual([...sentenceHits(TRANSFER, SET_UP_CONDITION), ...hits(/hand off with context|route calls intelligently|routing specialized requests|can move to your team|seamless hand-?offs?|a person will answer/i)], []);
});

test("R10 no invented plan, price or quote wording, and honest price and sales links", () => {
    const problems: string[] = [
        ...hits(/see plans|see agency pricing|see recruitment plans|explore hospitality plans|get your custom quote|custom pricing|custom quote|tailored quote|flexible plans/i),
        ...hits(/\b(?:starter|growth|solo|practice|launch|scale|enterprise|professional|firm)\s+(?:plan|tier)\b|\$\s?\d/i),
    ];
    for (const file of pageFiles) {
        for (const match of read(file).matchAll(/<Link[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/Link>/g)) {
            const [, href, inner] = match;
            const label = inner.replace(/<[^>]*>/g, "").trim();
            if (/pric|plan|quote/i.test(label) && !(label === "View Pricing" && href === "/pricing")) problems.push(`${file}: "${label}" -> ${href}`);
            if (href === "/pricing" && label !== "View Pricing") problems.push(`${file}: "${label}" -> ${href}`);
            if (/^(?:contact|talk to) sales$/i.test(label) && href !== "/contact") problems.push(`${file}: "${label}" -> ${href}`);
        }
    }
    assert.deepEqual(problems, []);
});

test("R11 no unsourced percentage, multiplier, response time or customer count", () =>
    forbid(/\d+(?:\.\d+)?\s?%|\b\d+x\b|\b\d+\s*(?:sec|seconds|ms|hrs?|hours)\b|\b\d[\d,]*\+?\s+(?:customers|clients|businesses|companies|teams)\b|<\s*\d+\s*(?:sec|s)\b/i));

// No measured set-up time exists: how soon an account can call depends on its phone line,
// knowledge and campaign.
test("R11 no unsourced promise of how soon an account goes live", () =>
    // "within hours you configure" is about calling hours, not a go-live promise.
    forbid(/\bwithin (?:a few )?(?:days|hours|minutes)\b(?! you\b)|\bin (?:days|hours|minutes)\b|days, not months|our team handles the setup/i));

// Two buttons side by side must not lead to the same page: the visitor is offered one choice twice.
test("R13 no two neighbouring buttons share a target", () => {
    const problems: string[] = [];
    for (const file of pageFiles) {
        const lines = readFileSync(path.join(root, file), "utf8").split(/\r?\n/);
        let previous: { line: number; href: string } | undefined;
        lines.forEach((text, index) => {
            const href = /<Link href="(\/(?:book-a-demo|auth\/register|pricing|contact)[^"]*)"/.exec(text)?.[1];
            if (!href) return;
            if (previous && previous.href === href && index - previous.line <= 6) problems.push(`${file}: lines ${previous.line + 1} and ${index + 1} both go to ${href}`);
            previous = { line: index, href };
        });
    }
    assert.deepEqual(problems, []);
});

test("R12 nothing suggests the caller is misled about talking to an AI", () =>
    forbid(/don't realize|not speaking with a human|indistinguishable from (?:a |an? actual )?human|sounds? human\b/i));
