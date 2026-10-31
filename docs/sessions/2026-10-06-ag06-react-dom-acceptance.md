# AG06 real React lifecycle qualification

Source commit: `e5a5b0e89c1ed33ff2def2b95bb4bb2ac2a83b2b`, based on `db99f652`.

Real React DOM tests reproduced three authentication races: a late initial verification restored the previous admin after logout; an older rejected verification removed a newer login token; and a delayed logout completion removed a newer login. Each asynchronous completion previously wrote state or tokens without checking which operation currently owned the session.

The bounded fix adds an operation counter to the existing AuthProvider, checks the existing API authentication generation after asynchronous responses, and invalidates pending operations when the provider unmounts. Backend contracts, recovery outcomes and authentication policy are unchanged.

The same 12 controls changed from **9 passing / 3 failing** to **12 passing**. The full Admin suite passed **171 tests**, including those 12. TypeScript and the production build passed. Scoped authentication lint finished with zero errors and warnings. Both root and independent source reviews cleared the change. Exact commands, logs and hashes are recorded in [evidence.json](artifacts/ag06-react-dom/evidence.json).

The tests use real React 19.2.3, StrictMode, effects, native DOM events and the complete parent drawer refresh, with JSDOM and synthetic API responses. They cover uncertain recovery reopening, exact-request replay, stale selection/session responses, another review's saved recovery and the three reproduced authentication races. No hooks are replaced. They do **not** establish browser, cookie, real server, provider or customer acceptance.

Admin CI installs its own dependencies, so exact `jsdom@25.0.1` is declared as an Admin development dependency. The lock adds 57 development-only entries without changing existing dependency entries. A fresh fixture installed 239 packages from the exact lock with install scripts disabled; bounded public npm fetches were authorized after the offline cache proved incomplete. The initial offline installation failure is retained separately from the test baseline.

Execution used `tmp/ag06-admin-dom-validation-online-20261006`. Baseline source/dependency/test parity covered 61 files; final source/config/dependency/test parity covered 67. Tests and build qualified authentication hash `ced0d006cf3ef911be9c06cd5ecc0a2fe4f0c8e6b433399281bc730c2934d4e2`. The only subsequent change was a documented lint exemption for intentional mount-only verification, producing final hash `13e01bf1054a395a242eec1bbc0e7e8f8a0aa595947a701e1a68ac3c130412fb`. Root authorized rerunning scoped lint only for that comment-only change; both input hashes are preserved.

Automatic approval review rejected removal of the temporary dependency junction. It remains untouched, and no alternative unlink or deletion was attempted. Validation used a separate fresh fixture. No browser, preview or application server was launched, and no provider or database action was performed. Broader production and customer acceptance gates remain open.
