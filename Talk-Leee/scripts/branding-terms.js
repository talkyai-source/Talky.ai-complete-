/**
 * Single source of truth for brand-name enforcement.
 *
 * The product is spelled **Talk-Lee** — hyphen, both words capitalised.
 * Historic and misspelt forms below are rejected by `npm run check-branding`
 * and rewritten by `npm run fix-branding`.
 *
 * IMPORTANT — every term here is matched CASE-SENSITIVELY and every one of
 * them is capitalised. That is deliberate: the lowercase forms are wire
 * contracts shared with the backend and MUST NOT be rewritten. Specifically,
 * these are not brand text and are intentionally left alone:
 *
 *   talky_at / talky_rt / talky_sid   HttpOnly auth cookies set by the API
 *   talklee_auth_token                legacy auth cookie still honoured
 *   talklee.auth.token                localStorage key for the Bearer fallback
 *   talklee_call_id                   API response field
 *   x-talklee-mw-internal             middleware bypass header
 *   __talkleeSql                      server-side postgres client global
 *   talkleeai.com / api.talkleeai.com the actual domains
 *   .talklee-sidebar                  CSS hook asserted by visual tests
 *
 * Renaming any of those would log every user out or break requests, so keep
 * new entries capitalised and display-only.
 */

// Order matters: more specific forms first, so "Talky.ai" is consumed before
// the bare "Talky" rule can turn it into "Talk-Lee.ai".
const FORBIDDEN_TERMS = [
  'VoiceFluid',   // pre-launch working name
  'Talky.ai',     // old domain-style wordmark
  'Talk-Leee',    // triple-e typo (also the repo folder name)
  'Talkly',       // misspelling across the marketing pages
  'Talky',        // misspelling across the marketing pages
];

const REPLACEMENT_TERM = 'Talk-Lee';

/**
 * Misspellings that appear in LOWERCASE contexts — domains, emails, URLs.
 * These are reported by `check-branding` but deliberately NOT auto-rewritten,
 * because blind substitution would produce nonsense like "admin@Talk-Lee.ai".
 * A human has to pick the right domain, so fix these by hand.
 */
const CHECK_ONLY_TERMS = [
  'talkly',   // e.g. admin@talkly.ai
  'talky',    // e.g. talky.ai — NOTE: safe here only because the real
              // cookie/storage identifiers are talky_at / talky_rt /
              // talky_sid / talklee_*, which are excluded below.
];

/**
 * Substrings that legitimately contain a lowercase term above. A line matching
 * any of these is not a branding violation — these are wire contracts with the
 * backend (see the header comment).
 */
const CHECK_ONLY_ALLOWLIST = [
  'talky_at',
  'talky_rt',
  'talky_sid',
  // Client-side localStorage flag coordinating logout across tabs. Deliberately
  // NOT renamed: a tab that queued this key before a deploy would have its
  // pending logout silently dropped by the newly-deployed code. Never shown to
  // a user, so the misspelling has no visible effect. Rename only alongside a
  // migration that reads both keys for one release.
  'talky.logout.pending',
];

const SEARCH_DIR = 'src';

const FILE_PATTERN = /\.(tsx|ts|js|jsx|css|md|json)$/;

/** Escape a literal so it can be used inside a RegExp ("Talky.ai" has a dot). */
function toGlobalRegExp(term) {
  return new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g');
}

/** True when a line's only lowercase-term hits are known wire contracts. */
function isAllowlistedLine(line, term) {
  let remaining = line;
  for (const allowed of CHECK_ONLY_ALLOWLIST) {
    remaining = remaining.split(allowed).join('');
  }
  return !remaining.includes(term);
}

module.exports = {
  FORBIDDEN_TERMS,
  CHECK_ONLY_TERMS,
  CHECK_ONLY_ALLOWLIST,
  REPLACEMENT_TERM,
  SEARCH_DIR,
  FILE_PATTERN,
  toGlobalRegExp,
  isAllowlistedLine,
};
