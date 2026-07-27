const fs = require('fs');
const path = require('path');

const {
  FORBIDDEN_TERMS,
  REPLACEMENT_TERM,
  SEARCH_DIR,
  FILE_PATTERN,
  toGlobalRegExp,
} = require('./branding-terms');

function fixFiles(dir) {
  let count = 0;

  if (!fs.existsSync(dir)) {
    return 0;
  }

  const files = fs.readdirSync(dir);

  for (const file of files) {
    const filePath = path.join(dir, file);
    const stat = fs.statSync(filePath);

    if (stat.isDirectory()) {
      count += fixFiles(filePath);
    } else {
      if (file.match(FILE_PATTERN)) {
        const original = fs.readFileSync(filePath, 'utf8');
        let content = original;
        const applied = [];

        // Sequential, most-specific-first (see branding-terms.js) so that
        // "Talky.ai" is rewritten whole rather than left as "Talk-Lee.ai".
        for (const term of FORBIDDEN_TERMS) {
          if (!content.includes(term)) continue;
          const occurrences = content.split(term).length - 1;
          content = content.replace(toGlobalRegExp(term), REPLACEMENT_TERM);
          applied.push(`${term}×${occurrences}`);
        }

        if (content !== original) {
          console.log(`🔧 Fixing branding in: ${filePath} (${applied.join(', ')})`);
          fs.writeFileSync(filePath, content, 'utf8');
          count++;
        }
      }
    }
  }
  return count;
}

console.log(`🔍 Replacing ${FORBIDDEN_TERMS.map((t) => `"${t}"`).join(', ')} with "${REPLACEMENT_TERM}"...`);
const fixedCount = fixFiles(SEARCH_DIR);
if (fixedCount > 0) {
  console.log(`✅ Fixed branding in ${fixedCount} files.`);
} else {
  console.log('✅ No branding issues found to fix.');
}
