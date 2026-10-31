import assert from "node:assert/strict";
import { test } from "node:test";
import { describeAddress, formatAddressForEdit, formatServer, parseProviderAddress, resolveAddress } from "./pbx-address";

function parsed(input: string) {
    const result = parseProviderAddress(input);
    assert.equal(result.ok, true, `expected "${input}" to parse`);
    if (!result.ok) throw new Error("unreachable");
    return result.value;
}

function rejected(input: string) {
    const result = parseProviderAddress(input);
    assert.equal(result.ok, false, `expected "${input}" to be rejected`);
    if (result.ok) throw new Error("unreachable");
    return result.reason;
}

test("a plain host states no port or transport, and resolves to port 5060 over UDP", () => {
    const value = parsed("sip3.example.com");
    assert.deepEqual(value, { host: "sip3.example.com", port: null, transport: null });
    const resolved = resolveAddress(value);
    assert.deepEqual(resolved, { host: "sip3.example.com", port: 5060, transport: "udp" });
    assert.equal(describeAddress(resolved), "sip3.example.com · port 5060");
});

test("host:port keeps the port the person wrote", () => {
    assert.deepEqual(parsed("sip3.example.com:5080"), { host: "sip3.example.com", port: 5080, transport: null });
});

test("IPv4 addresses work with and without a port", () => {
    assert.deepEqual(parsed("203.0.113.10"), { host: "203.0.113.10", port: null, transport: null });
    assert.deepEqual(parsed("203.0.113.10:5070"), { host: "203.0.113.10", port: 5070, transport: null });
});

test("a SIP address with a port and ;transport=tcp is split into host, port and transport", () => {
    const value = parsed("sip:sip3.example.com:5080;transport=tcp");
    assert.deepEqual(value, { host: "sip3.example.com", port: 5080, transport: "tcp" });
    assert.equal(describeAddress(resolveAddress(value)), "sip3.example.com · port 5080 · TCP");
});

test("TLS without a port resolves to 5061, whether it comes from sips: or ;transport=tls", () => {
    for (const input of ["sips:pbx.example.com", "pbx.example.com;transport=tls", "tls://pbx.example.com"]) {
        const resolved = resolveAddress(parsed(input));
        assert.deepEqual(resolved, { host: "pbx.example.com", port: 5061, transport: "tls" }, input);
    }
    // An explicit port is never replaced by the TLS default.
    assert.equal(resolveAddress(parsed("sips:pbx.example.com:5999")).port, 5999);
});

test("an explicit ;transport= overrides the scheme", () => {
    assert.equal(parsed("sips:pbx.example.com;transport=tcp").transport, "tcp");
});

test("user@, headers, path and letter case are not part of the server address", () => {
    assert.deepEqual(parsed("SIP:Alice@PBX.Example.COM:5060?x=y"), { host: "pbx.example.com", port: 5060, transport: null });
    assert.equal(parsed("  <sip:pbx.example.com>  ").host, "pbx.example.com");
    assert.equal(parsed("pbx.example.com/").host, "pbx.example.com");
    assert.equal(parsed("pbx.example.com.").host, "pbx.example.com");
});

test("an address copied from a saved line (UDP://host:port) is understood", () => {
    assert.deepEqual(parsed("UDP://sip.example.com:5060"), { host: "sip.example.com", port: 5060, transport: "udp" });
});

test("IPv6 addresses work in brackets with a port, and bare without one", () => {
    assert.deepEqual(parsed("[2001:db8::1]:5080"), { host: "2001:db8::1", port: 5080, transport: null });
    assert.deepEqual(parsed("2001:db8::1"), { host: "2001:db8::1", port: null, transport: null });
});

test("input that is not an address is rejected with a plain reason", () => {
    assert.match(rejected(""), /Enter the address/);
    assert.match(rejected("   "), /Enter the address/);
    assert.match(rejected("not a host!"), /valid address/);
    assert.match(rejected("https://pbx.example.com"), /valid address/);
    assert.match(rejected("-bad-.example.com"), /valid address/);
    assert.match(rejected("999.1.1.1"), /valid address/);
    assert.match(rejected("pbx.example.com:99999"), /port/);
    assert.match(rejected("pbx.example.com:0"), /port/);
    assert.match(rejected("pbx.example.com:abc"), /port/);
    assert.match(rejected("pbx.example.com;transport=ws"), /isn't supported/);
});

test("Advanced overrides win over what the address says", () => {
    const value = parsed("pbx.example.com:5080;transport=tcp");
    assert.deepEqual(resolveAddress(value, { transport: "tls", port: 5999 }), { host: "pbx.example.com", port: 5999, transport: "tls" });
    // null means "not set": the address and the defaults still apply.
    assert.deepEqual(resolveAddress(value, { transport: null, port: null }), { host: "pbx.example.com", port: 5080, transport: "tcp" });
    // Choosing TLS alone moves the default port with it.
    assert.equal(resolveAddress(parsed("pbx.example.com"), { transport: "tls" }).port, 5061);
});

test("a saved server is shown as host:port, with the connection type only when it is not UDP", () => {
    assert.equal(formatServer({ host: "sip.example.com", port: 5060, transport: "udp" }), "sip.example.com:5060");
    assert.equal(formatServer({ host: "sip.example.com", port: 5061, transport: "tls" }), "sip.example.com:5061 · TLS");
    assert.equal(formatServer({ host: "203.0.113.10", port: 5080, transport: "tcp" }), "203.0.113.10:5080 · TCP");
    assert.equal(formatServer({ host: "2001:db8::1", port: 5060, transport: "udp" }), "[2001:db8::1]:5060");
});

test("the Edit form shows a saved line the way it would be typed, and it parses back to the same line", () => {
    assert.equal(formatAddressForEdit("pbx.example.com", 5060, "udp"), "pbx.example.com");
    assert.equal(formatAddressForEdit("pbx.example.com", 5061, "tls"), "pbx.example.com");
    assert.equal(formatAddressForEdit("pbx.example.com", 5061, "udp"), "pbx.example.com:5061");
    assert.equal(formatAddressForEdit("2001:db8::1", 5080, "udp"), "[2001:db8::1]:5080");
    const cases: Array<[string, number, "udp" | "tcp" | "tls"]> = [
        ["pbx.example.com", 5060, "udp"],
        ["pbx.example.com", 5080, "tcp"],
        ["203.0.113.10", 5061, "tls"],
        ["2001:db8::1", 5080, "udp"],
    ];
    for (const [host, port, transport] of cases) {
        const resolved = resolveAddress(parsed(formatAddressForEdit(host, port, transport)), { transport });
        assert.deepEqual(resolved, { host, port, transport });
    }
});
