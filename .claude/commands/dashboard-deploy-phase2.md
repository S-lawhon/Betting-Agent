---
description: "Phase 2 — start the standalone dashboard on :8081 alongside the old one, then prove it survives the engine dying"
---

# Phase 2 — the standalone dashboard

This starts `betting-dashboard.service` on `127.0.0.1:8081`. The engine keeps
serving the old dashboard on 8080 throughout, so this is additive and reversible.

## Preconditions — refuse if any fails

1. Phases 0 and 1 are done. `/dashboard-verify` shows `engine_state.json` fresh
   and `rollup.json` present.
2. Confirm with me before touching Caddy — that is the TLS front door for
   `dashboard.htxtrades.org` and its config has **never been in git**.

## Steps

1. **Install and start the unit:**
   ```bash
   scp scripts/betting-dashboard.service root@129.212.176.202:/etc/systemd/system/
   ssh root@129.212.176.202 'systemctl daemon-reload && systemctl enable --now betting-dashboard && sleep 3 && systemctl status betting-dashboard --no-pager | head -14'
   ```

2. **Check it from the inside before exposing it:**
   ```bash
   ssh root@129.212.176.202 'curl -s localhost:8081/healthz' | python3 -m json.tool
   ssh root@129.212.176.202 'curl -s -o /dev/null -w "%{http_code}\n" localhost:8081/health'
   ```
   Every source should read `available: true`. Remember `/healthz` returns 200
   even when a source is stale — read the `sources` block, do not trust the code.

3. **SAVE THE EXISTING CADDY CONFIG FIRST.** It exists only on that box:
   ```bash
   ssh root@129.212.176.202 'cp -n /etc/caddy/Caddyfile /etc/caddy/Caddyfile.pre-rebuild && ls -la /etc/caddy/'
   ```
   Then show me the current config so we can compare before replacing it:
   ```bash
   ssh root@129.212.176.202 'cat /etc/caddy/Caddyfile'
   ```
   **Do not overwrite it until I have seen that diff and said go.** The repo copy
   is a template with placeholders (`scripts/Caddyfile.dashboard.template`) — it
   deliberately contains no hostname and no credential hash, so it needs filling
   in on the server. Ask me for the values; do not invent them and do not print a
   password or hash back into the transcript.

4. **Validate before reloading.** Never reload an unvalidated Caddy config:
   ```bash
   ssh root@129.212.176.202 'caddy validate --config /etc/caddy/Caddyfile.new --adapter caddyfile'
   ```
   Only on success:
   ```bash
   ssh root@129.212.176.202 'mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile && systemctl reload caddy && systemctl is-active caddy'
   ```
   Reminder from `HOW_TO_ADD_USERS.md`: the Cloudflare record must stay
   **DNS-only / grey cloud**, or certificate renewal breaks.

5. ## The test that matters — do not skip it

   The entire reason the dashboard is now its own process is that the old one
   died with the engine. Prove the new one does not:

   ```bash
   ssh root@129.212.176.202 'systemctl stop betting-pod-shop'
   ```

   Now load the dashboard and check, explicitly:
   - the page still renders
   - the engine chip reads **down** — sourced from systemd's own view in
     `status.json`, not inferred from silence
   - every engine-dependent panel says **unknown** or **stale**, and **NOT zero**
   - Gates, P&L and the funnel still render from their own sources

   Also capture the API's view:
   ```bash
   ssh root@129.212.176.202 'curl -s localhost:8081/api/v2/dashboard' \
     | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['engine']['liveness'], '|', d['engine']['liveness_reason']); print('open exposure:', d['pnl']['open'])"
   ```
   `liveness` must be `down`. `pnl.open.bankroll` must be `null` — **if it shows a
   number while the engine is stopped, that is a fabricated value and a hard
   stop.** Report it immediately.

   **Then start the engine again, and confirm it came back:**
   ```bash
   ssh root@129.212.176.202 'systemctl start betting-pod-shop && sleep 20 && systemctl is-active betting-pod-shop'
   ```
   Do not end this command with the engine stopped. Verify it is `active` and say
   so explicitly.

6. Report what the engine-down test showed, then hand back to me. Do **not**
   proceed to cutover — that is `/dashboard-cutover`, and only after I have used
   the new dashboard for a few days.

## Rollback

```bash
ssh root@129.212.176.202 'cp /etc/caddy/Caddyfile.pre-rebuild /etc/caddy/Caddyfile && systemctl reload caddy'
ssh root@129.212.176.202 'systemctl disable --now betting-dashboard'
```
The engine never knew this happened.
