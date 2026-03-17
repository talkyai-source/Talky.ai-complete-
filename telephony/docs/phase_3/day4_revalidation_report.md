# Day 4 Re-Validation Report

Date: 2026-03-02

## Scope

1. Repeated Day 4 verifier runs (3x).
2. RTP pacing/sequence/timestamp acceptance gates.
3. API contract checks (start/duplicate/invalid codec/invalid ptime/invalid port/stop idempotency).

## Multi-Run Results

| Run | Packets | Seq | Ts | p95 (ms) | Max (ms) | Acceptance |
|---|---:|---|---|---:|---:|---|
| 1 | 64/64 | True | True | 20.14 | 20.21 | True |
| 2 | 64/64 | True | True | 20.108 | 20.128 | True |
| 3 | 64/64 | True | True | 20.365 | 21.657 | True |

## API Contract Checks

1. `POST /v1/sessions/start` valid -> `200 started`
2. Duplicate start -> `409 already_exists`
3. Invalid codec (`opus`) -> `400`
4. Invalid ptime (`40`) -> `400`
5. Invalid port (`99999`) -> `400`
6. `GET /v1/sessions/{id}/stats` -> `200`
7. `POST /v1/sessions/stop` first -> `200 stopped`
8. `POST /v1/sessions/stop` second -> `200 already_stopped`
9. `GET /stats` -> `200`

## Verdict

Day 4 is stable and passes acceptance criteria under repeated execution and API contract testing.
