import { describe, it, afterEach } from "node:test";
import assert from "node:assert";
import { GET } from "./route";

const rates = {
  result: "success",
  base_code: "USD",
  time_last_update_unix: 1794528151,
  rates: { USD: 1, GBP: 0.755149, EUR: 0.903491, PKR: 276.622899 },
};
const from = (country?: string, query = "") =>
  new Request(`https://talkleeai.com/api/display-currency${query}`, {
    headers: country === undefined ? {} : { "x-vercel-ip-country": country },
  });

describe("GET /api/display-currency", () => {
  const originalFetch = globalThis.fetch;
  let calls: string[] = [];
  const answerWith = (body: unknown, status = 200) => {
    calls = [];
    globalThis.fetch = async (input) => { calls.push(String(input)); return Response.json(body, { status }); };
  };
  afterEach(() => { globalThis.fetch = originalFetch; });

  it("answers with the currency and rate for the visitor's country", async () => {
    answerWith(rates);
    const response = await GET(from("GB"));
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { currency: "GBP", rate: 0.755149, as_of: "2026-11-13" });
    assert.equal(response.headers.get("cache-control"), "private, max-age=3600");
    assert.deepEqual(calls, ["https://open.er-api.com/v6/latest/USD"]);
  });

  it("sends nothing about the visitor to the rate source", async () => {
    let init: RequestInit | undefined;
    globalThis.fetch = async (_input, options) => { init = options; return Response.json(rates); };
    await GET(new Request("https://talkleeai.com/api/display-currency", {
      headers: { "x-vercel-ip-country": "PK", "x-forwarded-for": "203.0.113.9", cookie: "talky_at=secret" },
    }));
    assert.equal(init?.headers, undefined);
    assert.equal(init?.body, undefined);
    assert.equal(init?.method, undefined);
  });

  it("says dollars only, without asking for rates, for the US, an unlisted country or no country", async () => {
    for (const country of ["US", "ZZ", "", undefined]) {
      answerWith(rates);
      const response = await GET(from(country));
      assert.deepEqual(await response.json(), { currency: null }, String(country));
      assert.equal(response.headers.get("cache-control"), "private, no-store");
      assert.equal(calls.length, 0);
    }
  });

  it("says dollars only when the rate source fails or answers oddly", async () => {
    const broken: [unknown, number][] = [
      [{ error: "too many requests" }, 429],
      [{ ...rates, result: "error" }, 200],
      [{ ...rates, base_code: "EUR" }, 200],
      [{ ...rates, rates: { USD: 1 } }, 200],
      [{ ...rates, rates: { GBP: 0 } }, 200],
      [{ ...rates, rates: { GBP: -3 } }, 200],
      ["not json at all", 200],
    ];
    for (const [body, status] of broken) {
      answerWith(body, status);
      const response = await GET(from("GB"));
      assert.equal(response.status, 200);
      assert.deepEqual(await response.json(), { currency: null }, JSON.stringify(body));
    }
    globalThis.fetch = async () => { throw new Error("network down"); };
    assert.deepEqual(await (await GET(from("GB"))).json(), { currency: null });
  });

  it("lets the team preview another country with ?country=", async () => {
    answerWith(rates);
    assert.equal((await (await GET(from("GB", "?country=pk"))).json()).currency, "PKR");
    assert.equal((await (await GET(from(undefined, "?country=DE"))).json()).currency, "EUR");
    assert.deepEqual(await (await GET(from("GB", "?country=US"))).json(), { currency: null });
    assert.deepEqual(await (await GET(from("GB", "?country=<script>"))).json(), { currency: null });
  });
});
