# Where the P-029 settled-combo archive lives

**It is NOT in this repo.** `combo_research/archive/` does not exist by design.

| | |
|---|---|
| Local backup | `~/Backups/p029-combo-archive/` (see the README there) |
| Source of truth | `143.198.162.120:/var/lib/p029/archive/` (P-029 VPS) |
| Contents | 1,000 gzipped JSONL parts + `manifest.json` — 719 MB, 11.6M rows, days 2026-07-22 → 2026-07-31 |
| Verified | all 1,000 parts pass `gzip -t`, 2026-07-31 |

## Why it is not committed

This repo is public and `.git` was 49 MB. 719 MB of already-gzipped data would grow it ~15×,
permanently — git keeps blobs forever, so reversing it means a history rewrite. The
`golf_quirks_research/archive/` precedent is 25 MB only because 208 MB of *loose* files
compressed to 10 MB; these parts are already compressed.

Decision made by Sam on 2026-07-31: back up outside git.

## The gap this file exists to close

`scripts/check_research_committed.sh` cannot guard a file it cannot see. The 2026-07-25 loss
happened because working caches were invisible to `git status` and nobody noticed. This
pointer is committed so the archive's location survives in the repo even though the data
does not — **if you move or delete the backup, edit this file.**

## Refresh

```bash
rsync -az --stats --exclude=seen.sqlite \
  -e "ssh -i ~/.ssh/betting_deploy" \
  root@143.198.162.120:/var/lib/p029/archive/ \
  ~/Backups/p029-combo-archive/
```

The archiver writes a new day each morning at 09:30 UTC. Re-run periodically, or recent
days exist only on the VPS. Kalshi purges settled combo markets after ~3 months — aged-out
days are **not re-pullable**.
