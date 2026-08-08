#!/usr/bin/env bash
#
# Guard against the 2026-07-25 failure mode: research work product sitting
# untracked on disk until it is deleted.
#
# On that day the golf_quirks_research harness .py files were never committed and
# vanished. The markdown reports were recoverable from session copies; the code was
# not, and P-023 Phase 2 is still blocked on rebuilding it.
#
# Exits non-zero if any research analysis code or REPORT is untracked or has
# uncommitted modifications. Run before handing a session off or ending a research run.
#
#   bash scripts/check_research_committed.sh
#
set -uo pipefail

cd "$(git rev-parse --show-toplevel)"

# What counts as work product worth protecting.
PATTERNS=(
  '*_research/**/*.py'
  '*_research/*.py'
  '*_research/**/REPORT*.md'
  '*_research/REPORT*.md'
  '*_research/**/*_params.json'
  '*_research/*_params.json'
  # Model/human research runs write these outside a *_research directory.
  # They are the durable completion signal for the dispatch funnel and must
  # not live only on the VPS.
  'research/dispositions/*.json'
  'research/dispositions/**/*.json'
  # Calibration is itself a research gate. Its durable report and any compact
  # committed result artifact must not disappear inside ignored data/ output.
  'research/calibration/REPORT*.md'
  'research/calibration/*.json'
)

fail=0

# Stale Cowork git worktrees under .claude/worktrees/ are full repo copies; every
# research file in them matches the patterns above and is pure noise here.
strip_noise() { grep -v '^\.claude/worktrees/' || true; }

# ── Untracked (the case that actually bit us) ─────────────────────────────────
# --others lists untracked; -x forces ignored files to be listed too, so a research
# .py hidden behind a broad .gitignore rule (data/, *.jsonl) still gets caught.
untracked="$(git ls-files --others -x '' -- "${PATTERNS[@]}" 2>/dev/null | strip_noise | sort -u)"
if [ -n "$untracked" ]; then
  echo "UNTRACKED research work product — commit this before you lose it:"
  printf '  %s\n' $untracked
  fail=1
fi

# ── Tracked but modified / staged-not-committed ───────────────────────────────
dirty="$(git status --porcelain -- "${PATTERNS[@]}" 2>/dev/null | grep -v '^??' | sed 's/^...//' | strip_noise | sort -u)"
if [ -n "$dirty" ]; then
  [ "$fail" = 1 ] && echo ""
  echo "UNCOMMITTED changes to tracked research work product:"
  printf '  %s\n' $dirty
  fail=1
fi

# ── Ignored-by-gitignore research code (silent invisibility, the root cause) ───
# A file matching a work-product pattern that .gitignore excludes will never show up
# in `git status`, which is precisely why nobody noticed in July.
ignored="$(git ls-files --others --ignored --exclude-standard -- "${PATTERNS[@]}" 2>/dev/null | strip_noise | sort -u)"
if [ -n "$ignored" ]; then
  [ "$fail" = 1 ] && echo ""
  echo "research work product hidden by .gitignore (git status will NEVER show these):"
  printf '  %s\n' $ignored
  fail=1
fi

if [ "$fail" = 0 ]; then
  echo "OK — all research code and REPORTs are committed."
fi
exit "$fail"
