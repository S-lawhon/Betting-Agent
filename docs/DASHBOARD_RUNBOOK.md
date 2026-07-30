# Dashboard runbook

Deploying the rebuilt dashboard, in three additive phases. Nothing about the
current dashboard changes until phase 3 (`/dashboard-cutover`), which is
deliberately deferred.

## Run it from Claude Code

These steps are packaged as project slash commands in `.claude/commands/`, so
Claude Code (running on the Mac, where SSH and `git push` work) can execute them:

| Command | Does |
|---|---|
| `/dashboard-merge` | review the diff, run the suite, fast-forward `main`, push |
| `/dashboard-deploy-phase0` | deploy the code; the engine starts writing snapshots |
| `/dashboard-deploy-phase1` | hand-build the rollup, then enable its hourly timer |
| `/dashboard-deploy-phase2` | start the standalone dashboard on `:8081` + Caddy, then the engine-down test |
| `/dashboard-verify` | **read-only** health check of the whole data plane — safe anytime |
| `/dashboard-cutover` | phase 3: retire the embedded dashboard, move the health gate |

Two deliberate choices in how these are packaged:

* **They are commands, not skills.** Skills are *autonomously* invoked by the
  model and there is no supported way to turn that off. A command runs only when
  a human types it. For something that restarts a live trading engine and
  overwrites a TLS config, slash-only invocation is the safety property — and it
  is worth the fact that `.claude/commands/` is the older of the two formats.
* **`allowed-tools` pre-approves only read-only checks.** `/dashboard-verify` is
  scoped to reads. The phase commands list nothing, so every mutating step goes
  through normal permission rules and surfaces a prompt. The docs do not specify
  whether a tool absent from `allowed-tools` is prompted or hard-denied, so the
  design does not depend on that behaviour either way.

Each command states its own preconditions and refuses to run if they are unmet,
and each ends with its rollback. Run them in order; `/dashboard-verify` between
any two is free.

---


Branch `dashboard-rebuild` (commit `485cbc0`) is already fetched into your repo,
on top of `b4fc21b`. Nothing is deployed yet, and nothing about the current
dashboard has changed.

**Scope of this pass:** the new dashboard runs *side by side* on `:8081` while the
engine keeps serving the old one on `:8080`. `scripts/deploy.sh`'s health gate is
untouched. Cutover is deferred until you've used it.

---

## 0. Review and merge (on your Mac)

```bash
cd ~/Desktop/"Betting Fund Project"

# what changed
git diff --stat main..dashboard-rebuild
git log -1 --format=%B dashboard-rebuild        # the full rationale

# run the suite on the branch. EXPECT: 2145 passed, 3 skipped.
git checkout dashboard-rebuild
python3 -m pytest tests/ -q

# the back-compat gate specifically — 147 tests, zero changes allowed
python3 -m pytest tests/test_web_dashboard.py tests/test_dashboard.py -q
```

If the count differs from **2145 passed, 3 skipped**, stop and tell me — a
mismatch means the branch and your tree disagree about something.

```bash
git checkout main
git merge --ff-only dashboard-rebuild
git push origin main
rm dashboard-rebuild.bundle
rm -rf _to_delete/                # git lock files I had to move aside; safe to delete
```

---

## Phase 0 — deploy the code (nothing serves differently yet)

```bash
bash scripts/deploy.sh 129.212.176.202 restart
```

Your normal command, unchanged. It still test-gates before restarting, and still
health-checks `:8080/health`. **Never answer `y` to its prompt with failures
outstanding.**

What this turns on: the engine starts writing two small files every cycle.

```bash
ssh root@129.212.176.202 '
  ls -la /opt/betting-pod-shop/data/dashboard/
  sleep 310
  ls -la /opt/betting-pod-shop/data/dashboard/     # mtimes must have advanced
'
```

Sanity-check that `/api/status` gained nothing but additive keys:

```bash
curl -su USER:PASS https://dashboard.htxtrades.org/api/status \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(sorted(d))"
```

`engine_status`, `risk`, `cycle`, `pods`, `settlement`, `trades` must all still be
there. `trades` is now capped at 400 (`trades_truncated` says whether more exist).

**Rollback:** `deploy.sh`'s existing auto-rollback. The new files are inert —
nothing reads them yet.

---

## Phase 1 — build the rollup (do this by hand, off a deploy window)

The first pass streams every surviving archive, so it is not something to let a
timer discover during a restart.

```bash
ssh root@129.212.176.202 '
  cd /opt/betting-pod-shop
  nice -n 19 ionice -c3 venv/bin/python -m scripts.build_dashboard_rollup \
      --root /opt/betting-pod-shop --full
'
```

Cross-check the result against something independent before trusting it:

```bash
ssh root@129.212.176.202 '
  cd /opt/betting-pod-shop
  venv/bin/python -m scripts.build_dashboard_rollup --dry-run --json | head -30
  # does the 24h overlap agree with the collector?
  python3 -c "import json;print(json.load(open(\"manager/state/status.json\"))[\"trade\"][\"actions\"])"
  # spot-check one archive
  zgrep -c PLACED data/trade_logs/trade_log.archive_*.jsonl.gz | tail -3
'
```

Then install the timer:

```bash
scp scripts/systemd/dashboard-rollup.{service,timer} root@129.212.176.202:/etc/systemd/system/
ssh root@129.212.176.202 '
  systemctl daemon-reload
  systemctl enable --now dashboard-rollup.timer
  systemctl list-timers dashboard-rollup.timer --no-pager
'
```

**⚠ Back up `data/dashboard/rollup.json`.** Once `rotate_active_log` prunes an
archive (it keeps 12), the rows it held survive *only* in that file's counters and
cannot be rebuilt from disk. The timer passes `--backup`, which writes
`rollup.json.bak.gz` beside it — include both in whatever backs up
`manager/state/`.

**Rollback:** `systemctl disable --now dashboard-rollup.timer`. Nothing reads the
file yet.

---

## Phase 2 — start the new dashboard on :8081

```bash
scp scripts/betting-dashboard.service root@129.212.176.202:/etc/systemd/system/
ssh root@129.212.176.202 '
  systemctl daemon-reload
  systemctl enable --now betting-dashboard
  systemctl status betting-dashboard --no-pager | head -12
  curl -s localhost:8081/healthz | python3 -m json.tool
'
```

`/healthz` returns 200 even when a source is stale — it tells you *which* source,
rather than refusing to load. Check that every source reads `available: true`.

Then point Caddy at it. The config is now version-controlled as a template with
no hostname and no credentials in it:

```bash
scp scripts/Caddyfile.dashboard.template root@129.212.176.202:/etc/caddy/Caddyfile.new
ssh root@129.212.176.202 '
  sed -i "s|__DASHBOARD_HOST__|dashboard.htxtrades.org|; \
          s|__BASIC_AUTH_USER__|YOUR_USER|; \
          s|__BCRYPT_HASH__|$(caddy hash-password --plaintext YOUR_PASS)|" \
      /etc/caddy/Caddyfile.new
  caddy validate --config /etc/caddy/Caddyfile.new --adapter caddyfile
'
```

Before overwriting, **save the current config** — it has never been in git:

```bash
ssh root@129.212.176.202 'cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.pre-rebuild'
ssh root@129.212.176.202 'mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile && systemctl reload caddy'
```

Reminder from `HOW_TO_ADD_USERS.md`: the Cloudflare record must stay **DNS-only /
grey cloud**, or cert renewal breaks.

### The test that matters

Stop the engine for two minutes and confirm the dashboard still works and says so
honestly:

```bash
ssh root@129.212.176.202 'systemctl stop betting-pod-shop'
# open the dashboard. Expect:
#   - the page loads
#   - engine chip reads "down" (sourced from systemd, not from silence)
#   - every engine-dependent panel says "unknown"/"stale" — NOT zero
#   - gates, P&L and the funnel still render from their own sources
ssh root@129.212.176.202 'systemctl start betting-pod-shop'
```

That behaviour is the whole reason the dashboard is now its own process.

**Rollback:** repoint Caddy at `127.0.0.1:8080` (or restore
`/etc/caddy/Caddyfile.pre-rebuild`) and `systemctl disable --now
betting-dashboard`. The engine never knew.

---

## Then use it on your phone

- bottom tab bar reachable with a thumb, no horizontal scroll anywhere
- the funnel legible, the paper ribbon visible on every tab
- **Now** should answer, in one screen: where each gate stands, what's alarming,
  and why nothing fired

---

## Deferred — do not do these yet

**Phase 3 (cutover), once you're happy:** drop `--web --no-browser` from
`scripts/betting-pod-shop.service`, repoint `deploy.sh`'s health gate to
`:8081/healthz` **in the same commit** (they must revert together), then delete
`src/templates/dashboard.html` a week later.

**Phase 4:** tighten the memory ceilings from real numbers —
`systemctl show -p MemoryPeak betting-pod-shop betting-dashboard` after a week.
This pass set `MemoryMax=640M` on the engine (it was the only unit on the box
with no cap, and the one that OOM-crash-looped in June), 192M on the dashboard
and 256M on the rollup.

---

## One decision I left for you

`scripts/rotate_trade_logs.py` is the only writer of the monthly
`data/trade_logs/archive/YYYY-MM.jsonl.gz` convention, and it is **not**
registered as a cron in `manager/registry.yaml`. Without it, the 12-archive prune
is the only history horizon — which is exactly what makes `rollup.json`
irreplaceable. Registering it is the cheapest way to reduce that exposure. Out of
scope for the rebuild; worth a decision.
