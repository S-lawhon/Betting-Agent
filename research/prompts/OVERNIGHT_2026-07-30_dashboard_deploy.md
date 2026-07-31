# Overnight run — deploy the rebuilt dashboard (2026-07-30)

**You are running UNATTENDED. Sam is asleep. Nobody will answer a question.**

That changes two things. First, where a step below or a slash command says
"confirm with me" — **this document is that authorisation**, for exactly the scope
listed here and nothing beyond it. Second, when something is unclear, the correct
move is **stop and write it up**, never improvise on a live box. An unfinished run
with a clear report is a good outcome. A creative one is not.

You are working on a **paper-mode** trading engine on a 2 GB DigitalOcean droplet
(`root@129.212.176.202`, app at `/opt/betting-pod-shop`). Paper mode means no real
money is at risk, but the box is live, it has OOM-crash-looped before, and it runs
six other workloads.

**Timing constraint:** P-022's next placement window opens **2026-07-30T16:00Z**.
Finish well before that, and never end the run with `betting-pod-shop` stopped.

---

## ⚠ PREFLIGHT GATE — read this before anything else

At the time this brief was written the working tree had an **uncommitted change to
`config_multi_pod.yaml`**: `pods.P-022.min_top_size` changed `100 → 0`, disabling
the size screen, with a comment dated 2026-07-30 reasoning about the POI26 window.

That interacts with this run in a way that is easy to miss:

**`scripts/deploy.sh` rsyncs the WORKING TREE, not a git ref.**
`config_multi_pod.yaml` is not on its exclude list. So **phase 0 will deploy any
uncommitted trading-parameter change to production as a side effect of deploying
the dashboard.** That may well be intended — but it must be a decision, not a
by-product.

**Therefore:**

1. Run `git status --porcelain` and `git diff` first.
2. **If any tracked file is still modified — STOP.** Do not commit it, do not
   stash it, do not revert it, and do not deploy. Changing or discarding a live
   trading parameter is not an unattended decision, and neither is shipping one
   silently. Write a report naming the file, the exact diff, and the fact that
   phase 0 would have carried it to production, then end the run. Everything below
   is still waiting tomorrow; a wrongly-deployed P-022 parameter during a live
   window is not recoverable.
3. **If the tree is clean**, proceed — and still record in the report which commit
   `config_multi_pod.yaml` was at, so the P-022 parameter state at deploy time is
   on the record.

The two changes do not conflict textually (the P-022 edit is around line 539; the
dashboard block is appended at the end), so this is purely about authorisation,
not merge mechanics.

---

## Scope — the whole list, in order

Use the project slash commands where they exist; they encode gates this summary
does not repeat. Read `docs/DASHBOARD.md` and `docs/DASHBOARD_RUNBOOK.md` first.

1. **Preflight.** The gate above. `git fetch && git status`. Confirm no tracked
   file is modified, and confirm `dashboard-rebuild` exists.
2. **Commit the untracked Claude Code assets** — `.claude/commands/dashboard-*.md`
   and `docs/DASHBOARD_RUNBOOK.md` — as their own commit, before the merge.
3. **`/dashboard-merge`** — suite, fast-forward `main`, push. Then clean up
   `dashboard-rebuild.bundle` and `_to_delete/`.
4. **`/dashboard-deploy-phase0`** — deploy the code. Verify the engine writes
   `data/dashboard/engine_state.json` and that its mtime **advances across two
   cycles** (~10 min). One existence check is not evidence.
5. **`/dashboard-deploy-phase1`** — the `--full` rollup backfill. This is the long
   job and the main reason to run overnight. Then **cross-check it** and enable
   `dashboard-rollup.timer`, confirming one timer-driven run actually succeeded.
6. **Install `betting-dashboard.service` on `127.0.0.1:8081`** — the parts of
   `/dashboard-deploy-phase2` that do NOT involve Caddy. Confirm `/healthz` reports
   every source `available: true`.
7. **The engine-down test** — see its own section below. Mandatory, and mandatory
   to verify the restart.
8. **`/dashboard-verify`** — the full read-only sweep, recorded verbatim.
9. **Adversarial review** — see below.
10. **Write and commit the report.**

---

## Hard stops — halt the run and write the report

- **The suite is not exactly `2145 passed, 3 skipped`.** A count mismatch is the
  stale-copy signature that silently reverted a production settler fix on
  2026-07-29 (Sam's tree reported 1,888 tests where the working copy had 1,804).
  Report both numbers. **Do not edit tests to reach the target.**
- **`deploy.sh` reports test failures.** It will abort by itself — with no TTY its
  `read` gets an empty reply and it exits. That is correct. **Do not retry it, do
  not feed it input, do not bypass it, do not rsync by hand.** That gate is the
  only thing that caught the settler revert.
- **`/api/status` loses or renames any of** `engine_status`, `risk`, `cycle`,
  `pods`, `settlement`, `trades`. Roll phase 0 back immediately — `src/health_check.py`
  and 147 tests depend on that contract.
- **The rollup's numbers disagree with the independent cross-check** by more than
  rounding. Stop before enabling the timer and show both figures.
- **`betting-pod-shop` will not return to `active`.** See below.
- Anything you would need Sam's judgement for.

## Never, under any circumstances

- Touch `/etc/caddy/*` or reload/restart Caddy. **The TLS front door stays exactly
  as it is.** The new dashboard is reachable only on loopback tonight; that is
  deliberate, not an oversight to fix.
- Run `/dashboard-cutover`, or edit `ExecStart` in `scripts/betting-pod-shop.service`,
  or repoint `deploy.sh`'s `HEALTH_URL`. Phase 3 is explicitly deferred.
- Delete anything on the droplet, especially under `data/`. If something looks
  like it needs removing, write it down instead.
- Open a firewall port, or bind the dashboard to `0.0.0.0`.
- `cat`, `grep`, `sed` or otherwise print `.env`, `*.pem`, `id_*`, a bcrypt hash or
  any credential. A transcript is a durable copy that cannot be redacted — a
  Kalshi private key leaked this way on 2026-07-28.
- `git push --force`, rebase, or push any branch other than a fast-forward `main`.
- Modify a test, threshold or decision rule to make something pass.

---

## The engine-down test — mandatory, and mandatory to verify

This is the single check that proves the rebuild worked. The old dashboard was a
thread inside the engine process and died with it.

```bash
ssh root@129.212.176.202 'systemctl stop betting-pod-shop'
sleep 30
ssh root@129.212.176.202 'curl -s localhost:8081/api/v2/dashboard' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('liveness:', d['engine']['liveness']); print('reason  :', d['engine']['liveness_reason']); print('open    :', d['pnl']['open'])"
ssh root@129.212.176.202 'curl -s -o /dev/null -w "page: %{http_code}\n" localhost:8081/'
```

Record, verbatim:
- `liveness` — **must be `down`**
- `pnl.open.bankroll` — **must be `null`**. A number here while the engine is
  stopped is a **fabricated value**, exactly the defect the rebuild exists to
  kill (the old dashboard hardcoded `bankroll = 10000`). Treat it as a hard stop
  and headline it.
- the page still returns 200

**Then bring the engine back and prove it:**

```bash
ssh root@129.212.176.202 'systemctl start betting-pod-shop && sleep 25 && systemctl is-active betting-pod-shop'
```

If it is not `active`: retry up to **3 times** with 30 s between attempts, capture
`journalctl -u betting-pod-shop -n 80 --no-pager`, and if it still will not start,
**put that at the very top of the report in capitals** with the journal output.
Do not attempt a creative repair. Do not leave it stopped without saying so
unmistakably.

---

## Cross-checking the rollup — do not skip this

This is the first time the rollup runs against real data. Three independent
figures over the same log; report them **side by side** and say plainly whether
they agree:

1. the rollup's own summary — `build_dashboard_rollup --dry-run --json`
2. the collector's 24-hour `trade.actions` in `manager/state/status.json`
3. a `zgrep -c PLACED` spot-check on two archives

Also record `rows_total`, `pruned_sources_counted` and
`carried_placed_rows_skipped`. Note that `carried_placed_rows_skipped` rising
after a 06:00 rotation is the double-count guard **working**, not an error. If
`pruned_sources_counted` > 0, confirm `rollup.json.bak.gz` exists — that file then
holds history that exists nowhere else on disk.

---

## Adversarial review

The dashboard code was written in one session by one author and has not been read
by anyone else. Spend real effort trying to **break** it, then record findings
without fixing them silently.

Read `src/dashboard_sources.py`, `src/dashboard_api.py`, `src/dashboard_server.py`,
`scripts/build_dashboard_rollup.py` and `src/templates/dashboard_v2.html`, and
specifically look for:

- **Any path where a missing or null value renders as `0`, `0.00`, or an empty
  bar rather than "unknown".** That is the central invariant. `null` from a broken
  reader and a measured `0` require opposite responses.
- Any place the dashboard **computes a gate verdict** instead of displaying the
  sanctioned reader's. Gate Standard §1: a tile is not a verdict.
- Rollup arithmetic that could double-count or drop rows on rotation, truncation,
  an inode change, `BANKROLL_RESET`, or a pruned archive.
- Unbounded reads or anything that could grow memory on a 2 GB box.
- Anything in the template that fetches an external asset.

For each finding: file, line, what breaks, and how you'd confirm it. **If you find
a defect that makes a displayed number wrong, say so at the top of the report** —
a plausible wrong number is worse than a visible gap.

If a fix is genuinely one line and covered by an existing test, you may make it,
but it must be its own commit with the reasoning in the message, and the full
suite must pass afterwards.

---

## Report

Write to `research/RUN_SUMMARY_2026-07-30-dashboard.md` and **commit it, plus any
artifacts, as you go — not at the end.** On 2026-07-25 a research harness was
never committed and vanished; the reports survived only as session copies.
`bash scripts/check_research_committed.sh` fails if anything is left untracked or
hidden by `.gitignore` — run it before you finish.

The report must contain:

1. **Headline** — one line: did the deploy land, is the engine `active`, is the
   dashboard answering on 8081.
2. **What ran, in order**, with the actual observed numbers. Never a number you
   did not see. If you could not check something, say "not checked" and why —
   do not omit it.
3. The rollup cross-check table.
4. The engine-down test result, verbatim.
5. `/dashboard-verify` output.
6. Adversarial review findings, worst first.
7. **"What I did NOT do"** — an explicit list, so Sam knows what is left. It
   should at minimum name: Caddy untouched, cutover not run, `:8081` loopback-only.
8. **Exact next commands** for Sam in the morning, including how to view the
   dashboard over an SSH tunnel:
   ```bash
   ssh -L 8081:127.0.0.1:8081 root@129.212.176.202
   # then open http://localhost:8081/ in a browser
   ```

Finish by leaving `main` pushed and the working tree clean.
