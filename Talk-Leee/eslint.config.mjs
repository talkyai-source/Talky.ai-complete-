import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Typographical-only rules — not real bugs. Downgrade to warning so a
  // stray apostrophe in marketing copy can't block a production deploy.
  {
    rules: {
      // Honour the underscore convention this codebase already uses for
      // deliberately-unused bindings. The auth wrappers in lib/*-utils.ts keep
      // a leading `_token` parameter on their public signatures for call-site
      // compatibility (the shared HTTP client owns the token now) — that is an
      // intentional, documented signature, not dead code the rule should chase.
      // Everything NOT prefixed with `_` is still reported exactly as before.
      "@typescript-eslint/no-unused-vars": [
        "warn",
        {
          args: "after-used",
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
      "react/no-unescaped-entities": "warn",
      // Next 16 enables the React Compiler-oriented hook diagnostics by
      // default. Keep the established rules-of-hooks gate as an error, but
      // introduce the new migration diagnostics as warnings so existing UI
      // patterns remain visible without turning the framework upgrade into a
      // broad, unrelated component rewrite.
      "react-hooks/static-components": "warn",
      "react-hooks/use-memo": "warn",
      "react-hooks/void-use-memo": "warn",
      "react-hooks/preserve-manual-memoization": "warn",
      "react-hooks/immutability": "warn",
      "react-hooks/globals": "warn",
      "react-hooks/refs": "warn",
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/error-boundaries": "warn",
      "react-hooks/purity": "warn",
      "react-hooks/set-state-in-render": "warn",
      "react-hooks/config": "warn",
      "react-hooks/gating": "warn",
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "scripts/**",
    ".storybook/**",
    "storybook-static/**",
    "playwright-report/**",
    "test-results/**",
    "next-env.d.ts",
    "next.config.js",
  ]),
]);

export default eslintConfig;
