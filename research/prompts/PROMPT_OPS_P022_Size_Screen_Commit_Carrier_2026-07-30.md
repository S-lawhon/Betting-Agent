Work in `~/Desktop/Betting Fund Project` (the Betting Pod Shop repo, branch `main`). Read `CLAUDE.md` first.

A Cowork session made a P-022 change earlier today but could not commit it, because that sandbox has no GitHub credentials and cannot run git against this folder. The file contents are already on disk and verified. Your job is to get them committed, pushed, and deployed, and to clear the git locks blocking that.

## Situation

P-022 (round-leader dead-heat fade maker) is ARMED for its first real quoting window, which opens today at 16:00Z. A top-of-book size screen shipped yesterday in commit `297ce2b` as `min_top_size: 100`. It has been disabled, set to `0`, for two reasons:

1. It is an §8.1 population change. `golf_quirks_research/P022_ONESIDED_PREREGISTRATION_2026-07-29.md` §8 says verbatim: "the pod has no size or depth screen, and the size resting ahead of its quote is bimodal (median 13 contracts, max 1,122). Adding one is an §8.1 change and is not part of this registration." Its §2 pins "No depth screen" among the unchanged parameters. §8.1 resets T to 0 under a new pod ID for population changes, and nothing in §8 exempts a narrowing. The screen's own report claimed tightening was allowed at any time, which contradicts the registration governing this gate.

2. It is the wrong screen. On `golf_quirks_research/live_book_census_aigwo26.json` the 24 in-band books split disjointly: the 10 the screen keeps carry 801 to 1,122 contracts ahead of the quote, the 14 it refuses carry 12 or 13 (one at 320). `_check_fills` only fills on a YES-taker print strictly through the quote price, so the screen keeps the books roughly 65x harder to fill and discards the reachable ones. The threshold value is irrelevant: 20, 50, 100 and 500 all give the identical partition, because `top_ask_qty` here is either 1 or 800-plus.

Full reasoning is in `research/REPORT_P022_Size_Screen_Section8_2026-07-30.md`, already on disk.

## State on disk

Four files are modified or new and uncommitted:

- `config_multi_pod.yaml` — `min_top_size: 100` to `0`, plus a rewritten comment explaining why the old "allowed at any time under §8" claim was wrong
- `research/REPORT_P022_Size_Screen_Section8_2026-07-30.md` — new
- `golf_quirks_research/screen_vs_size_ahead.py` — new, reproduces the partition and sweeps the threshold
- `scripts/p022_droplet_state_check.sh` — new, read-only droplet inspector
- `p022_commit_and_deploy.sh` at the repo root — a one-shot carrier script, delete after use

`git add` and `git commit` are currently failing. Both `.git/index.lock` and `.git/packed-refs.lock` exist, created at the same millisecond, with `packed-refs` rewritten 2ms after its own lock, which looks like a process killed at the end of a `pack-refs`. Something also wrote `FETCH_HEAD` and `ORIG_HEAD` minutes later, so a client may be fetching on a timer. VS Code's auto-fetch does that.

An earlier `deploy.sh` run already rsynced the working tree to the droplet, so `min_top_size: 0` may already be live there. `deploy.sh` ships the working tree rather than `HEAD`, so confirm rather than assume.

## What to do

1. Check for a live git process with `ps -Ao pid,etime,command | grep '[g]it'`. If a GUI client is running, tell me and stop; do not race it. If nothing is holding them, remove `.git/index.lock` and `.git/packed-refs.lock`.

2. Run `git pull --ff-only`. If it fails, stop and tell me. Do not commit onto a stale clone. A stale clone silently reverted a production settler fix on 2026-07-29 and this is the rule that came out of it.

3. Verify the four files are the ones that were reviewed:

```
387187e75978e91cd49ba333204b2acf044abb5fcbcd258ea0d9b33c8f0cd933  research/REPORT_P022_Size_Screen_Section8_2026-07-30.md
17ac7a6dc42f72484f013dd7b937b7a57bcde614cfc7517c4d592f4b7b16277c  golf_quirks_research/screen_vs_size_ahead.py
fcf328b63987d1276be271fce666b8c9ab5cf6ec8916e770882a8f9696d0452d  scripts/p022_droplet_state_check.sh
7922191b88360ea7793d0355b380249b9272303e93db9a414c0390c3cf742269  config_multi_pod.yaml
```

If any differ, show me the diff and stop rather than committing something unreviewed.

4. Assert from parsed YAML, not from the diff, that `pods.P-022.quoting.min_top_size` is `0` and that every other registered parameter is unchanged: `mid_band` `[0.03, 0.12]`, `quote_offset` `0.02`, `no_new_quote_h` `12.0`, `fade_start_h` `24.0`, `pct_per_name` `0.005`, `pct_per_tournament` `0.05`, `pct_total` `0.15`. Those are gate conditions under §7, and any of them moving is an §8.1 reset. Stop if one moved.

5. Run `python3 -m pytest tests/ -q`. Expect roughly 1,946 passing, 3 skipped. Stop on any failure.

6. Make two commits, in this order, with these exact messages. The reasoning in them is the deliverable, so do not shorten them.

Commit 1, staging the three new files:

```
research(P-022): the size screen is an §8.1 change — HOLD the deploy

The 07-29 one-sided pre-registration says it in writing, §8, verbatim:
"the pod has no size or depth screen ... Adding one is an §8.1 change and is
not part of this registration." Its §2 pins "No depth screen" in the list of
unchanged parameters. `297ce2b` shipped one the next day, and its report
claims tightening is "allowed at any time under the pre-registration rules."
Both cannot hold. §8.1's reset is about POPULATION IDENTITY, not generosity —
nothing in §8 exempts a narrowing.

What the screen selects for, measured on the census already in the repo
(live_book_census_aigwo26.json, 146 markets, AIGWO26 R1, 2026-07-28):

  kept (10)    size_ahead_of_quote 801 .. 1122
  refused (14) 12 or 13 on all 13 one-sided; 320 on the one two-sided

Disjoint, no overlap. `_check_fills` needs a YES-taker print strictly THROUGH
the quote, so those are ~801-contract vs ~12-contract sweeps: the screen keeps
the books ~65x harder to fill and discards the reachable ones. The
pre-registration named this exact bimodality (median 13, max 1,122) as
unaddressed BY DESIGN.

The threshold is irrelevant — 20, 50, 100 and 500 all give the identical
partition, because top_ask_qty here is either 1 or 800+. Not a calibration
question about 100; a binary decision about whether to quote the median-13
mode. top_ask_qty also understates what must be swept by a median 12x on the
refused books, so it is a poor proxy for fill difficulty even in principle.

Why the A/B could not see this: 12 quotes both ways because the §7
per-tournament cap binds first, so quote count is pinned and insensitive to
which books get the capital. P-017A's standing rule — never propose a maker
variant without a fill estimate first — was not applied to it.

Second, independent problem: the pre-registration pins "posted market" to
quirks_common.replay's denominator so the live fill rate and the backtest
cells (67/47/15%) are the same quantity. Screened books emit REFUSED, not
QUOTE, so they leave the denominator and the pinned identity breaks — and the
<=25% stop-and-report trigger would fire over a population it was never
defined on.

Adds: the report, a reproducible partition/sweep script, and
scripts/p022_droplet_state_check.sh (read-only; Cowork cannot SSH, so the
running revision and effective min_top_size are unverified from a session).
```

Commit 2, staging `config_multi_pod.yaml`:

```
config(P-022): DISABLE the size screen before the POI26 window — §8.1

Sam's decision, 2026-07-30. `min_top_size: 100` -> `0` (the tested disable
path). Every other registered parameter is byte-identical: band (0.03, 0.12),
offset +0.02, window [12, 24]h, caps 0.5/5/15%. Asserted, not eyeballed.

The screen is an §8.1 population change, and the pre-registration governing
this gate says so by name — see the preceding commit and
research/REPORT_P022_Size_Screen_Section8_2026-07-30.md. It is also the wrong
screen: on the AIGWO26 census it keeps the 10 books with 801-1,122 contracts
ahead of the quote and refuses the 14 with 12-13, disjointly, so it discards
precisely the books a resting quote can be filled in.

The stale claim in the config comment ("A TIGHTENING ... allowed at any time
under §8") is replaced with the reason it is wrong, so the next reader does
not re-derive it from the five-item list in §8.1 instead of from the
registration.

Observability is unaffected: QUOTE rows keep book_side + raw
yes_bid/yes_ask/bid_qty/ask_qty, which is what settles this from real fills.
```

7. Run `bash scripts/check_research_committed.sh`. It must come back clean. Research artifacts hidden by `.gitignore` were lost this way on 2026-07-25.

8. `git push origin main`.

9. Deploy with `bash scripts/deploy.sh 129.212.176.202 restart`, then verify on the droplet with `bash scripts/p022_droplet_state_check.sh`. It should print `min_top_size : 0` and `screen DISABLED`. If it prints `100`, or says the key is absent so the code default applies, the merged config did not take and the screen is live for the window. Tell me immediately if so.

10. Delete `p022_commit_and_deploy.sh` from the repo root once done. If you would rather run it than do steps 2 through 9 by hand, it performs exactly those steps with the same checks; `bash p022_commit_and_deploy.sh --deploy`.

## Do not

- Do not re-enable the size screen or change `min_top_size` away from 0. If a screen is wanted it is a P-022b registration needing a fill estimate first, gating on `size_ahead_of_quote` rather than `top_ask_qty`, and the case for an upper bound is at least as strong as for a lower one.
- Do not touch any other P-022 quoting or risk parameter. They are gate conditions and moving one resets T to 0.
- Do not `cat`, `grep` or `sed` `.env`, `.env.bak-portable`, or `kalshi_private_key.pem`. A PEM leaked that way on 2026-07-28.
- Do not `git add -A`. Stage the named files only.

## After

Report what the droplet check printed, and whether the pod quotes when the window opens at 16:00Z. The next readout that matters is the pre-registered fill rate on one-sided-referenced quotes after five tournaments carrying a quote, with a stop-and-report trigger at 25% or below. With the screen off, that number is comparable to the backtest cells it was pinned to again.
