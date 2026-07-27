const fs = require('fs');
const path = require('path');

const {
  FORBIDDEN_TERMS,
  CHECK_ONLY_TERMS,
  REPLACEMENT_TERM,
  SEARCH_DIR,
  FILE_PATTERN,
  isAllowlistedLine,
} = require('./branding-terms');

function searchFiles(dir) {
  let found = false;

  if (!fs.existsSync(dir)) {
    return false;
  }

  const files = fs.readdirSync(dir);

  for (const file of files) {
    const filePath = path.join(dir, file);
    const stat = fs.statSync(filePath);

    if (stat.isDirectory()) {
      if (searchFiles(filePath)) found = true;
    } else {
      if (file.match(FILE_PATTERN)) {
        const content = fs.readFileSync(filePath, 'utf8');
        const lines = content.split('\n');
        const violations = [];

        lines.forEach((line, index) => {
          const hits = FORBIDDEN_TERMS.filter((term) => line.includes(term));
          // Lowercase forms live in domains/emails — flag, never auto-rewrite,
          // and ignore the auth-cookie identifiers that legitimately match.
          const softHits = CHECK_ONLY_TERMS.filter(
            (term) => line.includes(term) && !isAllowlistedLine(line, term),
          );
          const all = [...hits, ...softHits];
          if (all.length > 0) {
            violations.push(`   Line ${index + 1} [${all.join(', ')}]: ${line.trim()}`);
          }
        });

        if (violations.length > 0) {
          console.error(`❌ Branding violation found in: ${filePath}`);
          violations.forEach((v) => console.error(v));
          found = true;
        }
      }
    }
  }
  return found;
}

console.log(`🔍 Checking for forbidden branding terms (${FORBIDDEN_TERMS.map((t) => `"${t}"`).join(', ')})...`);
if (searchFiles(SEARCH_DIR)) {
  console.error(`\nFAILED: Forbidden branding terms found.`);
  console.error(`The product is spelled "${REPLACEMENT_TERM}". Run \`npm run fix-branding\` to correct them.`);
  process.exit(1);
} else {
  console.log('✅ Branding check passed.');
  process.exit(0);
}
