---
name: "source-command-dashboard-cutover"
description: "Phase 3 — retire the embedded dashboard and move the health gate to :8081 (only after living with the new one)"
---

# source-command-dashboard-cutover

Use this skill when the user asks to run the migrated source command `dashboard-cutover`.

## Command Template

# Phase 3 — cutover

This drops `--web` from the engine and repoints `scripts/deploy.sh`'s health gate
at the standalone dashboard. After this the engine no longer serves HTTP.

## Preconditions — refuse if any fails. Ask me each one; do not assume.

1. **I have actually used the new dashboard for several days**, on desktop and
   phone, and told you to proceed. This is not a technical check — ask me.
2. Phase 2's engine-down test passed and was witnessed, not assumed.
3. `/dashboard-verify` is clean right now.
4. `betting-dashboard` has not restart-looped:
   ```bash
   ssh root@129.212.176.202 'systemctl show -p NRestarts -p MemoryPeak betting-dashboard'
   ```

## Steps

1. **Both edits go in ONE commit.** `scripts/betting-pod-shop.service` and
   `scripts/deploy.sh` must revert together or neither — a half-reverted cutover
   leaves the deploy gate pointing at a port nothing serves, which will make
   every future deploy look like a failure:
   - remove `--web --no-browser` from `ExecStart` in
     `scripts/betting-pod-shop.service`
   - repoint `HEALTH_URL` in `scripts/deploy.sh` from
     `http://localhost:8080/health` to `http://localhost:8081/healthz`
   - `/healthz` returns JSON, not the literal `ok`. **Check how `deploy.sh`
     asserts on the response** and adjust the assertion in the same commit if it
     greps for `ok`. Read the script before editing it.

2. Run the full suite. Some tests reference the 8080 default and the `--web`
   flag — those flags remain in `src/cli.py` for local dev, so the suite should
   still pass unchanged. If anything fails, the cutover is wrong, not the tests.

3. Deploy and verify the gate works on its new target:
   ```bash
   bash scripts/deploy.sh 129.212.176.202 restart
   ```
   Same rule as always: **N** at the prompt if there are any failures.

4. Confirm the engine is no longer listening on 8080 and the dashboard still
   answers:
   ```bash
   ssh root@129.212.176.202 'ss -ltnp | grep -E "8080|8081"; curl -s -o /dev/null -w "8081 -> %{http_code}\n" localhost:8081/healthz'
   ```

5. **Leave `src/templates/dashboard.html` in place for at least a week.** Deleting
   it is a separate change with no upside today, and it is the fallback if
   something about v2 turns out to be wrong.

## Rollback

One-line revert of the single commit, then redeploy:
```bash
git revert <commit> && bash scripts/deploy.sh 129.212.176.202 restart
```
Because both edits are in one commit, this cannot leave the gate mismatched.
