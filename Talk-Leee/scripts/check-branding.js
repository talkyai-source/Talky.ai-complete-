const fs = require('fs');
const path = require('path');

const {
  FORBIDDEN_TERMS,
  REPLACEMENT_TERM,
  FILE_PATTERN,
  violations,
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
        const diagnostics = [];

        lines.forEach((line, index) => {
          const hits = violations(line);
          if (hits.length > 0) {
            diagnostics.push(`   Line ${index + 1} [${hits.join(', ')}]`);
          }
        });

        if (diagnostics.length > 0) {
          console.error(`❌ Branding violation found in: ${filePath}`);
          diagnostics.forEach((entry) => console.error(entry));
          found = true;
        }
      }
    }
  }
  return found;
}

console.log(`🔍 Checking for forbidden branding terms (${FORBIDDEN_TERMS.map((t) => `"${t}"`).join(', ')})...`);
const srcFailed = searchFiles('src');
const publicFailed = searchFiles('public');
if (srcFailed || publicFailed) {
  console.error(`\nFAILED: Forbidden branding terms found.`);
  console.error(`Review each spelling against the product name "${REPLACEMENT_TERM}".`);
  process.exit(1);
} else {
  console.log('✅ Branding check passed.');
  process.exit(0);
}
