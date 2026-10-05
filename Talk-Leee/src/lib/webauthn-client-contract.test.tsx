import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "./api";
import { createWebAuthnCredential, getWebAuthnCredential } from "./webauthn-utils";
import PasskeyLogin from "@/components/auth/passkey-login";

const begin = api.beginPasskeyLogin, complete = api.completePasskeyLogin;
const credentialsDescriptor = Object.getOwnPropertyDescriptor(navigator, "credentials");
const windowCredentialDescriptor = Object.getOwnPropertyDescriptor(window, "PublicKeyCredential");
const globalCredentialDescriptor = Object.getOwnPropertyDescriptor(globalThis, "PublicKeyCredential");
class SyntheticCredential {
    id = "synthetic-key"; rawId = new Uint8Array([251, 255]).buffer; type = "public-key";
    response = { clientDataJSON: new Uint8Array([1]).buffer, authenticatorData: new Uint8Array([2]).buffer,
        signature: new Uint8Array([3]).buffer, userHandle: null, attestationObject: new Uint8Array([4]).buffer };
}
function installCredentials() {
    const seen: Array<CredentialRequestOptions | CredentialCreationOptions> = [];
    Object.defineProperty(window, "PublicKeyCredential", { configurable: true, value: SyntheticCredential });
    Object.defineProperty(globalThis, "PublicKeyCredential", { configurable: true, value: SyntheticCredential });
    Object.defineProperty(navigator, "credentials", { configurable: true, value: {
        get: async (options: CredentialRequestOptions) => { seen.push(options); return new SyntheticCredential(); },
        create: async (options: CredentialCreationOptions) => { seen.push(options); return new SyntheticCredential(); },
    } });
    return seen;
}
function restore(object: object, name: string, descriptor?: PropertyDescriptor) {
    if (descriptor) Object.defineProperty(object, name, descriptor); else Reflect.deleteProperty(object, name);
}
afterEach(() => {
    cleanup(); api.beginPasskeyLogin = begin; api.completePasskeyLogin = complete;
    restore(navigator, "credentials", credentialsDescriptor);
    restore(window, "PublicKeyCredential", windowCredentialDescriptor);
    restore(globalThis, "PublicKeyCredential", globalCredentialDescriptor);
});
const requestOptions = { challenge: "AQI", rpId: "login.example.com", timeout: 42000,
    userVerification: "required", allowCredentials: [{ type: "public-key", id: "-_8", transports: ["internal"] }] };

test("passkey login preserves the backend RP, allowed key, timeout and required verification", async () => {
    const seen = installCredentials(); let success = 0; let ceremony: string | undefined;
    api.beginPasskeyLogin = async () => ({ ceremony_id: "bound-ceremony", options: requestOptions, has_passkeys: true });
    api.completePasskeyLogin = async id => {
        ceremony = id;
        return { access_token: "synthetic", token_type: "bearer", user_id: "user-a", tenant_id: "tenant-a",
            email: "a@example.com", role: "tenant_admin", minutes_remaining: 1, message: "", mfa_required: false,
            mfa_challenge_token: undefined, business_name: undefined };
    };
    render(<PasskeyLogin onSuccess={() => { success++; }} />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => assert.equal(success, 1));
    const options = (seen[0] as CredentialRequestOptions).publicKey!;
    assert.equal(options.rpId, "login.example.com"); assert.equal(options.timeout, 42000);
    assert.equal(options.userVerification, "required");
    assert.deepEqual(new Uint8Array(options.allowCredentials![0].id as ArrayBuffer), new Uint8Array([251, 255]));
    assert.deepEqual(options.allowCredentials![0].transports, ["internal"]);
    assert.equal(ceremony, "bound-ceremony");
});

test("request-option decoder passes credential IDs as bytes without mutating the server record", async () => {
    const seen = installCredentials();
    await getWebAuthnCredential(requestOptions as unknown as Parameters<typeof getWebAuthnCredential>[0]);
    const options = (seen[0] as CredentialRequestOptions).publicKey!;
    assert.deepEqual(new Uint8Array(options.allowCredentials![0].id as ArrayBuffer), new Uint8Array([251, 255]));
    assert.equal(requestOptions.allowCredentials[0].id, "-_8");
});

test("registration decodes excluded existing keys and preserves server requirements", async () => {
    const seen = installCredentials();
    const input = { challenge: "AQI", rp: { id: "example.com", name: "Talky" },
        user: { id: "AwQ", name: "a@example.com", displayName: "A" },
        pubKeyCredParams: [{ type: "public-key", alg: -7 }],
        excludeCredentials: [{ type: "public-key", id: "-_8", transports: ["usb"] }],
        authenticatorSelection: { userVerification: "required", residentKey: "required" }, timeout: 45000 };
    await createWebAuthnCredential(input as unknown as Parameters<typeof createWebAuthnCredential>[0]);
    const options = (seen[0] as CredentialCreationOptions).publicKey!;
    assert.deepEqual(new Uint8Array(options.excludeCredentials![0].id as ArrayBuffer), new Uint8Array([251, 255]));
    assert.deepEqual(new Uint8Array(options.user.id as ArrayBuffer), new Uint8Array([3, 4]));
    assert.equal(options.authenticatorSelection?.userVerification, "required");
    assert.equal(options.timeout, 45000);
});
