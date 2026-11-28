import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const website = fileURLToPath(new URL('../', import.meta.url));
const websiteIdentifiers = {
  'src/lib/server-auth.ts': ['talky_at', 'talky_sid'],
  'src/app/auth/login/login-client.tsx': ['talky_rt'],
  // One identifier per line here and below: the repository's secret scan reads line by line and
  // flags a name ending in "token" that is followed by another quoted string.
  'src/lib/auth-token.ts': [
    'talklee_auth_token',
    'talklee.auth.token',
  ],
  'src/lib/auth-context.tsx': ['talky.logout.pending', 'talky.admin.identity.v1', 'talklee.auth.identity.v1'],
  'src/components/auth/askai-signin-notice.tsx': ['talky.askai.signin'],
  'src/components/ui/call-ended-notice.tsx': ['talky.askai.callended'],
  'src/lib/sign-in-ended.ts': ['talky.signin.tab'],
  'src/app/campaigns/new/page.tsx': ['talky:campaign-new:'],
  'src/app/inbound-campaigns/new/page.tsx': ['talky:inbound-campaign-new:step1:', 'talky:inbound-campaign-new:step2:'],
  'src/lib/inbound-campaign-draft.ts': ['talky:inbound-campaign-new:active-draft-id'],
  'src/lib/browser-session.ts': ['talky-admin-session:'],
  'src/components/providers/theme-provider.tsx': ['talklee.theme'],
  'src/app/book-a-demo/demo-booking.tsx': ['talklee.demo-request.saved'],
  'src/components/notifications/qualified-lead-alerts.tsx': ['talklee.qlead.seen.v1', 'talklee.campaign-alerts.seen.v1:'],
  'src/lib/billing-purchase.ts': ['talklee.billing-purchase.v1:'],
  'src/lib/call-history-workflow.ts': ['talklee.call-history.workflow.v1'],
  'src/lib/contact-enquiry-draft.ts': ['talklee.contact-enquiry.v1'],
  'src/lib/email-audit.ts': ['talklee.email.audit.v1', 'talklee.email.audit.v2:'],
  'src/lib/notifications.ts': ['talklee.notifications.v1', 'talklee.notifications.settings.v1',
    'talklee.notifications.v2:', 'talklee.notifications.settings.v2:', 'talklee.notifications.clear.v1:'],
  'src/lib/sidebar.ts': ['talklee.sidebar.state.v1'],
  'src/lib/support-session.ts': ['talklee.support.conversation.v1:'],
  'src/lib/telephony-number-requests-api.ts': ['talklee.number-request-key.v1:'],
  'src/lib/use-call-history-workflow.ts': ['talklee.call-history.pending.v1:', 'talklee.call-history.imported.v1:'],
  'src/lib/api.ts': ['X-Talky-Expected-User', 'X-Talky-Expected-Tenant'],
  'src/lib/http-client.ts': ['X-Talky-Expected-User', 'X-Talky-Expected-Tenant'],
  'src/proxy.ts': ['x-talklee-mw-internal'],
  'src/components/connectors/salesforce-settings.tsx': ['Talky_Notes__c', 'Talky_Campaign_Id__c'],
  'src/components/layout/sidebar.tsx': ['talklee-sidebar'],
  'tests/dashboard-audit.spec.ts': ['.talklee-sidebar'],
  'src/app/privacy/privacy-content.test.ts': ['Talk-Leee/'],
};
const protectedExamples = [
  ...Object.values(websiteIdentifiers).flat(), 'talky_oauth_nonce_example', 'talky:campaign-new:account:user:wizard',
  'talky:inbound-campaign-new:step2:example', 'talky-admin-session:example',
  'X-Talky-Token',
  'Talky_Phone__c', 'Talky.ai call id:', 'talky_business_month',
  'talky_basic_month', 'talky_basic_year', 'talky_business_year', 'talky_payment_test_month',
  'TalkyAI-VoiceAgent/1.0', 'talkleeai.com', 'whyChooseTalkLee', 'withTalkLee', 'whyTalkLee',
];

function fixture(t, dir, files) {
  const root = mkdtempSync(path.join(tmpdir(), 'talk-lee-brand-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(path.join(root, dir));
  for (const [file, contents] of Object.entries(files)) {
    writeFileSync(path.join(root, dir, file), contents);
  }
  return root;
}

function run(script, cwd) {
  const result = spawnSync(process.execPath, [path.join(website, 'scripts', script)], {
    cwd, encoding: 'utf8',
  });
  assert.ifError(result.error);
  return result;
}

test('reports each display spelling, including beside a protected token', (t) => {
  const terms = ['VoiceFluid', 'Talk-Leee', 'Talky', 'Talkly', 'Talky.ai', 'talky.ai',
    'talkly.ai', 'Talklee', 'TalkLee', 'talk-lee.ai'];
  const root = fixture(t, 'src', Object.fromEntries(terms.map((term, i) => [
    `example-${i}.tsx`, `"talky_at"; "Try ${term} today."`,
  ])));
  const result = run('check-branding.js', root);
  assert.equal(result.status, 1);
  terms.forEach((_, i) => assert.ok(result.stderr.includes(`example-${i}.tsx`), result.stderr));
});

test('public metadata and vector assets are checked', (t) => {
  const files = { 'site.webmanifest': '{"name":"Talklee"}', 'logo.svg': '<title>TalkLee</title>' };
  const result = run('check-branding.js', fixture(t, 'public', files));
  assert.equal(result.status, 1);
  for (const name of Object.keys(files)) assert.ok(result.stderr.includes(name), result.stderr);
});

test('protected identifiers pass the checker', (t) => {
  const result = run('check-branding.js', fixture(t, 'src', { 'contract.ts': protectedExamples.join('\n') }));
  assert.equal(result.status, 0, result.stderr);
});

test('the fix script preserves contracts beside an actual display correction', (t) => {
  const original = protectedExamples.join('\n');
  const root = fixture(t, 'src', { 'contract.ts': original, 'mixed.js': '"Talky" + "Talky_Notes__c"' });
  const result = run('fix-branding.js', root);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(readFileSync(path.join(root, 'src/contract.ts'), 'utf8'), original);
  assert.equal(readFileSync(path.join(root, 'src/mixed.js'), 'utf8'), '"Talk-Lee" + "Talky_Notes__c"');
});

test('the website still contains its established wire and storage identifiers', () => {
  for (const [file, identifiers] of Object.entries(websiteIdentifiers)) {
    const source = readFileSync(path.join(website, file), 'utf8');
    for (const identifier of identifiers) assert.ok(source.includes(identifier), `${file}: ${identifier}`);
  }
});
