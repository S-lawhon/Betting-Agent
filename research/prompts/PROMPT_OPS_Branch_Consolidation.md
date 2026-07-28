# PROMPT — Branch consolidation and push

**Run last, so it captures the whole night.**

## The exposure

**66 commits ahead of both `main` and `origin/main`, on a local branch named `p024-mlb-f5-research`** — a name that describes a study killed days ago. That branch holds the P-022 close-time resolver, the fee fixture, the throughput instrument, the AggregateRiskGuard wiring, the settler scoping fix, all five gate readers, and every research verdict from 07-26 onward.

It exists in **one place**: a laptop whose own cron has been denied read access to the folder for 139 consecutive runs. There is no second copy. The droplet has the deployed *files*, not the history.

## Steps

1. **Push first, tidy second.** Get the 66 commits to `origin` on their current branch name before doing anything clever. A safe ugly name beats a clean loss.
2. Verify the push by fetching into a scratch clone and confirming the tip SHA matches and the resolver, fixture and throughput files are present.
3. Then decide the merge posture and **state the reasoning in the report**: fast-forward `main`, or open a PR for the record. Given that every commit is already deployed and there is no reviewer but Sam, a fast-forward with a clear message is likely right — but say why.
4. **Do not delete or merge `p018-inplay-fade-core`.** Its tip removes the Legacy project P-001's scanner imports (see Task 3). Task 3 cherry-picks one commit off it; the branch itself stays.
5. Audit the other 18 local branches: which are merged, which hold unique work, which are dead. **`chore/repo-cleanup-2026-07-22` and the six `claude/*` branches** are the likely candidates for deletion — but check each for unique commits first, and note that last night's cleanup found **one worktree holding an uncommitted independent audit** that had already caught the fifth fee drift. Assume there is another.
6. **Report, do not delete**, anything holding unique work. Deletion is Sam's call.
7. Confirm `scripts/check_research_committed.sh` passes and nothing from tonight is untracked — the working tree currently carries several uncommitted `research/prompts/*.md` and two top-level research documents.

## Stop rule

No history rewriting. No force pushes. No branch deletions without Sam's explicit approval.

## Deliverable

A section in the run summary: tip SHA on origin, verification method, merge posture and its reasoning, branch audit table, and any uncommitted work found.
