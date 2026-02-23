const fs = require('fs');
const path = require('path');

const FORBIDDEN_TERM = 'VoiceFluid';
const REPLACEMENT_TERM = 'Talk-Lee';
const SEARCH_DIR = 'src';

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
      if (file.match(/\.(tsx|ts|js|jsx|css|md|json)$/)) {
        const content = fs.readFileSync(filePath, 'utf8');
        if (content.includes(FORBIDDEN_TERM)) {
          console.error(`❌ Branding violation found in: ${filePath}`);
          // Find line number
          const lines = content.split('\n');
          lines.forEach((line, index) => {
            if (line.includes(FORBIDDEN_TERM)) {
               console.error(`   Line ${index + 1}: ${line.trim()}`);
            }
          });
          found = true;
        }
      }
    }
  }
  return found;
}

console.log(`🔍 Checking for forbidden branding terms ("${FORBIDDEN_TERM}")...`);
if (searchFiles(SEARCH_DIR)) {
  console.error(`\nFAILED: Forbidden branding terms found.`);
  console.error(`Please replace "${FORBIDDEN_TERM}" with "${REPLACEMENT_TERM}".`);
  process.exit(1);
} else {
  console.log('✅ Branding check passed.');
  process.exit(0);
}
