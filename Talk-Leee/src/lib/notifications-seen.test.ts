import { afterEach, beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import { NotificationsStore, notificationSeenKey, type NotificationIdentity } from "@/lib/notifications";

const alice = { tenantId: "tenant-a", userId: "alice" };
const bob = { tenantId: "tenant-b", userId: "bob" };
const instances: NotificationsStore[] = [];
function store(identity: NotificationIdentity | null = alice) {
    const result = new NotificationsStore(); instances.push(result); result.setIdentity(identity); return result;
}
beforeEach(() => { window.localStorage.clear(); });
afterEach(() => {
    for (const instance of instances.splice(0)) instance.dispose();
    window.localStorage.clear();
});

test("seen ids are null before the first mark, then remembered per account and source", () => {
    const instance = store();
    assert.equal(instance.readSeen("failedcall"), null);
    instance.markSeen("failedcall", []);
    assert.deepEqual([...instance.readSeen("failedcall")!], []);
    instance.markSeen("failedcall", ["c-1", "c-2"]);
    instance.markSeen("failedcall", ["c-2", "c-3"]);
    assert.deepEqual([...instance.readSeen("failedcall")!].sort(), ["c-1", "c-2", "c-3"]);
    assert.equal(instance.readSeen("lowminutes"), null);
    const key = notificationSeenKey(instance.getSnapshot().scopeKey!, "failedcall");
    assert.equal(key, `talklee.failedcall.seen.v2:${JSON.stringify(["tenant-a", "alice"])}`);
    assert.deepEqual(JSON.parse(window.localStorage.getItem(key)!).sort(), ["c-1", "c-2", "c-3"]);
});

test("unmarking removes an id; the record stays so a first run is not mistaken", () => {
    const instance = store();
    instance.markSeen("lowminutes", ["low"]);
    instance.unmarkSeen("lowminutes", ["low"]);
    assert.notEqual(instance.readSeen("lowminutes"), null);
    assert.equal(instance.readSeen("lowminutes")!.has("low"), false);
});

test("another tab's write is read before each check, and another account never shares ids", () => {
    const tabOne = store();
    const tabTwo = store();
    tabOne.markSeen("qlead", ["evt-1"]);
    assert.equal(tabTwo.readSeen("qlead")!.has("evt-1"), true);
    const other = store(bob);
    assert.equal(other.readSeen("qlead"), null);
    assert.equal(store(null).readSeen("qlead"), null);
});

test("clearing notification history keeps the seen ids, so nothing is replayed", () => {
    const instance = store();
    instance.markSeen("failedcall", ["c-1"]);
    instance.create({ type: "error", title: "Failed call" });
    instance.clearAll();
    assert.deepEqual(instance.getSnapshot().notifications, []);
    assert.equal(instance.readSeen("failedcall")!.has("c-1"), true);
});

test("the seen set is capped at 500 ids, keeping the newest", () => {
    const instance = store();
    instance.markSeen("qlead", Array.from({ length: 600 }, (_, i) => `evt-${i}`));
    const seen = instance.readSeen("qlead")!;
    assert.equal(seen.size, 500);
    assert.equal(seen.has("evt-599"), true);
    assert.equal(seen.has("evt-99"), false);
});
