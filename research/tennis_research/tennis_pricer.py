"""
Point-level tennis pricing engine.

Prices every tennis contract structure Kalshi lists from two parameters:
    pa = P(player A wins a point on A's serve)
    pb = P(player B wins a point on B's serve)

Everything is exact dynamic programming over the scoring hierarchy —
point -> game -> set (with tiebreak) -> match — no Monte Carlo.
The set-level DP tracks the JOINT distribution of
(set winner, games won by A, games won by B, tiebreak occurred),
and match-level props are exact convolutions of set outcomes, so
correlations between match winner / set score / total games / tiebreak
occurrence are modeled exactly rather than assumed independent.

Assumptions (documented, standard in the literature):
  * Points on a given player's serve are iid within the match
    (Klaassen & Magnus 2001: deviations from iid exist but are small).
  * No fatigue / momentum drift across sets (can be added by perturbing
    pa, pb per set index).
  * Final-set rules: standard tiebreak at 6-6 in every set (matches
    current ATP/WTA tour + slam rules with 10-pt TB at 12-12 nuance
    ignored; negligible for pricing).
"""

from functools import lru_cache


# ---------------------------------------------------------------- game

@lru_cache(maxsize=None)
def p_hold(p: float) -> float:
    """P(server wins a standard game) given point-win prob p."""
    q = 1.0 - p
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    # win to love, 15, 30 + reach deuce * win-from-deuce
    win_before_deuce = p**4 * (1 + 4*q + 10*q**2)
    p_reach_deuce = 20 * p**3 * q**3
    p_win_deuce = p**2 / (1 - 2*p*q)
    return win_before_deuce + p_reach_deuce * p_win_deuce


# ------------------------------------------------------------ tiebreak

@lru_cache(maxsize=None)
def p_tiebreak(pa: float, pb: float, first_server: int = 0) -> float:
    """P(player A wins a 7-point tiebreak, win by 2).

    pa/pb: point-win probs on own serve. first_server: 0 if A serves
    the first point of the tiebreak. Serve order: 1 point, then
    alternating pairs (A B B A A B B ...), which we track by point index.
    """

    def server_of_point(k: int) -> int:  # k = 0-based point index
        # points 0 | 1,2 | 3,4 | ... -> server flips each block
        block = (k + 1) // 2
        return (first_server + block) % 2

    @lru_cache(maxsize=None)
    def rec(a: int, b: int) -> float:
        if a >= 7 and a - b >= 2:
            return 1.0
        if b >= 7 and b - a >= 2:
            return 0.0
        if a == 6 and b == 6:
            # From 6-6 each subsequent pair contains one point on each
            # player's serve; race to lead by 2 is a classic gambler's ruin.
            # P(A wins a pair 2-0) = pa*(1-pb); P(B wins pair) = (1-pa)*pb
            win = pa * (1 - pb)
            lose = (1 - pa) * pb
            return win / (win + lose)
        k = a + b
        p_pt = pa if server_of_point(k) == 0 else 1 - pb
        return p_pt * rec(a + 1, b) + (1 - p_pt) * rec(a, b + 1)

    return rec(0, 0)


# ----------------------------------------------------------------- set

@lru_cache(maxsize=None)
def set_distribution(pa: float, pb: float, first_server: int = 0):
    """Exact joint distribution of one set.

    Returns dict {(winner, games_a, games_b, tiebreak): prob} where
    winner is 0 (A) or 1 (B); tiebreak is True iff set reached 6-6.
    first_server: who serves game 1 of the set.
    """
    hold_a = p_hold(pa)
    hold_b = p_hold(pb)
    out = {}

    # state: (games_a, games_b) with server determined by parity
    def server_of_game(g: int) -> int:
        return (first_server + g) % 2

    @lru_cache(maxsize=None)
    def reach(a: int, b: int) -> float:
        """P(set reaches score a-b) — valid only for non-terminal paths."""
        if a == 0 and b == 0:
            return 1.0
        p = 0.0
        for x, y, awin in ((a - 1, b, True), (a, b - 1, False)):
            if x < 0 or y < 0:
                continue
            # predecessor must be non-terminal (set not already decided)
            if (x >= 6 or y >= 6) and abs(x - y) >= 2:
                continue
            if x == 7 or y == 7:
                continue
            srv = server_of_game(x + y)  # game just played
            if awin:
                pg = hold_a if srv == 0 else 1 - hold_b
            else:
                pg = 1 - hold_a if srv == 0 else hold_b
            p += reach(x, y) * pg
        return p

    # enumerate terminal scores: 6-0..6-4, 7-5, 7-6 and mirrors
    for a, b in [(6, 0), (6, 1), (6, 2), (6, 3), (6, 4), (7, 5)]:
        out[(0, a, b, False)] = reach(a - 1, b) * _game_win_prob(hold_a, hold_b, server_of_game(a - 1 + b), 0)
        out[(1, b, a, False)] = reach(b, a - 1) * _game_win_prob(hold_a, hold_b, server_of_game(b + a - 1), 1)
    # 7-6 via tiebreak: reach 6-6, then TB. TB first server = server_of_game(12)
    p66 = reach(6, 6)
    tb_first = server_of_game(12)
    pa_tb = p_tiebreak(pa, pb, tb_first)
    out[(0, 7, 6, True)] = p66 * pa_tb
    out[(1, 6, 7, True)] = p66 * (1 - pa_tb)
    return out


def _game_win_prob(hold_a, hold_b, srv, winner):
    """P(player `winner` wins a game served by `srv`)."""
    if winner == 0:
        return hold_a if srv == 0 else 1 - hold_b
    return 1 - hold_a if srv == 0 else hold_b


def _set_summary(pa, pb, first_server=0):
    """Aggregate set_distribution into useful marginals."""
    dist = set_distribution(pa, pb, first_server)
    return dist


# --------------------------------------------------------------- match

def match_distribution(pa: float, pb: float, best_of: int = 3,
                       first_server: int = 0):
    """Exact joint distribution over full-match outcomes.

    Returns dict {(sets_a, sets_b, total_games, any_tiebreak): prob}.

    Serve order across sets: the player who serves first in set k+1 is
    the one who did NOT serve the last game of set k. We track this
    exactly via total games played parity.
    """
    need = best_of // 2 + 1
    results = {}

    def rec(sets_a, sets_b, games_so_far, any_tb, first_srv, prob):
        if prob < 1e-15:
            return
        if sets_a == need or sets_b == need:
            key = (sets_a, sets_b, games_so_far, any_tb)
            results[key] = results.get(key, 0.0) + prob
            return
        for (w, ga, gb, tb), p in set_distribution(pa, pb, first_srv).items():
            games_in_set = ga + gb
            # next set's first server: opposite of whoever served last game.
            # last game index within set = games_in_set - 1 (0-based), its
            # server = (first_srv + games_in_set - 1) % 2 for non-TB sets;
            # for TB sets game 13 is the TB, treated as game index 12.
            last_game_idx = games_in_set - 1
            last_srv = (first_srv + last_game_idx) % 2
            nxt = 1 - last_srv
            rec(sets_a + (w == 0), sets_b + (w == 1),
                games_so_far + games_in_set, any_tb or tb, nxt, prob * p)

    rec(0, 0, 0, False, first_server, 1.0)
    return results


def set_score_distribution(pa, pb, best_of=3, first_server=0):
    """{(sets_a, sets_b): prob} — e.g. {(2,0): .., (2,1): .., (0,2): ..}."""
    out = {}
    for (sa, sb, g, tb), p in match_distribution(pa, pb, best_of, first_server).items():
        out[(sa, sb)] = out.get((sa, sb), 0.0) + p
    return out


# --------------------------------------------------------------- props

def price_props(pa: float, pb: float, best_of: int = 3, first_server: int = 0):
    """Price every Kalshi-listed tennis prop from (pa, pb).

    Returns a dict of fair YES probabilities:
      match_a          - A wins match                     (KXATPMATCH)
      set1_a           - A wins set 1                     (KXATPSETWINNER-..-1)
      set2_a           - A wins set 2 (unconditional*)    (KXATPSETWINNER-..-2)
      exact_20/21/02/12- exact set score                  (KXATPEXACTMATCH)
      total_games_over - {line: P(total > line)}          (KXATPGTOTAL)
      spread_a         - {line: P(gamesA - gamesB > line)}(KXATPGSPREAD)
      tiebreak_any     - any tiebreak in match            (KXATPTIEBREAK)
      straight_sets_w  - winner takes it in straight sets (KXATPSETSWEEP-ish)

    *Kalshi 'set 2 winner' pays on the set-2 winner regardless of match
     state; if the match ends 2-0 the set-2 market settled on set 2, and
     if a player retires before set 2 completes the market voids — we
     price the played-to-completion case.
    """
    full = match_distribution(pa, pb, best_of, first_server)
    sd = set_distribution(pa, pb, first_server)

    props = {}
    props["match_a"] = sum(p for (sa, sb, g, tb), p in full.items()
                           if sa > sb)
    props["set1_a"] = sum(p for (w, ga, gb, tb), p in sd.items() if w == 0)

    # set 2 winner: depends on set-1 outcome only through who serves first
    # in set 2 (and nothing else under iid) — average over set-1 endings.
    s2 = 0.0
    for (w, ga, gb, tb), p in sd.items():
        games = ga + gb
        last_srv = (first_server + games - 1) % 2
        nxt = 1 - last_srv
        s2 += p * sum(q for (w2, *_), q in set_distribution(pa, pb, nxt).items()
                      if w2 == 0)
    props["set2_a"] = s2

    ss = set_score_distribution(pa, pb, best_of, first_server)
    if best_of == 3:
        props["exact_20"] = ss.get((2, 0), 0.0)
        props["exact_21"] = ss.get((2, 1), 0.0)
        props["exact_02"] = ss.get((0, 2), 0.0)
        props["exact_12"] = ss.get((1, 2), 0.0)
    else:
        for k, v in ss.items():
            props[f"exact_{k[0]}{k[1]}"] = v

    # total games / spread: build marginal over totals and margins
    totals, margin = {}, {}
    for (sa, sb, g, tb), p in full.items():
        totals[g] = totals.get(g, 0.0) + p
    # margin needs games per player, so redo with a finer recursion
    margin = _margin_distribution(pa, pb, best_of, first_server)

    props["total_games_dist"] = dict(sorted(totals.items()))
    props["total_games_over"] = {
        line + 0.5: sum(p for g, p in totals.items() if g > line)
        for line in range(12, 40)
    }
    props["margin_dist"] = dict(sorted(margin.items()))
    props["spread_a"] = {
        line + 0.5: sum(p for m, p in margin.items() if m > line)
        for line in range(-15, 16)
    }
    props["tiebreak_any"] = sum(p for (sa, sb, g, tb), p in full.items() if tb)
    props["straight_sets_a"] = ss.get((2, 0) if best_of == 3 else (3, 0), 0.0)
    props["straight_sets_b"] = ss.get((0, 2) if best_of == 3 else (0, 3), 0.0)
    return props


def _margin_distribution(pa, pb, best_of=3, first_server=0):
    """{games_a - games_b: prob} over the full match."""
    need = best_of // 2 + 1
    out = {}

    def rec(sets_a, sets_b, marg, first_srv, prob):
        if prob < 1e-15:
            return
        if sets_a == need or sets_b == need:
            out[marg] = out.get(marg, 0.0) + prob
            return
        for (w, ga, gb, tb), p in set_distribution(pa, pb, first_srv).items():
            games = ga + gb
            last_srv = (first_srv + games - 1) % 2
            rec(sets_a + (w == 0), sets_b + (w == 1),
                marg + ga - gb, 1 - last_srv, prob * p)

    rec(0, 0, 0, first_server, 1.0)
    return out


# ----------------------------------------------- inversion from market

def implied_holds(match_prob_a: float, expected_total: float = None,
                  best_of: int = 3, avg_spw: float = 0.645,
                  tol: float = 1e-10):
    """Invert market prices back to (pa, pb).

    Given the match-winner probability (and optionally an expected total
    games figure from the totals ladder), solve for the serve-point
    probabilities. With only a match price, we constrain pa + pb to the
    tour-average level (2*avg_spw) and solve for the difference; with a
    total, we solve the 2x2 system since totals pin down pa+pb (serve
    dominance) while the match line pins down pa-pb (skill gap).

    avg_spw defaults: ATP hard ~0.645, ATP clay ~0.635, ATP grass ~0.66,
    WTA ~0.57.
    """
    def match_p(pa, pb):
        return sum(p for (sa, sb, g, tb), p in
                   match_distribution(pa, pb, best_of).items() if sa > sb)

    def exp_total(pa, pb):
        return sum(g * p for (sa, sb, g, tb), p in
                   match_distribution(pa, pb, best_of).items())

    def solve_d(s):
        lo, hi = -min(s - 0.02, 1.98 - s), min(s - 0.02, 1.98 - s)
        for _ in range(80):
            mid = (lo + hi) / 2
            if match_p((s + mid) / 2, (s - mid) / 2) < match_prob_a:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    if expected_total is None:
        s = 2 * avg_spw
        d = solve_d(s)
        return (s + d) / 2, (s - d) / 2

    lo_s, hi_s = 1.02, 1.94
    for _ in range(60):
        s = (lo_s + hi_s) / 2
        d = solve_d(s)
        t = exp_total((s + d) / 2, (s - d) / 2)
        # more serve dominance -> longer sets -> higher totals
        if t < expected_total:
            lo_s = s
        else:
            hi_s = s
    d = solve_d(s)
    return (s + d) / 2, (s - d) / 2


# ------------------------------------------------- mixture pricing

# 3-node Gauss-Hermite quadrature for N(0,1): nodes ±sqrt(3), 0
_GH3 = [(-1.7320508075688772, 1 / 6), (0.0, 2 / 3), (1.7320508075688772, 1 / 6)]


def price_props_mixture(pa, pb, sig_s=0.0, sig_d=0.0, best_of=3):
    """Price props under parameter uncertainty.

    The match-day serve-sum s = pa+pb and skill-gap d = pa-pb are treated
    as Gaussian around their inputs with sds (sig_s, sig_d), integrated by
    3-node Gauss-Hermite quadrature. This captures day-to-day form and
    matchup heterogeneity that a fixed-(pa,pb) iid model misses: it raises
    P(straight sets), P(any tiebreak) and fattens the totals tails at the
    same mean total, matching empirical tennis distributions far better.
    """
    s0, d0 = pa + pb, pa - pb
    agg = None
    for zs, ws in _GH3:
        for zd, wd in _GH3:
            s = min(max(s0 + sig_s * zs, 1.02), 1.9)
            d = d0 + sig_d * zd
            hi = min(s - 0.02, 1.98 - s)
            d = min(max(d, -hi), hi)
            w = ws * wd
            # round nodes so the lru-cached DP layers get heavy reuse
            pr = price_props(round((s + d) / 2, 3), round((s - d) / 2, 3), best_of)
            if agg is None:
                agg = {}
                for k, v in pr.items():
                    agg[k] = ({kk: w * vv for kk, vv in v.items()}
                              if isinstance(v, dict) else w * v)
            else:
                for k, v in pr.items():
                    if isinstance(v, dict):
                        for kk, vv in v.items():
                            agg[k][kk] = agg[k].get(kk, 0.0) + w * vv
                    else:
                        agg[k] += w * v
    return agg


def implied_holds_mixture(match_prob_a, s, sig_s, sig_d, best_of=3):
    """Solve for mean d such that the MIXTURE match prob equals the target."""
    lo, hi = -min(s - 0.02, 1.98 - s), min(s - 0.02, 1.98 - s)
    for _ in range(40):
        mid = (lo + hi) / 2
        pr = price_props_mixture((s + mid) / 2, (s - mid) / 2, sig_s, sig_d, best_of)
        if pr["match_a"] < match_prob_a:
            lo = mid
        else:
            hi = mid
    d = (lo + hi) / 2
    return (s + d) / 2, (s - d) / 2


# ------------------------------------------------------------ fees

def kalshi_taker_fee(price: float, contracts: float = 1.0,
                     rate: float = 0.07) -> float:
    """Kalshi taker fee in dollars: ceil-to-cent(rate * C * P * (1-P))."""
    import math
    raw = rate * contracts * price * (1 - price)
    return math.ceil(raw * 100) / 100


if __name__ == "__main__":
    # sanity checks against known values
    assert abs(p_hold(0.5) - 0.5) < 1e-12
    assert abs(p_hold(0.6) - 0.7357) < 1e-3          # O'Malley table value
    assert abs(p_tiebreak(0.6, 0.6) - 0.5) < 1e-12   # symmetry
    sd = set_distribution(0.65, 0.65)
    assert abs(sum(sd.values()) - 1) < 1e-9
    md = match_distribution(0.65, 0.62, 3)
    assert abs(sum(md.values()) - 1) < 1e-9
    pr = price_props(0.65, 0.62)
    assert abs(sum(pr["total_games_dist"].values()) - 1) < 1e-9
    assert abs(pr["exact_20"] + pr["exact_21"] + pr["exact_02"] + pr["exact_12"] - 1) < 1e-9
    assert abs((pr["exact_20"] + pr["exact_21"]) - pr["match_a"]) < 1e-9
    print("all sanity checks pass")
    print(f"p_hold(0.65) = {p_hold(0.65):.4f}")
    print(f"match_a(0.65 vs 0.62, Bo3) = {pr['match_a']:.4f}")
    print(f"set1_a = {pr['set1_a']:.4f}  tiebreak_any = {pr['tiebreak_any']:.4f}")
    print(f"E[total games] = {sum(g*p for g,p in pr['total_games_dist'].items()):.2f}")
