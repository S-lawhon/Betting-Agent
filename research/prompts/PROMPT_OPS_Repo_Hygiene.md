# Claude Code Task — OPS: Repo Hygiene & Pending Deploys (do this FIRST, ~15 min)

> Small, mechanical, and urgent. This is the task that prevents a repeat of the 2026-07-25 file loss. Run it before any research task in the queue.

## Context
Repo: `~/Desktop/Betting Fund Project` (paper-mode Kalshi engine; **no real orders, ever**).

On 2026-07-25 the `golf_quirks_research/` harness `.py` files were **never git-committed and vanished from disk**. The three markdown reports were recoverable from Cowork session copies; the harnesses were not, and P-023 Phase 2 is now blocked on rebuilding them. As of 2026-07-26 `git status` shows the *same* directory is **still untracked**, alongside five prompt files. The house rule adopted after that incident — *commit research artifacts to git immediately at each gate* — has not yet been applied to the artifacts that triggered it.

## Task
1. **Commit the untracked research artifacts.** Currently untracked (verify with `git status --short` before acting):
   - `golf_quirks_research/` — the restored P-022/P-023 reports + `P-022_Fade_Pod_Spec.md` + `data/` caches
   - `research/prompts/NIGHT_RUN_2026-07-24.md`
   - `research/prompts/PROMPT_P023_MakeCut_Phase2.md`
   - `research/prompts/PROMPT_P026_Leader_Monitor.md`
   - `research/prompts/PROMPT_P027_ECONSTAT_Flips.md`
   - `research/prompts/PROMPT_SATELLITES_Quirk_Census.md`
   - plus anything new this queue creates

   Before committing, check `.gitignore` and the size of `golf_quirks_research/data/`. Cached API pulls are **worth committing** if they are the only surviving copy of a ~1-month Kalshi trade-history window that cannot be re-pulled (it rolls off). If any single cache file is >50 MB, gzip it rather than excluding it, and say so in the commit message. Do not commit anything containing credentials.

2. **Audit for the same failure mode elsewhere.** Run `git status --short --untracked-files=all` across every `*_research/` directory and report any other uncommitted analysis artifact. Commit anything that represents work product; list anything you judge to be genuine scratch and why.

3. **Add a guard so this cannot recur silently.** Write `scripts/check_research_committed.sh`: exits non-zero if any `*_research/**/*.py` or `*_research/**/REPORT*.md` is untracked or has uncommitted changes. Wire it into the existing test/CI entry point if one exists; otherwise document it in `CLAUDE.md` under the research-workflow conventions as a pre-handoff check.

4. **Flag (do NOT run) the pending deploy.** `config_multi_pod.yaml` has local changes the droplet does not have — at minimum the `basketball_ncaaw` removal and the P-017 `max_event_exposure_pct: 0.08` cap (committed as `287cf89`). Report exactly what a deploy would ship by diffing local config against the droplet's, but **do not run `scripts/deploy.sh`** — that is Sam's call and it touches a live paper engine.

## Definition of done
All research work product tracked in git with a clear commit message; `scripts/check_research_committed.sh` present and passing; a short written summary of (a) what was committed, (b) any other at-risk artifacts found, (c) precisely what is pending deploy and why you did not deploy it. No deploy performed, no live data touched.
