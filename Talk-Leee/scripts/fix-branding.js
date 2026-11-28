const fs = require('fs');
const path = require('path');

const {
  FORBIDDEN_TERMS,
  REPLACEMENT_TERM,
  FILE_PATTERN,
  fixDisplaySpellings,
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
        const content = fixDisplaySpellings(original);

        if (content !== original) {
          console.log(`🔧 Fixing branding in: ${filePath}`);
          fs.writeFileSync(filePath, content, 'utf8');
          count++;
        }
      }
    }
  }
  return count;
}

console.log(`🔍 Replacing ${FORBIDDEN_TERMS.map((t) => `"${t}"`).join(', ')} with "${REPLACEMENT_TERM}"...`);
const fixedCount = fixFiles('src');
if (fixedCount > 0) {
  console.log(`✅ Fixed branding in ${fixedCount} files.`);
} else {
  console.log('✅ No branding issues found to fix.');
}
