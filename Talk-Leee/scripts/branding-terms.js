/**
 * Single source of truth for the brand check (`npm run check-branding`).
 *
 * The product is spelled **Talk-Lee**. Display spellings are checked
 * case-sensitively; lowercase forms (domains, emails) are reported and need a
 * manual edit, never an automatic one.
 *
 * The `contracts` below are NOT brand text. They are names other systems hold:
 * auth cookies, browser-storage keys, request headers, Salesforce field names
 * customers created, the marker inside CRM call records, Stripe lookup keys.
 * Renaming one signs every user out, drops saved state or breaks a customer's
 * set-up. Add a new one here as an exact pattern; never skip a whole file.
 */
const FORBIDDEN_TERMS = [
  'VoiceFluid', 'Talky.ai', 'Talk-Leee', 'Talkly', 'Talky', 'Talklee', 'TalkLee',
];
const CHECK_ONLY_TERMS = ['talk-lee.ai', 'talky.ai', 'talkly', 'talky'];
const REPLACEMENT_TERM = 'Talk-Lee';
const FILE_PATTERN = /\.(?:tsx?|jsx?|css|md|json|webmanifest|svg|html?|xml|txt)$/i;

// Keep these alternatives ahead of the display spellings. Consume only the
// protected token so that a second occurrence in the same line is still checked.
const contracts = [
  /\btalky_(?:at|rt|sid)\b/,
  /\btalky_oauth_nonce_[\w-]*/,
  /\btalky\.(?:logout\.pending|askai\.(?:signin|callended)|signin\.tab|admin\.identity\.v1)\b/,
  /\btalky:(?:inbound-)?campaign-new:/, /\btalky-admin-session:/,
  /\bX-Talky-(?:Expected-(?:User|Tenant)|Token)\b/,
  /\bTalky_(?:Phone|Notes|Campaign_Id)__c\b/,
  /\bTalky\.ai call id:/,
  /\btalky_(?:(?:basic|business)_(?:month|year)|payment_test_month)\b/,
  /\bTalkyAI-VoiceAgent\/1\.0\b/,
  /\bTalk-Leee(?=[/\\])/,
  /\b(?:whyChooseTalkLee|whyTalkLee|withTalkLee)\b/,
  /(?<=postgresql:\/\/[^\s'"/]+\/)talky(?=["'?#\s]|$)/,
];

function literal(term) {
  return term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

const allTerms = [...FORBIDDEN_TERMS, ...CHECK_ONLY_TERMS];
const matches = new RegExp([
  ...contracts.map(pattern => pattern.source),
  `(?<spelling>${allTerms.map(literal).join('|')})`,
].join('|'), 'g');

function violations(text) {
  return [...new Set(Array.from(text.matchAll(matches), hit => hit.groups.spelling).filter(Boolean))];
}

function fixDisplaySpellings(text) {
  return text.replace(matches, (original, spelling) =>
    FORBIDDEN_TERMS.includes(spelling) ? REPLACEMENT_TERM : original);
}

module.exports = { FORBIDDEN_TERMS, REPLACEMENT_TERM, FILE_PATTERN, violations, fixDisplaySpellings };
