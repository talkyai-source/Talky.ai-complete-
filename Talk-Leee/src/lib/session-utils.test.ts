import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { api } from "./api";
import { logoutAllOtherSessions } from "./session-utils";

const oldList = api.getActiveSessions;
const oldRevoke = api.revokeSession;
afterEach(() => { api.getActiveSessions = oldList; api.revokeSession = oldRevoke; });
function sessions(ids: string[]) {
  return {
    sessions: ["current", ...ids].map(id => ({
      id, is_current: id === "current", ip_address: "127.0.0.1", user_agent: "Synthetic browser", device_info: {},
      created_at: "2026-10-05T00:00:00Z", last_active_at: "2026-10-05T00:00:00Z",
    })), total: ids.length + 1,
  } satisfies Awaited<ReturnType<typeof api.getActiveSessions>>;
}

test("revoking other sessions cannot report success when one outcome is unconfirmed", async () => {
  api.getActiveSessions = async () => sessions(["a", "b", "c"]);
  const calls: string[] = [];
  api.revokeSession = async id => { calls.push(id); if (id === "b") throw new Error("synthetic timeout after send"); return { detail: "Revoked" }; };
  await assert.rejects(logoutAllOtherSessions(), /1 of 3.*not confirmed.*2 confirmed/i);
  assert.deepEqual(calls.sort(), ["a", "b", "c"]);
});

test("aggregate success requires all requested revocations, and excludes current session", async () => {
  api.getActiveSessions = async () => sessions(["a", "b"]);
  const calls: string[] = [];
  api.revokeSession = async id => { calls.push(id); return { detail: "Revoked" }; };
  assert.deepEqual(await logoutAllOtherSessions(), { success: true });
  assert.deepEqual(calls.sort(), ["a", "b"]);
});

test("no other sessions needs no revoke; failed inventory cannot claim success", async () => {
  api.getActiveSessions = async () => sessions([]);
  let revokes = 0;
  api.revokeSession = async () => { revokes++; return { detail: "Revoked" }; };
  assert.deepEqual(await logoutAllOtherSessions(), { success: true });
  api.getActiveSessions = async () => { throw new Error("synthetic inventory unavailable"); };
  await assert.rejects(logoutAllOtherSessions(), /inventory unavailable/);
  assert.equal(revokes, 0);
});
