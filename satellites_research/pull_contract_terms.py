"""
satellites_research/pull_contract_terms.py
──────────────────────────────────────────
Download + text-extract the certified contract-terms PDFs for every template
touched by the three satellite sub-studies.  READ-ONLY.

The whole point of this task is that settlement rules are READ, not assumed
(this project's mechanic hypotheses are 3-for-3 because of that discipline),
so every rule quoted in the REPORT must be traceable to one of the .txt files
this script produces.

Cached: an existing .pdf/.txt pair is never re-fetched.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
TERMS = os.path.join(HERE, "contract_terms")
DATA = os.path.join(HERE, "data")
os.makedirs(TERMS, exist_ok=True)

# Study A: the award family (tie-regime census).  Study B: RT family.
# Study C: WINS / EXACTWINS partitions.  Plus adjacent templates used as
# controls / to test whether the rule generalises.
TEMPLATES = [
    # -- Study A: awards
    "GLOBES", "CRITICS", "AMAS", "OSCARS", "EMMYS", "GRAMMY", "ACMA",
    "TONYS", "SAGAWARDS", "BAFTA", "BILLBOARDAWARDS", "BETAWARDS",
    "AWARDSCMA", "GAMEAWARDS", "LATINGRAMMY", "LATINGRAMMYS",
    "IHEARTRADIOMUSICAWARDS", "PRODUCERSGUILDAWARDS",
    "FILMINDEPENDENTSPIRITAWARDS", "GOLDENGLOBESNOM", "AWARDS",
    # -- Study B: Rotten Tomatoes family
    "RT", "RTTV", "RTCOMPARISON", "RTNGS", "METACRITIC",
    # -- Study C: season-wins partitions
    "NFLWINS", "NFLEXACTWINS", "MLBWINS", "NBAWINS", "NHLWINS", "WNBAWINS",
    "NCAAFWINS", "NCAAMBWINS", "NFLXWINS", "NFLDIVISIONWINS",
    "NFLDIVISIONTOTALWINS",
]

BASES = [
    "https://kalshi-public-docs.s3.amazonaws.com/contract_terms/{}.pdf",
    "https://assets.kalshi.com/contract_terms/{}.pdf",
]


def extract(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join((p.extract_text() or "") for p in r.pages)


def main() -> None:
    # Prefer the exact URL the series catalogue advertises, when we have it.
    url_by_tpl: dict[str, str] = {}
    p = os.path.join(DATA, "all_series.json")
    if os.path.exists(p):
        for s in json.load(open(p)).values():
            u = s.get("contract_terms_url") or ""
            if u:
                url_by_tpl.setdefault(u.rsplit("/", 1)[-1].replace(".pdf", ""), u)

    ok, miss = [], []
    for tpl in TEMPLATES:
        txt_p = os.path.join(TERMS, f"{tpl}.txt")
        if os.path.exists(txt_p):
            ok.append(tpl)
            continue
        urls = ([url_by_tpl[tpl]] if tpl in url_by_tpl else []) + \
               [b.format(tpl) for b in BASES]
        body = None
        for u in urls:
            try:
                r = requests.get(u, timeout=30)
            except requests.RequestException:
                continue
            if r.status_code == 200 and r.content[:4] == b"%PDF":
                body = r.content
                src = u
                break
            time.sleep(0.3)
        if body is None:
            miss.append(tpl)
            print(f"  MISS {tpl}")
            continue
        with open(os.path.join(TERMS, f"{tpl}.pdf"), "wb") as fh:
            fh.write(body)
        with open(txt_p, "w") as fh:
            fh.write(f"SOURCE: {src}\n\n" + extract(body))
        ok.append(tpl)
        print(f"  ok   {tpl}  <- {src}")
        time.sleep(0.3)
    print(f"\n{len(ok)} available, {len(miss)} missing: {miss}")


if __name__ == "__main__":
    main()
