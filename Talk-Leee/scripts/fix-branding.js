const fs = require('fs');
const path = require('path');

const FORBIDDEN_TERM = 'VoiceFluid';
const REPLACEMENT_TERM = 'Talk-Lee';
const SEARCH_DIR = 'src';

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
      if (file.match(/\.(tsx|ts|js|jsx|css|md|json)$/)) {
        let content = fs.readFileSync(filePath, 'utf8');
        if (content.includes(FORBIDDEN_TERM)) {
          console.log(`🔧 Fixing branding in: ${filePath}`);
          const regex = new RegExp(FORBIDDEN_TERM, 'g');
          content = content.replace(regex, REPLACEMENT_TERM);
          fs.writeFileSync(filePath, content, 'utf8');
          count++;
        }
      }
    }
  }
  return count;
}

console.log(`🔍 Automatically replacing "${FORBIDDEN_TERM}" with "${REPLACEMENT_TERM}"...`);
const fixedCount = fixFiles(SEARCH_DIR);
if (fixedCount > 0) {
  console.log(`✅ Fixed branding in ${fixedCount} files.`);
} else {
  console.log('✅ No branding issues found to fix.');
}
