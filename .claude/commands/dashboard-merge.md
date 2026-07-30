---
description: Review, test and merge the dashboard-rebuild branch into main
# Only the read-only git queries used by the !`...` injections below are
# pre-approved. The mutating steps (checkout / merge / push / rm) are
# deliberately absent so they still surface a permission prompt.
allowed-tools: Bash(git branch --show-current), Bash(git status --short), Bash(git log *), Bash(git diff *)
---

# Merge the dashboard rebuild

Branch `dashboard-rebuild` sits on top of `b4fc21b`. Nothing is deployed by this
command — it only gets the code onto `main` and pushed.

Current branch: !`git branch --show-current`
Working tree: !`git status --short`
Branch commits: !`git log --oneline main..dashboard-rebuild 2>/dev/null || echo "(branch not found)"`

## Preconditions — refuse if any fails

1. `dashboard-rebuild` exists locally. If not, stop: the bundle was never fetched.
2. The working tree has **no uncommitted changes to tracked files**. Untracked
   files are fine. If there are modifications, stop and show them — do not stash
   or discard someone's work to make room for this.
3. `main` is at `b4fc21b` or a descendant that still allows a fast-forward.

## Steps

1. **Show me what changed** before anything else:
   ```bash
   git diff --stat main..dashboard-rebuild
   git log -1 --format=%B dashboard-rebuild
   ```
   Summarise the diff in a few lines. Call out anything touching
   `scripts/betting-pod-shop.service`, `src/engine.py` or `manager/registry.yaml`,
   since those affect the running engine.

2. **Run the full suite on the branch.**
   ```bash
   git checkout dashboard-rebuild
   python3 -m pytest tests/ -q
   ```
   **EXPECT EXACTLY: 2145 passed, 3 skipped.**

   A different count is a hard stop. It means this working copy and the branch
   disagree about something — the exact failure mode that silently reverted a
   production settler fix on 2026-07-29 (a stale clone showed 1,804 tests where
   the real tree had 1,888). Report the count and stop. Do not "fix" tests to
   reach the number.

3. **Run the back-compat gate specifically.** These 147 tests pin the
   `/api/status` contract that `src/health_check.py` depends on:
   ```bash
   python3 -m pytest tests/test_web_dashboard.py tests/test_dashboard.py -q
   ```
   Zero failures, and **do not modify either file** to achieve that.

4. **Fast-forward merge and push.**
   ```bash
   git checkout main
   git merge --ff-only dashboard-rebuild
   git push origin main
   ```
   If `--ff-only` refuses, stop and report why. Do not rebase or force.

5. **Clean up the transfer artifacts** (only after a successful push):
   ```bash
   rm -f dashboard-rebuild.bundle
   rm -rf _to_delete/
   ```
   `_to_delete/` holds git lock files that had to be moved rather than deleted
   because the tool that fetched the bundle could not unlink them. Deleting it is
   safe. If `.git/index.lock` reappears and blocks a commit, deleting that file is
   also safe **provided no git process is running**.

6. Confirm `main` is clean and pushed, then tell me the next step is
   `/dashboard-deploy-phase0`.

## Rollback

Nothing here touches production. To undo the merge before pushing:
`git reset --hard b4fc21b`. After pushing, revert the merge commit rather than
force-pushing.
