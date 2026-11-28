import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

// Source-text guard: PROVIDERS in page.tsx is not exported, so the card data is
// read from the file text. Run from the frontend package folder (as npm test does).
const source = readFileSync(path.join(process.cwd(), "src/app/connectors/page.tsx"), "utf8");

function cards(): Array<{ name: string; description: string }> {
    const out: Array<{ name: string; description: string }> = [];
    const re = /name: "([^"]+)",\s*description: "([^"]+)",/g;
    for (let m = re.exec(source); m; m = re.exec(source)) out.push({ name: m[1], description: m[2] });
    return out;
}
const sentence = (name: string) => cards().find((c) => c.name === name)?.description;

test("W10-T3: the five card names are unchanged", () => {
    assert.deepEqual(cards().map((c) => c.name), ["Google Calendar", "Gmail", "HubSpot", "Google Drive", "Salesforce"]);
});

test("W10-T2: no card or heading promises what the code does not do", () => {
    assert.doesNotMatch(sentence("Google Drive") ?? "", /recording/i);
    assert.doesNotMatch(sentence("Google Calendar") ?? "", /reminder/i);
    assert.doesNotMatch(source, /description="[^"]*integration/i);
    assert.doesNotMatch(source, /automate workflows/i);
});

test("W10-T1: each card sentence is pinned", () => {
    // backend/app/infrastructure/assistant/tools/calendar_read.py:87 (read_calendar_events),
    // tools/meetings.py:127 book_meeting, :194 update_meeting_tool, :250 cancel_meeting_tool,
    // api/v1/endpoints/meetings.py:217/318/359. No calling-agent calendar tool exists.
    assert.equal(sentence("Google Calendar"), "Lets the assistant read your upcoming events and book, change or cancel meetings.");
    // tools/inbox.py:318 read_emails, :404 read_email; tools/comms.py:215 send_email via
    // services/email_service.py:94 (connector resolve) and connectors/email/gmail.py:283 send_email.
    assert.equal(sentence("Gmail"), "Lets the assistant read your inbox and send emails from your account.");
    // services/crm_sync_service.py:677-681 (HubSpot contact needs an email), :634 _resolve_contact,
    // connectors/crm/hubspot.py:263 log_call, :234 create_contact.
    // Not "each call": call logging can be switched off, and test calls are skipped.
    assert.equal(sentence("HubSpot"), "Logs calls on the matching HubSpot contact, and creates the contact when an email is known.");
    // tools/drive.py:119 drive_list_files, :166 drive_read_file. services/drive_sync_service.py has
    // no caller anywhere in backend/app, so no recording or transcript is uploaded.
    assert.equal(sentence("Google Drive"), "Lets the assistant list your files and read text files.");
    // connectors/crm/salesforce.py:512-531 Lead, :555-571 Task, crm_sync_service.py:587 log_calls
    // toggle (so not "every" call), api/v1/endpoints/salesforce.py:839 callback-requests route.
    assert.equal(sentence("Salesforce"), "Logs calls as Salesforce Tasks, creates Leads for new callees, and lets Salesforce request agent callbacks.");
});

test("W10-T1: the page description and the providers line are pinned", () => {
    assert.match(source, /description="Configure connectors and manage connector accounts\."/);
    assert.match(source, /<CardDescription>Connect an account to use it with the assistant and your call records\.<\/CardDescription>/);
});
