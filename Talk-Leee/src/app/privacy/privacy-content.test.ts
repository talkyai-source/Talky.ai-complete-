import { test } from "node:test";
import assert from "node:assert/strict";
import type { LegalBlock } from "@/components/legal/legal-document";
import { privacyBlocks, privacyIntro } from "./privacy-content";
import { termsBlocks } from "../terms/terms-content";

// QA F1-06 (11 Nov 2026): the Privacy Policy must name the outside services the
// product really sends personal data to, and neither legal page may point to a
// /dpa page that does not exist. Every service below was found in the code, not
// in the old policy; `evidence` is where the code sends data to it (paths from
// the repository root), so a failure says what to look at. A guard over the
// literal copy, since the defect is the copy.
const textOf = (blocks: LegalBlock[]): string =>
    blocks
        .flatMap((block) => [
            "title" in block ? block.title : "",
            "text" in block ? block.text : "",
            ...("items" in block ? block.items : []),
            ...("headers" in block ? block.headers : []),
            ...("rows" in block ? block.rows.flat() : []),
        ])
        .join("\n");

const isTable = (block: LegalBlock): block is Extract<LegalBlock, { type: "table" }> => block.type === "table";

// The blocks between the h2 whose title matches and the next h2.
function section(title: RegExp): LegalBlock[] {
    const start = privacyBlocks.findIndex((block) => block.type === "h2" && title.test(block.text));
    if (start < 0) return [];
    const next = privacyBlocks.findIndex((block, index) => index > start && block.type === "h2");
    return privacyBlocks.slice(start + 1, next < 0 ? undefined : next);
}

const policyText = [...privacyIntro, textOf(privacyBlocks)].join("\n");
const termsText = textOf(termsBlocks);
const whoBlocks = section(/who processes your data/i);
const whoText = textOf(whoBlocks);
const whoTable = whoBlocks.find(isTable);
const serviceRows = whoTable?.rows ?? [];

// The first cell of a row is the service's name.
const PROCESSORS: { label: string; match: RegExp; evidence: string }[] = [
    { label: "Hetzner", match: /^Hetzner/, evidence: "docs/DEPLOYMENT.md:13-20; backend/deploy/infra/compose.prod.yml:29-34,80-83" },
    { label: "Vercel", match: /^Vercel/, evidence: "docs/DEPLOYMENT.md:20; docs/ARCHITECTURE.md:70" },
    {
        label: "Deepgram",
        match: /^Deepgram/,
        evidence: "backend/app/infrastructure/stt/deepgram_flux.py:558; tts/deepgram_tts.py:178; stt/deepgram_prerecorded.py:36",
    },
    { label: "AssemblyAI", match: /^AssemblyAI/, evidence: "backend/app/infrastructure/stt/assemblyai.py:43-45" },
    {
        label: "Groq",
        match: /^Groq/,
        evidence: "backend/app/infrastructure/llm/groq.py:413; domain/services/call_summary/summarizer.py:23; infrastructure/assistant/agent.py:3,130",
    },
    { label: "Google (Gemini)", match: /Gemini/, evidence: "backend/app/infrastructure/llm/gemini.py:155" },
    {
        label: "OpenAI",
        match: /^OpenAI/,
        evidence: "backend/app/infrastructure/llm/openai.py:58; backend/app/realtime/openai.py:72",
    },
    { label: "Cerebras", match: /^Cerebras/, evidence: "backend/app/infrastructure/llm/cerebras.py:155" },
    { label: "DeepSeek", match: /^DeepSeek/, evidence: "backend/app/infrastructure/llm/deepseek.py:42" },
    { label: "Cartesia", match: /^Cartesia/, evidence: "backend/app/infrastructure/tts/cartesia.py:63" },
    {
        label: "ElevenLabs",
        match: /^ElevenLabs/,
        evidence: "backend/app/infrastructure/tts/elevenlabs_tts.py:70; tts/elevenlabs_clone.py:31",
    },
    { label: "Google (Cloud Text-to-Speech)", match: /Text-to-Speech/, evidence: "backend/app/infrastructure/tts/google_tts.py:31" },
    {
        label: "BlazeDigitel",
        match: /^BlazeDigitel/,
        evidence: "backend/app/services/scripts/seed_platform_sip_trunk.py:3; backend/app/domain/services/telephony/trunk_resolver.py:25",
    },
    {
        label: "Vonage",
        match: /^Vonage/,
        evidence: "backend/app/infrastructure/telephony/vonage_provider_adapter.py:7; backend/app/services/sms_service.py:62",
    },
    { label: "Twilio", match: /^Twilio/, evidence: "backend/app/infrastructure/telephony/provider_factory.py:232" },
    { label: "Microsoft 365 (account email)", match: /^Microsoft 365/, evidence: "backend/app/domain/services/email_service.py:3-8,48" },
    { label: "Stripe", match: /^Stripe/, evidence: "backend/app/domain/services/billing_checkout.py:360" },
    {
        label: "Google (Calendar, Gmail, Drive)",
        match: /Calendar, Gmail, Drive/,
        evidence: "backend/app/infrastructure/connectors/calendar/google_calendar.py:37; email/gmail.py:128; drive/google_drive.py:37",
    },
    { label: "Microsoft (Outlook Calendar)", match: /Outlook/, evidence: "backend/app/infrastructure/connectors/calendar/outlook_calendar.py:44" },
    { label: "HubSpot", match: /^HubSpot/, evidence: "backend/app/infrastructure/connectors/crm/hubspot.py:38" },
    { label: "Salesforce", match: /^Salesforce/, evidence: "backend/app/infrastructure/connectors/crm/salesforce.py:52" },
    { label: "Sentry", match: /^Sentry/, evidence: "backend/app/core/sentry_init.py:94; Talk-Leee/src/instrumentation.ts" },
];

// Named in the old policy, but the code sends them nothing.
const NOT_USED: { label: string; match: RegExp; reason: string }[] = [
    {
        label: "Supabase",
        match: /Supabase/i,
        reason: "the database is PostgreSQL in Docker on the Hetzner server (docs/DEPLOYMENT.md:13-20)",
    },
    {
        label: "SendGrid",
        match: /SendGrid/i,
        reason: "account email goes through Microsoft 365 SMTP (notification_service.py:117); the SendGrid helper at :131 has no caller",
    },
    {
        label: "AWS",
        match: /\bAWS\b|Amazon/i,
        reason: "the SES helper has no caller, and recordings go to local disk unless S3-compatible storage is configured (recording_service.py:243-293, 801-885)",
    },
];

test("the policy has a 'Who processes your data' section with a table of services", () => {
    assert.ok(whoBlocks.length > 0, "no section titled 'Who Processes Your Data'");
    assert.ok(whoTable, "the section has no table");
    assert.equal(whoTable.headers.length, 3, "expected: service, what it does, what data");
});

test("every row says what the service does and what personal data it receives", () => {
    assert.ok(serviceRows.length > 0, "the table has no rows");
    for (const row of serviceRows) {
        assert.equal(row.length, whoTable?.headers.length, `row "${row[0]}" has the wrong number of cells`);
        for (const cell of row) assert.ok(cell.trim().length > 0, `row "${row[0]}" has an empty cell`);
    }
    const names = serviceRows.map((row) => row[0]);
    assert.equal(new Set(names).size, names.length, "a service is listed twice");
});

for (const { label, match, evidence } of PROCESSORS) {
    test(`the policy names ${label} as a service that receives personal data`, () => {
        assert.ok(
            serviceRows.some((row) => match.test(row[0] ?? "")),
            `${label} is missing from "Who Processes Your Data" (code: ${evidence})`,
        );
    });
}

test("the policy names the two services a visitor's browser contacts directly", () => {
    // Talk-Leee/src/app/book-a-demo/page.tsx:22-23 and
    // Talk-Leee/src/app/auth/login/login-client.tsx:83.
    assert.match(whoText, /Book a Demo/);
    assert.match(whoText, /Google Calendar/);
    assert.match(whoText, /Pwned Passwords/);
});

for (const { label, match, reason } of NOT_USED) {
    test(`the policy does not name ${label}`, () => {
        assert.equal(match.test(policyText), false, `${label} is named, but ${reason}`);
    });
}

test("the 'running AI voice calling campaigns' line names every speech, language and voice provider", () => {
    const line = privacyBlocks
        .flatMap((block) => ("items" in block ? block.items : []))
        .find((item) => item.startsWith("Running AI voice calling campaigns"));
    assert.ok(line, "the line is missing from section 3.1");
    for (const name of ["Deepgram", "AssemblyAI", "Groq", "Gemini", "OpenAI", "Cerebras", "DeepSeek", "ElevenLabs", "Cartesia"]) {
        assert.ok(line.includes(name), `${name} is missing from: ${line}`);
    }
});

test("neither the policy nor the Terms point to a /dpa page", () => {
    assert.equal(/\/dpa\b/i.test(policyText), false);
    assert.equal(/\/dpa\b/i.test(termsText), false);
});

test("both say the DPA is available on request from privacy@talkleeai.com", () => {
    assert.match(termsText, /\(DPA\) is available on request from privacy@talkleeai\.com and is incorporated into these Terms/);
    assert.match(policyText, /Data Processing Agreement available on request from privacy@talkleeai\.com/);
});

test("the promises that were not wrong are kept", () => {
    assert.match(policyText, /We do not sell, rent, license, or trade your personal data/);
    assert.match(policyText, /we never store card numbers/);
    assert.match(policyText, /Standard Contractual Clauses/);
    assert.match(policyText, /We do not use Google Analytics/);
});
