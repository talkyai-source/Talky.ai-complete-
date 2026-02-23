# Cutover Checklist

## Pre-Cutover

- [ ] All environments have matching configs and secrets
- [ ] Synthetic calls green for 72h
- [ ] Transfer tests (blind + attended) pass
- [ ] DTMF and barge-in tests pass
- [ ] Recording integrity validated
- [ ] Tenant routing isolation tests pass
- [ ] Rollback toggle tested in staging

## Cutover Window

- [ ] Enable 5% traffic route
- [ ] Observe 30 min metrics
- [ ] Increase to 25%
- [ ] Observe 60 min metrics
- [ ] Increase to 50%
- [ ] Observe 2h metrics
- [ ] Increase to 100%

## Post-Cutover

- [ ] Keep legacy path as warm standby
- [ ] Monitor for 14 days
- [ ] Decommission legacy code by staged PRs
- [ ] Archive packet captures and incident notes
