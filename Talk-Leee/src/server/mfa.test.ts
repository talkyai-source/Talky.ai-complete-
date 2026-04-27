import { test } from "node:test";
import assert from "node:assert/strict";
import { totpCodeAtTime, verifyTotpCode } from "@/server/mfa";

test("RFC 6238 SHA1 test vectors match", () => {
    const prevDigits = process.env.MFA_TOTP_DIGITS;
    const prevPeriod = process.env.MFA_TOTP_PERIOD_SECONDS;
    const prevAlgo = process.env.MFA_TOTP_ALGORITHM;

    process.env.MFA_TOTP_DIGITS = "8";
    process.env.MFA_TOTP_PERIOD_SECONDS = "30";
    process.env.MFA_TOTP_ALGORITHM = "SHA1";

    try {
        const secretBase32 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ";
        const cases: Array<{ t: number; code: string }> = [
            { t: 59, code: "94287082" },
            { t: 1111111109, code: "07081804" },
            { t: 1111111111, code: "14050471" },
            { t: 1234567890, code: "89005924" },
            { t: 2000000000, code: "69279037" },
            { t: 20000000000, code: "65353130" },
        ];

        for (const c of cases) {
            const nowMs = c.t * 1000;
            const generated = totpCodeAtTime({ secretBase32, nowMs });
            assert.equal(generated, c.code);
            assert.equal(verifyTotpCode({ secretBase32, nowMs, code: c.code, window: 0 }).ok, true);
        }
    } finally {
        if (prevDigits === undefined) delete process.env.MFA_TOTP_DIGITS;
        else process.env.MFA_TOTP_DIGITS = prevDigits;
        if (prevPeriod === undefined) delete process.env.MFA_TOTP_PERIOD_SECONDS;
        else process.env.MFA_TOTP_PERIOD_SECONDS = prevPeriod;
        if (prevAlgo === undefined) delete process.env.MFA_TOTP_ALGORITHM;
        else process.env.MFA_TOTP_ALGORITHM = prevAlgo;
    }
});

