import { test } from "node:test";
import assert from "node:assert/strict";
import { webAuthnConfigForRequest } from "@/server/passkeys";

function withEnv<T>(vars: Record<string, string | undefined>, fn: () => T) {
    const prev: Record<string, string | undefined> = {};
    for (const [k, v] of Object.entries(vars)) {
        prev[k] = process.env[k];
        if (v === undefined) delete process.env[k];
        else process.env[k] = v;
    }
    try {
        return fn();
    } finally {
        for (const [k, v] of Object.entries(prev)) {
            if (v === undefined) delete process.env[k];
            else process.env[k] = v;
        }
    }
}

test("webauthn config falls back to request host and origin in non-production", () => {
    withEnv(
        {
            NODE_ENV: "development",
            WEBAUTHN_RP_ID: undefined,
            WEBAUTHN_ALLOWED_ORIGINS: undefined,
        },
        () => {
            const req = new Request("http://localhost:3000/api/v1/auth/passkeys/login/options", {
                headers: { origin: "http://localhost:3000" },
            });
            const cfg = webAuthnConfigForRequest(req);
            assert.equal(cfg.rpId, "localhost");
            assert.deepEqual(cfg.expectedOrigins, ["http://localhost:3000"]);
            assert.ok(typeof cfg.rpName === "string" && cfg.rpName.length > 0);
        }
    );
});

test("webauthn config requires rp id and allowed origins in production", () => {
    withEnv(
        {
            NODE_ENV: "production",
            WEBAUTHN_RP_ID: undefined,
            WEBAUTHN_ALLOWED_ORIGINS: undefined,
        },
        () => {
            const req = new Request("https://example.com/api/v1/auth/passkeys/login/options", {
                headers: { origin: "https://example.com" },
            });
            assert.throws(() => webAuthnConfigForRequest(req), /WEBAUTHN_RP_ID|WEBAUTHN_ALLOWED_ORIGINS/);
        }
    );
});

test("webauthn config uses explicit allowlist when configured", () => {
    withEnv(
        {
            NODE_ENV: "production",
            WEBAUTHN_RP_ID: "example.com",
            WEBAUTHN_ALLOWED_ORIGINS: "https://app.example.com, https://admin.example.com",
        },
        () => {
            const req = new Request("https://app.example.com/api/v1/auth/passkeys/login/options", {
                headers: { origin: "https://app.example.com" },
            });
            const cfg = webAuthnConfigForRequest(req);
            assert.equal(cfg.rpId, "example.com");
            assert.deepEqual(cfg.expectedOrigins, ["https://app.example.com", "https://admin.example.com"]);
        }
    );
});
