import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

// W4: the ten industry pages and the navigation lines in src/Industries/industries.ts say only
// what the product does. Voice is the only channel; on a call the agent can schedule a callback
// (outgoing campaigns only), send a follow-up email, submit a configured form, transfer to a
// person (only when set up) or end the call; prices are fixed and public. A guard over the
// literal copy, since the defect is the copy. One test per rule so a failure names the rule, the
// file and the phrase. Each sentence rule has a fixture test: the false sentences it must flag
// and an honest one it must let through.
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

/** True when the rule's trigger matches and nothing in the sentence excuses it. */
const flagged = (trigger: RegExp, allowed: RegExp, sentence: string) => trigger.test(sentence) && !allowed.test(sentence);

// A plain negative: the sentence says the product does NOT do the thing.
const PLAIN_NEGATIVE = /\b(?:does not|doesn't|do not|don't|cannot|can't|is not|isn't|are not|aren't|not available|not in this release)\b|^No\b/i;

test("R1 no white-label or dedicated-workspace claim", () =>
    forbid(/white[- ]label|dedicated workspace/i));

// R2: a system with no connector may be named only to say the product does not connect to it (or to
// ask about it: a question promises nothing).
const NO_CONNECTOR_SYSTEMS = /\bATS\b|applicant tracking|core banking|help ?desk|ticketing|support tickets?|\bEHR\b|\bEMR\b|\bPMS\b|property management system|booking engine|payment gateway|HR (?:software|platforms?)|order systems?|listing feed|\bMLS\b/i;
const SAYS_NOT_CONNECTED = /\b(?:does not|doesn't|do not|don't|cannot|can't) (?:connect|integrate|sync)|\bno (?:direct )?(?:connection|(?:[\w-]+ ){0,3}connector)|\bis not available|\bare not connected|\?$/i;
test("R2 no system without a connector is named as something the product connects to", () =>
    forbidSentences(NO_CONNECTOR_SYSTEMS, SAYS_NOT_CONNECTED));
test("R2 fixture: a plain negative or a question passes, a promise fails", () => {
    const f = (s: string) => flagged(NO_CONNECTOR_SYSTEMS, SAYS_NOT_CONNECTED, s);
    assert.equal(f("A direct connection to ATS or HR software is not available."), false);
    assert.equal(f("Talk-Lee does not connect to your core banking system."), false);
    assert.equal(f("There is no connector for help desk software."), false);
    assert.equal(f("Does Talk-Lee connect to my help desk?"), false);
    assert.equal(f("Integrates with popular Applicant Tracking Systems."), true);
    assert.equal(f("Trigger support tickets automatically."), true);
    assert.equal(f("Automatically trigger customer notifications, follow-ups, support tickets, and internal workflows without manual intervention."), true);
    assert.equal(f("Works with your HR software."), true);
    assert.equal(f("Pulls listings from the MLS."), true);
});

const FINANCIAL = [path.join("src", "app", "industries", "financial-services", "page.tsx")];
const ACCOUNT_ACTIONS = /balance inquiries|card activation|lost or stolen card|account (?:information|details|inquiries)|transactions?/i;
const NO_ACCOUNT_ACCESS = /\b(?:not available|no access|does not|cannot|can't|do not)\b/i;
test("R3 no account-system action on financial-services", () =>
    forbidSentences(ACCOUNT_ACTIONS, NO_ACCOUNT_ACCESS, FINANCIAL));
test("R3 fixture", () => {
    const f = (s: string) => flagged(ACCOUNT_ACTIONS, NO_ACCOUNT_ACCESS, s);
    assert.equal(f("Help customers access account information, recent transactions and more."), true);
    assert.equal(f("Handle balance inquiries."), true);
    assert.equal(f("The agent cannot read account information or transactions."), false);
});

// R4: a question promises nothing, so a trailing "?" is acceptable here.
const R4_TRIGGER = /\breminders?\b|\b(?:multiple|different|preferred|other) languages?\b|language every guest/i;
const R4_ALLOWED = /\?$|\bno language setting\b|\b(?:does not|do not|cannot) (?:send|make|speak)\b/i;
test("R4 no other language, reminder, confirmation, omnichannel, availability or non-voice channel claim", () => {
    assert.deepEqual([
        ...sentenceHits(R4_TRIGGER, R4_ALLOWED),
        ...hits(/multilingual|multi-language|any language/i),
        ...hits(/appointment reminders|payment reminders|sends? reminders|confirmation calls|automated confirmations/i),
        ...hits(/omnichannel|real[- ]time availability/i),
        ...hits(/\bsms\b|whatsapp|live chat|chatbot|email campaigns/i),
    ], []);
});
test("R4 fixture", () => {
    const f = (s: string) => flagged(R4_TRIGGER, R4_ALLOWED, s);
    assert.equal(f("Serve callers in multiple languages."), true);
    assert.equal(f("A Language Every Guest Understands"), true);
    assert.equal(f("Send timely reminders"), true);
    assert.equal(f("Automated reminders, confirmations and follow-ups."), true);
    assert.equal(f("There is no language setting; speech recognition is English only."), false);
    assert.equal(f("Can it send reminders?"), false);
});

// R5: a question does NOT excuse a compliance word.
const R5_TRIGGER = /\b(?:hipaa|gdpr|pci|soc ?2)\b|complian(?:t|ce)/i;
const R5_ALLOWED = /\b(?:does not|do not|cannot|can't|is not|are not|not a guarantee)\b/i;
test("R5 no compliance promise", () =>
    forbidSentences(R5_TRIGGER, R5_ALLOWED));
test("R5 fixture", () => {
    const f = (s: string) => flagged(R5_TRIGGER, R5_ALLOWED, s);
    assert.equal(f("HIPAA-ready voice agents"), true);
    assert.equal(f("Stay compliant with GDPR."), true);
    assert.equal(f("Is it HIPAA compliant?"), true);
    assert.equal(f("Talk-Lee does not make your practice HIPAA compliant."), false);
});

test("R6 connector and Call History vocabulary", () => {
    // The word boundary in front means a code identifier or import path (for example
    // "TravelIntegrationsList") never starts a word with "integrat", so identifiers are not flagged.
    assert.deepEqual([...hits(/\bintegrat\w*/i), ...hits(/call records/i)], []);
    assert.equal(/\bintegrat\w*/i.test("integrating with your CRM"), true);
    assert.equal(/\bintegrat\w*/i.test("import { TravelIntegrationsList } from \"@/components/x\""), false);
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

// R7 (all copy, not only metadata): no availability promise. Calls run in the hours the account configures.
const ALWAYS_AVAILABLE = /24\/7|around the clock|always[- ]on|never miss/i;
test("R7 no round-the-clock or never-miss promise anywhere in the copy", () =>
    forbidSentences(ALWAYS_AVAILABLE, PLAIN_NEGATIVE));
test("R7 fixture", () => {
    const f = (s: string) => flagged(ALWAYS_AVAILABLE, PLAIN_NEGATIVE, s);
    assert.equal(f("Never miss a customer call."), true);
    assert.equal(f("Support customers around the clock."), true);
    assert.equal(f("Always-on coverage for your guests."), true);
    assert.equal(f("24/7 answering."), true);
    assert.equal(f("It is not a 24/7 service."), false);
    assert.equal(f("Calls are answered within your configured hours."), false);
});

// R8: a question promises nothing, so a trailing "?" is acceptable here.
const R8_TRIGGER = /\b(?:book\w*|schedul\w*)\b[^.]{0,50}\b(?:appointments?|meetings?|interviews?|consultations?|showings?|tours?|calendar)\b|\b(?:appointment|interview|meeting) (?:booking|scheduling)\b|added to (?:google )?calendar/i;
const R8_ALLOWED = /\b(?:does not|do not|doesn't|cannot|can't|is not|are not|no calendar)\b|\?$/i;
test("R8 the calling agent does not book into a calendar, screen resumes or schedule interviews", () => {
    assert.deepEqual([
        ...sentenceHits(R8_TRIGGER, R8_ALLOWED),
        ...hits(/books? appointments|appointment booking|appointment scheduling|schedules? interviews|books meetings|booked into your calendar|resume screening|screens resumes/i),
    ], []);
});
test("R8 fixture", () => {
    const f = (s: string) => flagged(R8_TRIGGER, R8_ALLOWED, s);
    assert.equal(f("Book meetings directly into your client's calendar."), true);
    assert.equal(f("Appointments booked over the phone are added to Google Calendar."), true);
    assert.equal(f("Schedule appointments directly into Google Calendar."), true);
    assert.equal(f("AI Interview Scheduling"), true);
    assert.equal(f("The agent does not book appointments or write to a calendar."), false);
    assert.equal(f("Can the agent schedule interviews?"), false);
});

// R9: every sentence about transfer carries the set-up condition. A question does NOT excuse it.
const TRANSFER = /transfer|hand(?:s|ed|ing)?[- ]?off|handoff|escalat|rout(?:e|es|ed|ing)\b|steps? in\b|takes? over/i;
const SET_UP_CONDITION = /\b(?:when|if) (?:a |the )?(?:transfer|hand-?off)(?: number| target| destination)? is (?:set up|configured)\b|\bwhen it is set up\b|\bwhen set up\b|\bset up for (?:the|your) campaign\b|\bdoes not guarantee\b|\b(?:does not|do not|doesn't|cannot|can't|never)\b[^.]{0,30}\b(?:transfer|hand|route|escalat)/i;
const OVERPROMISE = /always (?:answers|picks up)|instantly/i;
const r9Flagged = (s: string) => TRANSFER.test(s) && (!SET_UP_CONDITION.test(s) || OVERPROMISE.test(s));
test("R9 every transfer sentence names its set-up condition, and the banned phrases are gone", () => {
    const sentenceProblems = files.flatMap((file) => sentences(read(file)).filter(r9Flagged).map((s) => `${file}: "${s}"`));
    assert.deepEqual([...sentenceProblems, ...hits(/hand off with context|route calls intelligently|routing specialized requests|can move to your team|seamless hand-?offs?|a person will answer/i)], []);
});
test("R9 fixture: 'approved', a question or 'does not need' is not a condition", () => {
    assert.equal(r9Flagged("Every caller is transferred to your approved team and someone always picks up."), true);
    assert.equal(r9Flagged("Transfer to a person does not need any setup."), true);
    assert.equal(r9Flagged("Want every hot lead handed off instantly?"), true);
    assert.equal(r9Flagged("Your team steps in when expertise is needed."), true);
    assert.equal(r9Flagged("When a transfer is set up, someone always picks up."), true);
    assert.equal(r9Flagged("A live call can go to your team when a transfer is set up for the campaign."), false);
    assert.equal(r9Flagged("The agent does not transfer calls to a person."), false);
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
// knowledge and campaign. No scale figure exists either. "within hours you configure" is about
// calling hours, not a go-live promise, and is kept out by the lookahead.
const GO_LIVE_TIME = /\b(?:in|within)\s+(?:just\s+)?(?:a\s+(?:few|couple of)\s+)?(?:days|hours|minutes|weeks)\b(?! you\b)|days, not months|our team handles the setup|(?:start|launch|go live)\w*[^.]{0,40}\btoday\b/i;
const SCALE_CLAIM = /\b(?:hundreds|thousands|millions)\b|simultaneous/i;
test("R11 no unsourced promise of how soon an account goes live, and no scale figure", () => {
    assert.deepEqual([...hits(GO_LIVE_TIME), ...hits(SCALE_CLAIM)], []);
});
test("R11 fixture", () => {
    for (const bad of ["Transform your hiring process with AI in just a few days.", "Start delivering smarter customer support today.", "Go live within a couple of weeks.", "Manage thousands of customer conversations simultaneously.", "One property or hundreds."]) {
        assert.equal(GO_LIVE_TIME.test(bad) || SCALE_CLAIM.test(bad), true, bad);
    }
    for (const good of ["Calls only go out within hours you configure.", "Setup depends on your phone line, approved knowledge and campaign."]) {
        assert.equal(GO_LIVE_TIME.test(good) || SCALE_CLAIM.test(good), false, good);
    }
});

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

const R12_RULE = /don't realize|not speaking with a human|indistinguishable from (?:a |an? actual )?human|sounds? human\b|feels? human|sounds? like (?:a |an )?(?:real )?(?:human|person|someone)/i;
test("R12 nothing suggests the caller is misled about talking to an AI", () =>
    forbid(R12_RULE));
test("R12 fixture", () => {
    assert.equal(R12_RULE.test("Conversations That Feel Human"), true);
    assert.equal(R12_RULE.test("It sounds like someone who genuinely cares."), true);
    assert.equal(R12_RULE.test("The agent says at the start that it is an AI."), false);
});

// R14: a callback is scheduled only on an outgoing campaign (the AI dialer calls back). On incoming
// calls the agent only notes a callback REQUEST. A question does NOT excuse the sentence.
const SCHEDULES_CALLBACK = /schedul\w*[^.]{0,30}call[ -]?backs?|call[ -]?backs?[^.]{0,30}schedul/i;
const CALLBACK_OK = /\b(?:outgoing|outbound)\b|\b(?:does not|do not|cannot|can't)\b/i;
test("R14 a scheduled callback is outgoing-only", () =>
    forbidSentences(SCHEDULES_CALLBACK, CALLBACK_OK));
test("R14 fixture", () => {
    const f = (s: string) => flagged(SCHEDULES_CALLBACK, CALLBACK_OK, s);
    assert.equal(f("Schedule callbacks when clients ask for one."), true);
    assert.equal(f("Handle incoming calls and schedule callbacks without disrupting your team."), true);
    assert.equal(f("Can the agent schedule a callback?"), true);
    assert.equal(f("On outgoing campaigns, schedule a callback when a client asks."), false);
    assert.equal(f("The agent does not schedule callbacks on incoming calls."), false);
    assert.equal(f("It can note a callback request for your team."), false);
    assert.equal(f("Note callback requests"), false);
});

// R15: nobody is promised an outcome for every lead, customer, candidate or guest.
const EVERY_ONE = /every (?:lead|customer|candidate|guest) (?:hears|gets|receives)/i;
test("R15 no every-lead / customer / candidate / guest promise", () =>
    forbid(EVERY_ONE));
test("R15 fixture", () => {
    assert.equal(EVERY_ONE.test("so every lead hears from you after the call."), true);
    assert.equal(EVERY_ONE.test("Every guest gets a call back."), true);
    assert.equal(EVERY_ONE.test("Leads who agree and confirm their address get an email."), false);
});

// R16: CRM logging is a Business plan feature on the pricing page.
const LOGS_TO_CRM = /(?:log\w*|sync\w*|record\w*)[^.]{0,60}\b(?:hubspot|salesforce)\b|\b(?:hubspot|salesforce)\b[^.]{0,60}(?:log\w*|sync\w*)/i;
const CRM_OK = /Business plan|\b(?:does not|do not|cannot|can't)\b/i;
test("R16 every sentence saying calls go to HubSpot or Salesforce says Business plan", () =>
    forbidSentences(LOGS_TO_CRM, CRM_OK));
test("R16 fixture", () => {
    const f = (s: string) => flagged(LOGS_TO_CRM, CRM_OK, s);
    assert.equal(f("Log call outcomes to HubSpot or Salesforce."), true);
    assert.equal(f("It connects one HubSpot account and logs calls there."), true);
    assert.equal(f("Log call outcomes to HubSpot or Salesforce (Business plan)."), false);
    assert.equal(f("Connect HubSpot, Salesforce, Gmail, Google Calendar or Google Drive."), false);
});
