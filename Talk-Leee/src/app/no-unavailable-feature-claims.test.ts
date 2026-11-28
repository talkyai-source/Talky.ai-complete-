import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Release review, 14 Nov 2026: public pages promised things the product does
// not have, and two legal statements contradicted it. Each entry names the
// claim, where it stood, and what the product really does. A guard over the
// literal copy, since the defect is the copy.
const src = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (file: string) => readFileSync(path.join(src, file), "utf8");

const claims: { file: string; claim: RegExp; why: string }[] = [
    { file: "components/home/features-section.tsx", claim: /Resell Talk-Lee AI under your own brand with dedicated access/i,
      why: "white-label is not available in this release (docs/_content/partners.md)" },
    { file: "components/home/home-lazy-sections.tsx", claim: /Yes\. Our white.label program lets you/i,
      why: "white-label is not available in this release" },
    { file: "components/home/home-lazy-sections.tsx", claim: /issues dedicated numbers/i,
      why: "nothing issues a number by itself: a customer requests one under Settings, Telephony (Buy Now), staff review the request, and the account can use its own SIP trunk" },
    { file: "components/home/home-lazy-sections.tsx", claim: /lead forms/i,
      why: "there is no lead-form connector" },
    { file: "app/industries/healthcare/page.tsx", claim: /connect with most major scheduling and EHR platforms/i,
      why: "there is no EHR connector; bookings go to Google Calendar" },
    { file: "app/industries/marketing-automation/page.tsx", claim: /GoHighLevel|Zapier/,
      why: "the only CRM connectors are HubSpot and Salesforce" },
    { file: "app/pricing/page.tsx", claim: /Recording retention: 30 days|"Recording retention", "30 days"/,
      why: "retention comes from the account's recording policy (90 days by default), not from the plan" },
    { file: "app/pricing/page.tsx", claim: /CDRs \/ call detail records/,
      why: "customers get call history, not a call-detail-record export" },
    { file: "app/terms/terms-content.ts", claim: /All calls are recorded by default/i,
      why: "recording is off until the account sets a recording policy" },
    { file: "app/privacy/privacy-content.ts", claim: /never email body content|We do not read email body content|Gmail \(send only\)|files created by Talk-Lee only|Files you did not create through Talk-Lee/i,
      why: "the Gmail and Drive connectors request read access and the assistant reads messages and files on request" },
];

for (const { file, claim, why } of claims) {
    test(`${file} does not claim ${claim}`, () => {
        assert.equal(read(file).match(claim), null, why);
    });
}

test("the privacy page says what the Gmail and Drive connections can read", () => {
    const privacy = read("app/privacy/privacy-content.ts");
    assert.match(privacy, /read your messages, including their content, when you ask the assistant/);
    assert.match(privacy, /list and read the files in your Drive, including Google Docs, Sheets and Slides, when you ask the assistant/);
});

// W5 (28 Nov 2026): the home page promised things the product does not have.
// The surface is the page, its components and its structured data. footer.tsx,
// contact-section.tsx and category-overview.tsx belong to other packages and
// are read by their own tests.
const DATA_FILE = "lib/public-connector-status.ts";
const homeSurface = ():{ file: string; text: string }[] => {
    const dir = path.join(src, "components/home");
    const files = [
        "app/page.tsx",
        "lib/structured-data.ts",
        ...readdirSync(dir)
            .filter((f) => f.endsWith(".tsx") && !f.endsWith(".test.tsx"))
            .filter((f) => !["footer.tsx", "contact-section.tsx", "category-overview.tsx"].includes(f))
            .map((f) => `components/home/${f}`),
        DATA_FILE,
    ];
    return files.map((file) => ({ file, text: read(file) }));
};

const home: { name: string; claim: RegExp; why: string }[] = [
    { name: "W5-T5 calendar booking by the voice agent", claim: /book(ing)? appointments?|appointment (booking|scheduling)|live calendars?|Works with Google Calendar/i,
      why: "calls never book into a calendar; only the dashboard assistant does" },
    { name: "W5-T5 knowledge import", claim: /\bPDFs?\b|(?<![/\w])images(?![/\w])|crawl/i,
      why: "the knowledge upload takes .md and .txt files only; nothing crawls websites" },
    { name: "W5-T5 forms as a connector", claim: /HubSpot\/forms|lead forms|connect with [^."]*forms/i,
      why: "there is no forms connector" },
    { name: "W5-T5 dedicated infrastructure", claim: /Your servers|Your GPUs|dedicated infrastructure|dedicated servers|Own Your AI Voice Agent Platform|private cloud/i,
      why: "no such offering exists; the service is hosted, the customer owns no platform" },
    { name: "W5-T5 features the dashboard lacks", claim: /A\/B testing|Automated QA|Conversation Intelligence|drag.and.drop|CCaaS|all major CRM|call center software|real.time customer data/i,
      why: "none of these exists; Agent Quality is admin-only human review" },
    { name: "W5-T6 'integration' as the product's noun", claim: /\bintegrat\w*/i,
      why: "the dashboard says connector(s); 'integrate' and 'integrating' are the same word" },
    { name: "W5-T6 'call records'", claim: /call records/i, why: "the dashboard says Call History" },
    { name: "W5-T6 'Knowledgebase' / 'campaign knowledge'", claim: /Knowledgebase|campaign knowledge/i,
      why: "the dashboard says knowledge base" },
];

for (const { name, claim, why } of home) {
    test(`${name}: absent from the home page and its components`, () => {
        for (const { file, text } of homeSurface()) {
            assert.equal(text.match(claim), null, `${file}: ${why}`);
        }
    });
}

// One line at a time: a template literal holding quotes (the hero's SVG) would
// otherwise shift the pairing for the rest of the file.
const literals = (text: string) =>
    text
        .split("\n")
        .flatMap((line) => [...line.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1]))
        .filter((s) => s.includes(" ") && !s.trim().endsWith("?"));

test("W5-T7 every transfer sentence on the home page names its direction, its condition and that an answer is not guaranteed", () => {
    let seen = 0;
    for (const { file, text } of homeSurface()) {
        for (const s of literals(text).filter((l) => /\btransfer/i.test(l))) {
            seen += 1;
            assert.match(s, /outgoing|outbound/i, `${file}: no direction (outgoing): ${s}`);
            assert.match(s, /incoming|inbound/i, `${file}: no direction (incoming): ${s}`);
            assert.match(s, /set up|set-up|approved|needs|must/i, `${file}: no condition: ${s}`);
            assert.match(s, /not guaranteed|does not guarantee/i, `${file}: may promise that a person answers: ${s}`);
        }
    }
    assert.ok(seen >= 2, "the home page still describes transfer (card and FAQ)");
});

test("W5-T7 callbacks on the home page are outgoing only, and incoming calls note the request", () => {
    for (const { file, text } of homeSurface()) {
        for (const s of literals(text).filter((l) => /\bcallbacks?\b/i.test(l))) {
            assert.match(s, /outgoing|outbound|note|notes/i, `${file}: callback without its direction: ${s}`);
        }
    }
});

// Audit of 28 Nov 2026 (W5): the rules above let false sentences through. These
// rules read SENTENCES over all the visible text of each file (JSX text, strings
// and the data file), with comments removed, and are ported from the industry
// guard (src/app/industries/industry-claims.test.ts: R8, R9, R11, R12, R14, R16).
// Each has a fixture: the false sentence it must flag and an honest one it lets through.
const visibleText = (text: string): string =>
    text
        .replace(/\/\*[\s\S]*?\*\//g, " ")
        .replace(/(?<=^|\s)\/\/[^\n]*/gm, " ")
        .replace(/[‐‑]/g, "-")
        .replace(/&(?:rsquo|apos|#39);|’/g, "'")
        .replace(/&amp;/g, "&")
        .replace(/\s+/g, " ");

/** Sentence-like chunks: split at sentence ends, quotes, backticks, tags and braces. */
const sentencesOf = (text: string): string[] =>
    visibleText(text)
        .split(/(?<=[.!?])\s+|[`"]|<[^>]*>|[{}]/)
        .map((part) => part.trim())
        .filter((part) => part.length > 0);

const flaggedSentences = (trigger: RegExp, allowed: RegExp | null, skip: string[] = []): string[] =>
    homeSurface()
        .filter(({ file }) => !skip.includes(file))
        .flatMap(({ file, text }) =>
            sentencesOf(text)
                .filter((s) => trigger.test(s) && !(allowed && allowed.test(s)))
                .map((s) => `${file}: "${s}"`));

const flaggedText = (rule: RegExp, skip: string[] = []): string[] =>
    homeSurface()
        .filter(({ file }) => !skip.includes(file))
        .flatMap(({ file, text }) => {
            const hit = visibleText(text).match(rule);
            return hit ? [`${file}: "${hit[0]}"`] : [];
        });

const isFlagged = (trigger: RegExp, allowed: RegExp | null, sentence: string) =>
    trigger.test(sentence) && !(allowed && allowed.test(sentence));

// Google Calendar belongs to the dashboard assistant, not to calls: the home page names it only in
// the connector data file, where the entry says "not by calls".
const GOOGLE_CALENDAR = /Google Calendar/i;
test("W5-T11 'Google Calendar' stands only in the connector data file", () => {
    assert.deepEqual(flaggedText(GOOGLE_CALENDAR, [DATA_FILE]), []);
    assert.equal(GOOGLE_CALENDAR.test("Schedule appointments directly into Google Calendar."), true);
});

// R8 ported: the calling agent does not book into a calendar. The data file's Google Calendar entry
// is about the dashboard assistant and is pinned by public-connector-status.test.ts ("not by calls").
const R8_TRIGGER = /\b(?:book\w*|schedul\w*)\b[^.]{0,50}\b(?:appointments?|meetings?|interviews?|consultations?|showings?|tours?|calendar)\b|\b(?:appointment|interview|meeting) (?:booking|scheduling)\b|added to (?:google )?calendar/i;
const R8_ALLOWED = /\b(?:does not|do not|doesn't|cannot|can't|is not|are not|no calendar)\b|\?$/i;
test("W5-T11 R8 the calling agent does not book into a calendar", () => {
    assert.deepEqual(flaggedSentences(R8_TRIGGER, R8_ALLOWED, [DATA_FILE]), []);
});
test("W5-T11 R8 fixture", () => {
    const f = (s: string) => isFlagged(R8_TRIGGER, R8_ALLOWED, s);
    assert.equal(f("Schedule appointments directly into Google Calendar."), true);
    assert.equal(f("Book meetings directly into your client's calendar."), true);
    assert.equal(f("AI voice agents note the time a caller prefers on incoming calls."), false);
    assert.equal(f("The agent does not book appointments or write to a calendar."), false);
});

// Knowledge: .md and .txt files only. A negative may name what is not done.
const KNOWLEDGE = /(?<![/\w(])\bimages?\b(?![/\w:])|\bscrap\w*|\bimport\w*[^.]{0,30}\bwebsites?\b/i;
const KNOWLEDGE_OK = /\b(?:nothing|not|no|never)\b/i;
test("W5-T11 knowledge is .md and .txt files: no image upload, scraping or website import", () => {
    assert.deepEqual(flaggedSentences(KNOWLEDGE, KNOWLEDGE_OK), []);
});
test("W5-T11 knowledge fixture", () => {
    const f = (s: string) => isFlagged(KNOWLEDGE, KNOWLEDGE_OK, s);
    assert.equal(f("Upload an image or import your website."), true);
    assert.equal(f("We scrape your site for answers."), true);
    assert.equal(f("Nothing is imported from websites."), false);
    assert.equal(f("Upload .md or .txt files to a campaign's knowledge base."), false);
});

// Features the product lacks. "QA" in a comment is removed before this reads the text.
const LACKED = /\bQA\b|quality assurance|live monitoring|A\/B tests?/i;
test("W5-T11 no QA, live monitoring or A/B test claim", () => {
    assert.deepEqual(flaggedText(LACKED), []);
});
test("W5-T11 lacked-features fixture", () => {
    assert.equal(LACKED.test("Access recordings, transcriptions, and custom charts for performance and QA."), true);
    assert.equal(LACKED.test("Live monitoring of every call."), true);
    assert.equal(LACKED.test("Run A/B tests on your script."), true);
    assert.equal(LACKED.test("Review recordings where enabled, transcripts and Analytics charts per campaign."), false);
});

// Ownership of a hosted service (the dedicated-infrastructure rule above holds the phrase list).
test("W5-T11 dedicated-infrastructure fixture", () => {
    const rule = /Your servers|Your GPUs|dedicated infrastructure|dedicated servers|Own Your AI Voice Agent Platform|private cloud/i;
    for (const bad of ["Own Your AI Voice Agent Platform", "Run it in a private cloud.", "Deploy on your servers.", "Use your GPUs."]) assert.equal(rule.test(bad), true, bad);
    assert.equal(rule.test("Configure Your AI Voice Agents"), false);
});

// R16: CRM logging is on the Business plan. The data file's two CRM entries are pinned by
// public-connector-status.test.ts (does + customerMust must say "Business plan").
const LOGS_TO_CRM = /(?:log\w*|sync\w*|record\w*)[^.]{0,60}\b(?:hubspot|salesforce)\b|\b(?:hubspot|salesforce)\b[^.]{0,60}(?:log\w*|sync\w*)/i;
const CRM_OK = /Business plan|\b(?:does not|do not|cannot|can't)\b/i;
test("W5-T11 R16 every sentence saying calls are logged to HubSpot or Salesforce says Business plan", () => {
    assert.deepEqual(flaggedSentences(LOGS_TO_CRM, CRM_OK, [DATA_FILE]), []);
});
test("W5-T11 R16 fixture", () => {
    const f = (s: string) => isFlagged(LOGS_TO_CRM, CRM_OK, s);
    assert.equal(f("Connects to HubSpot and Salesforce to log your calls."), true);
    assert.equal(f("Log calls to HubSpot or Salesforce once connected"), true);
    assert.equal(f("Logs your calls to HubSpot or Salesforce once connected (Business plan)."), false);
    assert.equal(f("See the Connectors section on this page for the status of each."), false);
});

// R12: nothing suggests the caller is misled about talking to an AI. "Voice actor" is not part of it.
const R12_RULE = /don't realize|not speaking with a human|indistinguishable from (?:a |an? actual )?human|sounds? human\b|feels? human|human-like|sounds? like (?:a |an )?(?:real )?(?:human|person|someone)/i;
test("W5-T11 R12 nothing suggests the caller is misled about talking to an AI", () => {
    assert.deepEqual(flaggedText(R12_RULE), []);
});
test("W5-T11 R12 fixture", () => {
    assert.equal(R12_RULE.test("Feels Human"), true);
    assert.equal(R12_RULE.test("deliver human-like customer engagement."), true);
    assert.equal(R12_RULE.test("Natural conversations, each introduced as an AI."), false);
    assert.equal(R12_RULE.test("Choose a voice from the catalogue."), false);
});

// R11: no scale figure and no go-live promise. Phrases only, so honest text is not flagged.
const SCALE = /\bat scale\b|contact centers?|call centers?|\benterprises?\b|simultaneous|\bin (?:just )?(?:a few )?days\b/i;
test("W5-T11 R11 no scale or go-live claim", () => {
    assert.deepEqual(flaggedText(SCALE), []);
});
test("W5-T11 R11 fixture", () => {
    for (const bad of ["Global communication at scale", "power your entire contact center", "for enterprises and call centers", "Simultaneous Calls", "Live in days."]) assert.equal(SCALE.test(bad), true, bad);
    for (const good of ["Calls at Once", "Your plan sets how many calls can run at once.", "Launch one campaign and add more within your plan's limits."]) assert.equal(SCALE.test(good), false, good);
});

// Language: speech recognition is English only and there is no language setting.
const LANGUAGE = /language availability|language options?|multilingual|global communication/i;
const LANGUAGE_OK = /English only/i;
test("W5-T11 language: no language availability, option or multilingual claim", () => {
    assert.deepEqual(flaggedSentences(LANGUAGE, LANGUAGE_OK), []);
});
test("W5-T11 language fixture", () => {
    const f = (s: string) => isFlagged(LANGUAGE, LANGUAGE_OK, s);
    assert.equal(f("Language availability depends on your selected voice and model."), true);
    assert.equal(f("Review supported language options for the voice and model selected for your campaign."), true);
    assert.equal(f("Global communication at scale"), true);
    assert.equal(f("Speech recognition is English only in this release; there is no language setting."), false);
});

// R9 ported to sentences over all text (JSX included). A question promises nothing, so it passes
// unless it promises speed; the answer beside it carries the condition.
const TRANSFER = /transfer|hand(?:s|ed|ing)?[- ]?off|handoff|escalat|rout(?:e|es|ed|ing)\b|steps? in\b|takes? over/i;
const SET_UP_CONDITION = /\b(?:when|if) (?:a |the )?(?:transfer|hand-?off)(?: number| target| destination)? is (?:set up|configured)\b|\bwhen it is set up\b|\bwhen set up\b|\bset up for (?:the|your) campaign\b|\bdoes not guarantee\b|\b(?:does not|do not|doesn't|cannot|can't|never)\b[^.]{0,30}\b(?:transfer|hand|route|escalat)|\bneeds? set-?up\b|\bmust list transfer\b|\btransfer (?:option )?must be on\b|\bswitched on\b/i;
const OVERPROMISE = /always (?:answers|picks up)|instantly/i;
const r9Flagged = (s: string) => TRANSFER.test(s) && (OVERPROMISE.test(s) || !(SET_UP_CONDITION.test(s) || /\?$/.test(s)));
test("W5-T11 R9 every transfer sentence names its set-up condition (all text, not whole strings)", () => {
    const problems = homeSurface().flatMap(({ file, text }) => sentencesOf(text).filter(r9Flagged).map((s) => `${file}: "${s}"`));
    assert.deepEqual(problems, []);
});
test("W5-T11 R9 fixture", () => {
    assert.equal(r9Flagged("Calls are transferred to a person instantly."), true);
    assert.equal(r9Flagged("Transfer every caller to your team."), true);
    assert.equal(r9Flagged("Want every hot lead transferred instantly?"), true);
    assert.equal(r9Flagged("Only when it is set up, and a person answering is not guaranteed."), false);
    assert.equal(r9Flagged("Transfer needs set-up: approved numbers on outgoing calls. An answer is not guaranteed."), false);
    assert.equal(r9Flagged("Can calls be transferred to human agents?"), false);
});

// R14 ported to sentences over all text: a scheduled callback is outgoing-only.
const SCHEDULES_CALLBACK = /schedul\w*[^.]{0,30}call[ -]?backs?|call[ -]?backs?[^.]{0,30}schedul/i;
const CALLBACK_OK = /\b(?:outgoing|outbound)\b|\b(?:does not|do not|cannot|can't)\b/i;
test("W5-T11 R14 a scheduled callback is outgoing-only (all text, not whole strings)", () => {
    assert.deepEqual(flaggedSentences(SCHEDULES_CALLBACK, CALLBACK_OK), []);
});
test("W5-T11 R14 fixture", () => {
    const f = (s: string) => isFlagged(SCHEDULES_CALLBACK, CALLBACK_OK, s);
    assert.equal(f("Schedule callbacks on incoming calls and note the time."), true);
    assert.equal(f("Can the agent schedule a callback?"), true);
    assert.equal(f("Callbacks are scheduled on outgoing campaigns only."), false);
    assert.equal(f("It can note a callback request for your team."), false);
});
