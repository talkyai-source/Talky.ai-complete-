import assert from "node:assert/strict";
import { test } from "node:test";
import { isPublicEnvKey, publicAppConfig } from "@/lib/env";

test("isPublicEnvKey only allows NEXT_PUBLIC client-safe keys", () => {
    assert.equal(isPublicEnvKey("NEXT_PUBLIC_API_BASE_URL"), true);
    assert.equal(isPublicEnvKey("NEXT_PUBLIC_SENTRY_ENABLED"), true);
    assert.equal(isPublicEnvKey("DATABASE_URL"), false);
    assert.equal(isPublicEnvKey("API_SECRET"), false);
});

test("publicAppConfig exposes only public configuration metadata", () => {
    const config = publicAppConfig();

    assert.ok(config.apiBaseUrl.length > 0);
    assert.ok(["development", "staging", "production"].includes(config.appEnvironment));
    assert.ok(config.publicEnvKeys.every((key) => key.startsWith("NEXT_PUBLIC_")));
    assert.equal(typeof config.sentry.enabled, "boolean");
});
