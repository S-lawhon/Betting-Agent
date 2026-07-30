# RUN SUMMARY — 2026-07-30 overnight dashboard deploy

## Headline

**RUN HALTED AT THE PREFLIGHT GATE. Nothing was deployed, merged, committed
(other than this report), or changed — on the Mac or on the droplet.**
`betting-pod-shop` is `active` and was never touched. The dashboard deploy
(phases 0–2, merge, rollup, engine-down test, verify, adversarial review) did
**not** run and is all still waiting.

The brief's own preflight gate fired on its predicted trigger: a tracked file
is modified in the working tree, and `scripts/deploy.sh` rsyncs the working
tree, so phase 0 would have shipped an uncommitted trading-parameter change to
production as a side effect. Per the gate: stop, report, do not decide.

## What tripped the gate

- **File:** `config_multi_pod.yaml` (tracked, modified, uncommitted)
- **Change:** `pods.P-022.quoting.min_top_size: 100 → 0` — disables the P-022
  top-of-book size screen — plus a long replacement comment arguing the screen
  is an §8.1 change under the 07-29 one-sided pre-registration and is the
  wrong screen (gates on `top_ask_qty`, which is disjoint from
  `size_ahead_of_quote` on the AIGWO26 census).
- **Exact diff:** appended verbatim at the bottom of this report.

## Why this specific situation needed Sam, not an unattended run

Three facts that individually look routine and together do not:

1. **Git (`main` @ `b4fc21b`, in sync with `origin/main`) says
   `min_top_size: 100`** — committed 2026-07-29 in `297ce2b`.
2. **Production already runs `min_top_size: 0`.** Verified read-only over SSH
   at halt time: `/opt/betting-pod-shop/config_multi_pod.yaml` line 570 reads
   `min_top_size: 0`, and `betting-pod-shop` is `active`. So the droplet was
   updated out-of-band and git does not reflect what is live.
3. **The working tree matches production, not git.**

Every unattended path from here was wrong in a different way:

- **Deploy the dirty tree** (what phase 0 would have done): silently ships an
  uncommitted trading-parameter change — the exact by-product the gate exists
  to prevent. (Here it happens to match what's live, but that would have been
  luck, not authorisation.)
- **Revert to git and deploy**: silently flips the LIVE P-022 screen from
  0 back to 100 hours before the POI26 window opens (2026-07-30T16:00Z) —
  the worst possible unattended action.
- **Commit the change**: makes a live-parameter decision on Sam's behalf. The
  working-tree comment says "DISABLED 2026-07-30 by Sam's decision", but that
  claim lives inside the file itself; the run brief is explicit that this is
  not an unattended call.

## The intended resolution already exists on disk

`p022_commit_and_deploy.sh` (untracked, repo root) was prepared by a prior
Claude session explicitly **for Sam to run on the Mac**. It reproduces two
reviewed commits (the §8.1 research finding, then the config disable),
verifies the four files by SHA-256 first, asserts every other registered
P-022 parameter is unchanged, runs the suite, pushes, and optionally deploys.
The commit messages carrying the §8.1 reasoning exist only in that script —
that is why the change was left uncommitted rather than sloppiness.

## What ran (complete list)

1. `git fetch && git status --porcelain` — `M config_multi_pod.yaml` plus 12
   untracked files (dashboard assets, P-022 research, this brief's prompt).
2. `git diff config_multi_pod.yaml` — captured verbatim below.
3. `git log` — HEAD is `b4fc21b`, branch `main`, in sync with `origin/main`;
   `config_multi_pod.yaml` last committed in `297ce2b`. `dashboard-rebuild`
   branch and `dashboard-rebuild.bundle` both exist.
4. One read-only SSH to the droplet: `systemctl is-active betting-pod-shop`
   → `active`; `betting-live-maker` → `inactive` (not investigated — out of
   scope); `grep min_top_size` on the deployed config → `0` at line 570.
5. Read `p022_commit_and_deploy.sh` (read-only) to understand the situation.
6. Wrote and committed this report.

Nothing else. No test run, no merge, no rsync, no systemctl start/stop/restart,
no file on the droplet touched.

## What I did NOT do (the entire brief, explicitly)

- **Preflight beyond the gate** — halted at step 1.
- **Commit of `.claude/commands/dashboard-*.md` / `docs/DASHBOARD_RUNBOOK.md`**
  — not done (halt came first; also keeps the tree state exactly as found).
- **/dashboard-merge** — not run. `dashboard-rebuild` is unmerged; the bundle
  and `_to_delete/` cleanup untouched.
- **/dashboard-deploy-phase0** — not run. No code deployed;
  `engine_state.json` not checked.
- **/dashboard-deploy-phase1** — not run. No rollup built, no cross-check, no
  timer enabled.
- **Phase 2 / betting-dashboard.service on :8081** — not installed.
- **Engine-down test** — not run (engine never stopped).
- **/dashboard-verify** — not run.
- **Adversarial review** of the dashboard code — not done.
- **Caddy** — untouched (as required regardless).
- **Cutover** — not run (deferred regardless).
- `config_multi_pod.yaml` — **not committed, not stashed, not reverted**;
  left byte-identical to how I found it.
- The 12 untracked files — left untracked (several belong to the P-022
  decision and to `p022_commit_and_deploy.sh`'s hash checks; committing them
  out from under that script would break its verification step).
- `scripts/check_research_committed.sh` — deliberately not "fixed": it would
  fail on the untracked P-022 research files, and committing those is part of
  Sam's script, not this run.

## Engine / timing status at end of run

- `betting-pod-shop`: **active** (never touched).
- P-022 next window: 2026-07-30T16:00Z. Live config has the screen disabled
  (`min_top_size: 0`); whether that stands is Sam's pending decision.
- `main` pushed (this report only, fast-forward). Working tree intentionally
  NOT clean: the `config_multi_pod.yaml` modification remains, by the gate's
  own instruction.

## Exact next commands for Sam in the morning

1. **Decide the P-022 screen question first.** If the disable stands:

```bash
cd ~/Desktop/"Betting Fund Project"
bash p022_commit_and_deploy.sh            # commit + push (add --deploy to also deploy)
```

   If it does NOT stand, the droplet is live at `min_top_size: 0` right now
   and needs an explicit revert + deploy — do not let the next deploy carry
   the answer by accident in either direction.

2. **Then re-run the dashboard deploy** — the whole overnight brief
   (`research/prompts/OVERNIGHT_2026-07-30_dashboard_deploy.md`) is still
   valid and none of it has been consumed. With a clean tree the preflight
   gate will pass. Note the 16:00Z P-022 window when choosing when to run it.

3. When the dashboard is eventually up, view it over a tunnel:

```bash
ssh -L 8081:127.0.0.1:8081 root@129.212.176.202
# then open http://localhost:8081/ in a browser
```

## The diff, verbatim

```diff
diff --git a/config_multi_pod.yaml b/config_multi_pod.yaml
index a0e23b4..8a0ddf0 100644
--- a/config_multi_pod.yaml
+++ b/config_multi_pod.yaml
@@ -539,9 +539,35 @@ pods:
       # Size screen (added 2026-07-30, R5 house rule): refuse to place a NEW
       # quote off a book with fewer than this many contracts at the top of the
       # reference side(s) — spread says nothing (a 152,862-contract bid and a
-      # 1-contract bid both pass "spread <= 2c"). A TIGHTENING of the quoted
-      # population, allowed at any time under §8; resting quotes are untouched.
-      min_top_size: 100
+      # 1-contract bid both pass "spread <= 2c"). Resting quotes are untouched.
+      #
+      # DISABLED 2026-07-30 by Sam's decision, hours before the POI26 window.
+      # The comment above used to read "A TIGHTENING of the quoted population,
+      # allowed at any time under §8." That is wrong, and the document that
+      # governs this gate says so by name. P022_ONESIDED_PREREGISTRATION
+      # _2026-07-29.md §8: "the pod has no size or depth screen ... Adding one
+      # is an §8.1 change and is not part of this registration." Its §2 pins
+      # "No depth screen" among the unchanged parameters. §8.1's reset is about
+      # POPULATION IDENTITY, not generosity — a narrowing is not exempt.
+      #
+      # It is also the wrong screen. On the AIGWO26 census the in-band books
+      # split DISJOINTLY: the 10 it keeps carry 801-1,122 contracts ahead of
+      # the quote, the 14 it refuses carry 12-13 (one at 320). A fill needs a
+      # YES-taker print strictly THROUGH the quote, so the screen keeps the
+      # books ~65x harder to fill and discards the reachable ones — the exact
+      # bimodality the pre-registration flagged as unaddressed BY DESIGN. The
+      # threshold is irrelevant: 20, 50, 100 and 500 all give the same
+      # partition, because top_ask_qty here is either 1 or 800+.
+      #
+      # 0 disables (tested). Observability from 8aa1607 is unaffected: QUOTE
+      # rows still carry book_side + raw yes_bid/yes_ask/bid_qty/ask_qty, which
+      # is what will settle this from real fills. If a screen is wanted later
+      # it is a P-022b registration needing a fill estimate first (P-017A's
+      # standing rule), and it must gate on size_ahead_of_quote, never
+      # top_ask_qty — those differ by a median 12x on exactly these books.
+      # See research/REPORT_P022_Size_Screen_Section8_2026-07-30.md and
+      # golf_quirks_research/screen_vs_size_ahead.py.
+      min_top_size: 0
     risk:
       # §7 caps, as PERCENTAGES of bankroll. They were fixed dollars ($5/$50/$150
       # equivalents) which matched the rule only by coincidence at a $1,000
```
