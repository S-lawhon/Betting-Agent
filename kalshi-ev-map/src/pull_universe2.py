"""Pull open markets series-by-series (skips MVE combinatorial series).

Rationale: /markets?status=open enumerates every auto-generated MVE parlay
market (500k+, newest-first) and never terminates in reasonable time. The
tradable universe lives in ~12k listed series; most have no open markets and
return instantly.

Outputs:
  data/raw/series_all.json        (already pulled)
  data/raw/markets_by_series.json (series -> [market objects])
  data/kalshi_universe.parquet
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalshi_client as kc
from pull_universe import build_frame  # reuse row builder

kc.MIN_INTERVAL = 0.14  # ~7 req/s aggregate

MVE_SERIES_PREFIX = "KXMVE"


def main():
    series_all = json.load(open(kc.CACHE_DIR / "series_all.json"))
    meta = {s["ticker"]: s for s in series_all}
    done = kc.load_cached("markets_by_series") or {}
    todo = [s["ticker"] for s in series_all
            if s["ticker"] not in done
            and not s["ticker"].startswith(MVE_SERIES_PREFIX)]
    print(f"[series-pull] {len(todo)} series to fetch "
          f"({len(done)} cached)", flush=True)
    t0 = time.time()
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def fetch(st):
        try:
            return st, list(kc.paginate("/markets", {
                "series_ticker": st, "status": "open", "limit": 1000},
                list_key="markets"))
        except Exception as e:
            print(f"[warn] {st}: {e}", flush=True)
            return st, []

    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = [ex.submit(fetch, st) for st in todo]
        for i, fut in enumerate(as_completed(futs)):
            st, mkts = fut.result()
            done[st] = mkts
            if (i + 1) % 500 == 0:
                kc.cache_json("markets_by_series", done)
                n = sum(len(v) for v in done.values())
                print(f"[series-pull] {i+1}/{len(todo)}, {n} markets, "
                      f"{time.time()-t0:.0f}s", flush=True)
    kc.cache_json("markets_by_series", done)

    markets = [m for v in done.values() for m in v]
    print(f"[series-pull] total open non-MVE markets: {len(markets)}",
          flush=True)
    df = build_frame(markets, meta)
    out = kc.DATA_DIR / "kalshi_universe.parquet"
    df.to_parquet(out, index=False)
    print(f"[done] {len(df)} rows -> {out}", flush=True)


if __name__ == "__main__":
    main()
