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
  // QA F12-09 (QA-CMP-017): accessibility checks beyond Next's defaults, as
  // errors. Every rule here passes today, so a regression fails the lint gate.
  // `ul`/`ol` with role="list" stay allowed: it keeps list semantics in Safari
  // when list-style is none.
  {
    rules: {
      "jsx-a11y/anchor-has-content": "error",
      "jsx-a11y/anchor-is-valid": "error",
      "jsx-a11y/aria-role": "error",
      "jsx-a11y/heading-has-content": "error",
      "jsx-a11y/html-has-lang": "error",
      "jsx-a11y/iframe-has-title": "error",
      "jsx-a11y/img-redundant-alt": "error",
      "jsx-a11y/interactive-supports-focus": "error",
      "jsx-a11y/no-access-key": "error",
      "jsx-a11y/no-distracting-elements": "error",
      "jsx-a11y/no-redundant-roles": ["error", { ul: ["list"], ol: ["list"] }],
      "jsx-a11y/role-supports-aria-props": "error",
      "jsx-a11y/scope": "error",
      "jsx-a11y/tabindex-no-positive": "error",
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
    // Local validation probes are gitignored scratch files, not app sources.
    "tmp/**",
    "next-env.d.ts",
    "next.config.js",
  ]),
]);

export default eslintConfig;
