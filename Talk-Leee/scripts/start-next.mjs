#!/usr/bin/env node

import { existsSync } from "node:fs";
import { join } from "node:path";
import { createRequire } from "node:module";
import { spawn } from "node:child_process";

const require = createRequire(import.meta.url);
const nextBin = require.resolve("next/dist/bin/next");
const manifestPath = join(process.cwd(), ".next", "routes-manifest.json");

function runNext(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [nextBin, ...args], {
      stdio: "inherit",
      env: process.env,
    });

    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(new Error(`next ${args.join(" ")} failed with exit code ${code}`));
    });
  });
}

async function main() {
  if (!existsSync(manifestPath)) {
    console.log("[start] Missing .next/routes-manifest.json. Running `next build` first...");
    await runNext(["build"]);
  }

  if (!existsSync(manifestPath)) {
    throw new Error("Build did not produce .next/routes-manifest.json.");
  }

  await runNext(["start"]);
}

main().catch((error) => {
  console.error("[start] Unable to start Next.js:", error instanceof Error ? error.message : String(error));
  process.exit(1);
});
