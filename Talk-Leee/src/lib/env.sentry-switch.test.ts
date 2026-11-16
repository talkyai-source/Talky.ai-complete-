/**
 * FX7b Task 1.1 — NEXT_PUBLIC_SENTRY_ENABLED: "false" and "0" (any letter
 * case, spaces trimmed) mean off. Every other value, and unset, is as before.
 * Each "on" value is read where the default is off (development) and each
 * "off" value where the default is on (production), so the value decides.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

const KEYS = ["NEXT_PUBLIC_SENTRY_ENABLED", "NEXT_PUBLIC_APP_ENV", "VERCEL_ENV"] as const;
const saved = Object.fromEntries(KEYS.map((key) => [key, process.env[key]]));

function sentryEnabledWith(value: string | undefined, appEnv: "development" | "production"): boolean {
    if (value === undefined) delete process.env.NEXT_PUBLIC_SENTRY_ENABLED;
    else process.env.NEXT_PUBLIC_SENTRY_ENABLED = value;
    process.env.NEXT_PUBLIC_APP_ENV = appEnv;
    delete process.env.VERCEL_ENV;
    const id = require.resolve("./env");
    delete require.cache[id];
    try {
        // eslint-disable-next-line @typescript-eslint/no-require-imports -- the module reads the environment once, when loaded
        return (require("./env") as typeof import("./env")).sentryEnabled();
    } finally {
        delete require.cache[id];
    }
}

afterEach(() => {
    for (const key of KEYS) {
        if (saved[key] === undefined) delete process.env[key];
        else process.env[key] = saved[key];
    }
});

for (const value of ["true", "1"]) {
    test(`NEXT_PUBLIC_SENTRY_ENABLED=${JSON.stringify(value)} is on, also where the default is off`, () => {
        assert.equal(sentryEnabledWith(value, "development"), true);
        assert.equal(sentryEnabledWith(value, "production"), true);
    });
}

for (const value of ["false", "FALSE", " 0 ", "0", " False ", ""]) {
    test(`NEXT_PUBLIC_SENTRY_ENABLED=${JSON.stringify(value)} is off, also where the default is on`, () => {
        assert.equal(sentryEnabledWith(value, "production"), false);
        assert.equal(sentryEnabledWith(value, "development"), false);
    });
}

test("NEXT_PUBLIC_SENTRY_ENABLED unset: on outside development, off in development", () => {
    assert.equal(sentryEnabledWith(undefined, "production"), true);
    assert.equal(sentryEnabledWith(undefined, "development"), false);
});

test("any other text is on, as before", () => {
    assert.equal(sentryEnabledWith("no", "development"), true);
});
