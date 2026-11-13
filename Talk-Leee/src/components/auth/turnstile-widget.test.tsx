import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { TurnstileWidget, turnstileSiteKey, useTurnstile } from "./turnstile-widget";

// Cloudflare's published always-pass test site key (not a secret).
const SITE_KEY = "1x00000000000000000000AA";
const SCRIPT = 'script[src*="challenges.cloudflare.com"]';
const oldKey = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY;

type Options = {
  sitekey: string;
  action: string;
  callback: (token: string) => void;
  "expired-callback": () => void;
  "error-callback": () => void;
};
let drawn: Array<{ id: string; options: Options }>;
let removed: string[];

const page = window as unknown as { turnstile?: unknown };
// Stands in for Cloudflare's api.js: records what the page asks it to draw.
function installApi() {
  page.turnstile = {
    render(_container: HTMLElement, options: Options) {
      const id = `widget-${drawn.length + 1}`;
      drawn.push({ id, options });
      return id;
    },
    remove(id: string) { removed.push(id); },
  };
}

beforeEach(() => { drawn = []; removed = []; });
afterEach(() => {
  cleanup();
  document.querySelectorAll(SCRIPT).forEach((script) => script.remove());
  delete page.turnstile;
  if (oldKey === undefined) delete process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY;
  else process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY = oldKey;
});

test("without a site key nothing is drawn, no script is added and no token is offered", () => {
  delete process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY;
  installApi();
  const seen: Array<string | null> = [];
  const { container } = render(<TurnstileWidget action="login" onToken={(token) => seen.push(token)} />);
  assert.equal(container.innerHTML, "");
  assert.equal(document.querySelectorAll(SCRIPT).length, 0);
  assert.deepEqual(drawn, []);
  assert.deepEqual(seen, []);
});

test("a blank site key counts as no site key", () => {
  process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY = "   ";
  assert.equal(turnstileSiteKey(), undefined);
  const { container } = render(<TurnstileWidget action="login" onToken={() => {}} />);
  assert.equal(container.innerHTML, "");
  assert.equal(document.querySelectorAll(SCRIPT).length, 0);
});

test("with a site key the explicit-render script is added once and each widget is drawn for its form's action once it loads", () => {
  process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY = SITE_KEY;
  render(<><TurnstileWidget action="signup" onToken={() => {}} /><TurnstileWidget action="signup" onToken={() => {}} /></>);
  const scripts = document.querySelectorAll(SCRIPT);
  assert.equal(scripts.length, 1);
  assert.equal(scripts[0].getAttribute("src"), "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit");
  assert.equal(drawn.length, 0, "nothing can be drawn before the API has loaded");
  installApi();
  act(() => { scripts[0].dispatchEvent(new Event("load")); });
  assert.equal(drawn.length, 2);
  for (const widget of drawn) {
    assert.equal(widget.options.sitekey, SITE_KEY);
    assert.equal(widget.options.action, "signup");
  }
});

test("with the API already on the page the widget is drawn at once and no script is added", () => {
  process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY = SITE_KEY;
  installApi();
  render(<TurnstileWidget action="login" onToken={() => {}} />);
  assert.equal(drawn.length, 1);
  assert.equal(document.querySelectorAll(SCRIPT).length, 0);
});

test("the solved token reaches onToken, and expiry or an error withdraws it", () => {
  process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY = SITE_KEY;
  installApi();
  const seen: Array<string | null> = [];
  render(<TurnstileWidget action="login" onToken={(token) => seen.push(token)} />);
  const { options } = drawn[0];
  act(() => options.callback("token-1"));
  act(() => options["expired-callback"]());
  act(() => options.callback("token-2"));
  act(() => options["error-callback"]());
  assert.deepEqual(seen, ["token-1", null, "token-2", null]);
});

test("a parent re-render with a new callback does not rebuild the widget and throw its solved challenge away", () => {
  process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY = SITE_KEY;
  installApi();
  const first: Array<string | null> = [];
  const second: Array<string | null> = [];
  const view = render(<TurnstileWidget action="login" onToken={(token) => first.push(token)} />);
  view.rerender(<TurnstileWidget action="login" onToken={(token) => second.push(token)} />);
  assert.equal(drawn.length, 1);
  assert.deepEqual(removed, []);
  act(() => drawn[0].options.callback("token-1"));
  assert.deepEqual(first, []);
  assert.deepEqual(second, ["token-1"]);
});

test("unmounting removes the widget, withdraws its token and ignores a late callback", () => {
  process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY = SITE_KEY;
  installApi();
  const seen: Array<string | null> = [];
  const view = render(<TurnstileWidget action="login" onToken={(token) => seen.push(token)} />);
  act(() => drawn[0].options.callback("token-1"));
  view.unmount();
  assert.deepEqual(removed, ["widget-1"]);
  assert.deepEqual(seen, ["token-1", null]);
  act(() => drawn[0].options.callback("late"));
  assert.deepEqual(seen, ["token-1", null]);
});

type Held = { promise: Promise<void>; resolve: () => void; reject: (error: unknown) => void };
function deferred(): Held {
  let resolve!: () => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<void>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

// A form that sends one request, held open until the test settles it.
function Probe({ sent, requests }: { sent: Array<string | undefined>; requests: Held[] }) {
  const turnstile = useTurnstile("forgot_password");
  return (
    <form>
      {turnstile.widget}
      <output data-testid="state">{turnstile.pending ? "pending" : "ready"}</output>
      <button
        type="button"
        onClick={() => void turnstile.run((token) => {
          sent.push(token);
          const request = deferred();
          requests.push(request);
          return request.promise;
        }).catch(() => undefined)}
      >send</button>
    </form>
  );
}
const state = () => screen.getByTestId("state").textContent;
const send = () => fireEvent.click(screen.getByRole("button", { name: "send" }));
const settle = (request: Held, outcome: "ok" | "fail") =>
  act(async () => { if (outcome === "ok") request.resolve(); else request.reject(new Error("refused")); await request.promise.catch(() => undefined); });

test("useTurnstile is inert without a site key: never pending, no widget, the request carries no token", async () => {
  delete process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY;
  installApi();
  const sent: Array<string | undefined> = [];
  const requests: Held[] = [];
  render(<Probe sent={sent} requests={requests} />);
  assert.equal(state(), "ready");
  send();
  assert.deepEqual(sent, [undefined]);
  await settle(requests[0], "ok");
  assert.equal(state(), "ready");
  assert.deepEqual(drawn, []);
  assert.equal(document.querySelectorAll(SCRIPT).length, 0);
});

test("useTurnstile waits for a solved challenge, spends the token on one request, then starts a fresh challenge once it settles", async () => {
  process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY = SITE_KEY;
  installApi();
  const sent: Array<string | undefined> = [];
  const requests: Held[] = [];
  render(<Probe sent={sent} requests={requests} />);
  assert.equal(drawn[0].options.action, "forgot_password");
  assert.equal(state(), "pending");

  act(() => drawn[0].options.callback("token-1"));
  assert.equal(state(), "ready");
  send();
  assert.deepEqual(sent, ["token-1"]);
  assert.equal(state(), "pending", "the spent token is not offered again");
  assert.equal(drawn.length, 1, "the same widget stays while the request is in flight");

  await settle(requests[0], "fail");
  assert.equal(drawn.length, 2, "a fresh challenge starts once the request has settled, even if it failed");
  assert.deepEqual(removed, ["widget-1"]);
  act(() => drawn[1].options.callback("token-2"));
  send();
  assert.deepEqual(sent, ["token-1", "token-2"]);
  await settle(requests[1], "ok");
  assert.equal(drawn.length, 3);
});

test("useTurnstile sends no token, and leaves the widget alone, when the challenge is not solved yet", async () => {
  process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY = SITE_KEY;
  installApi();
  const sent: Array<string | undefined> = [];
  const requests: Held[] = [];
  render(<Probe sent={sent} requests={requests} />);
  send();
  assert.deepEqual(sent, [undefined]);
  await settle(requests[0], "ok");
  assert.equal(drawn.length, 1, "a challenge still being solved is not restarted");
  assert.deepEqual(removed, []);
});
