# Load tests

These run with [k6](https://k6.io/). Install:

    # macOS
    brew install k6
    # Linux
    sudo gpg -k && sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
    echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
    sudo apt-get update && sudo apt-get install k6

## Tests

| File | Purpose | When to run |
|---|---|---|
| `smoke.js` | 30s, 1 VU — sanity check | Every deploy / nightly CI |
| `soak.js`  | 30 min @ 50 RPS — leak hunt | Pre-release; weekly against staging |

## Running

    BASE_URL=https://staging.example.com \
    AUTH_TOKEN=eyJ... \
    k6 run smoke.js

## Reading results

The threshold lines at the top of each script define pass/fail. If
`http_req_failed`, `http_req_duration`, or `errors` thresholds breach,
k6 exits non-zero and you've got a regression.

## ⚠️ Never against production

Soak runs hit ~50 RPS sustained. Run them against staging only, or a prod
clone. Production runs need explicit coordination with on-call.
