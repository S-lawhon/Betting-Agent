"""
src/maker_challenger.py
───────────────────────
Tier-2 of the P-016 recursive review: champion / challenger evaluation.

Per live_agent_research/RECURSIVE_REVIEW_DESIGN.md, Tier 2 is
"theory-driven recalibration" — but the governing constraint is that the
champion is MID-GATE (282+/500 fills on a pre-registered metric whose
sample cannot be re-collected).  So this module does NOT quote, does NOT
run alongside the champion, and does NOT touch its config.  It is a pure
offline evaluator: it replays challenger arms against the tape the
champion already recorded.

WHY OFFLINE REPLAY RATHER THAN LIVE SHADOW ARMS
───────────────────────────────────────────────
The pod already has a shadow-quote mechanism (`GameBook.shadow_quotes`),
and adding arms there would be the obvious move.  It was rejected: any
code added to `_cycle_book` executes inside the champion's own loop, so a
bug in arm code could raise, change quote timing, or perturb inventory —
contaminating the very sample the gate depends on.  Offline replay has a
structural guarantee instead of a promise: this module imports nothing
from the pod and is never imported by it.

WHY ONLY CONSERVATIVE ARMS ARE EVALUABLE (the key insight)
──────────────────────────────────────────────────────────
The recorded tape contains only prints that crossed the CHAMPION's
quotes — `_check_fills` logs a FILL when a public print goes strictly
through our resting price, and public prints that hit nothing are never
recorded.  Therefore:

  • An arm quoting WIDER than champion (or suppressing quotes entirely
    in some window) fills on a strict SUBSET of the champion's prints.
    Every one of its fills is in the recorded tape → evaluation is EXACT.
  • An arm quoting NARROWER would fill on prints that were never
    recorded → its fill count and markout are unknowable offline.

This is not a limitation we work around — it lines up exactly with the
design doc's asymmetric-authority rule:

    "The system may become MORE conservative on its own authority.
     It may become more aggressive only within pre-registered bounds,
     and never by its own strategy search."

The transformations Tier 2 is permitted to act on autonomously (widening,
post-event suppression) are precisely the ones the recorded tape can
measure without collecting a single new data point or making a single
API call.  Aggressive arms (tighter spreads, higher size, a longer inning
gate, model-calibration shifts that move the quote CENTER) are Tier 3,
require human approval, and are refused by `ArmSpec.validate()`.

MARKOUT ALGEBRA (why the replay is exact, not approximate)
──────────────────────────────────────────────────────────
The pod records `markout_per_contract = sign * (mid_at_horizon - price)`.
For a fill retained by an arm whose quote sits `w` dollars further out:

    bid side (buy,  sign=+1): arm_price = p - w
        arm_markout = +1 * (M - (p - w)) = champ_markout + w
    ask side (sell, sign=-1): arm_price = p + w
        arm_markout = -1 * (M - (p + w)) = champ_markout + w

Both sides gain exactly `w` per contract.  So a widening arm's markout is
recoverable in closed form from the champion's record — no mid
re-lookup, no API call, no approximation.  The arm's cost is the fills it
loses, which is the entire empirical question.

NOTHING IN THIS MODULE WRITES CONFIG, PARAMETERS, OR POD STATE.
The only writer is scripts/maker_challenger_report.py, which writes
markdown proposals to live_agent_research/proposals/ for human review.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from src.kalshi_fees import fee_per_contract

# The gate's horizon.  MARKOUT_HORIZONS in the pod is (60, 300, 900); the
# pre-registered gate metric — and the one the markout-vs-settlement study
# found most robust (74.8% sign agreement vs 50% baseline) — is +5m.
GATE_HORIZON_S = 300

# Cluster bootstrap defaults.  Per CLAUDE.md, backtest CIs cluster by
# event because within-event outcomes correlate; for P-016 the cluster is
# the game.  Seed is fixed so a proposal is reproducible from its text.
BOOTSTRAP_REPS = 20_000
BOOTSTRAP_SEED = 20260721


class NotConservativeError(ValueError):
    """Raised when an arm would quote more aggressively than the champion.

    Such an arm cannot be evaluated from the recorded tape (its fills are
    unobservable) AND it exceeds Tier 2's authority.  Both reasons are
    fatal, so this is an error rather than a warning.
    """


@dataclass(frozen=True)
class ArmSpec:
    """A challenger arm, defined as a conservative TRANSFORM of what the
    champion actually quoted.

    Defining arms relative to the champion's realised quotes — rather than
    recomputing them from parameters — is deliberate.  It makes the subset
    property true by construction and sidesteps inventory divergence: an
    arm with fewer fills would carry different inventory, which feeds
    `inv_skew_coef` and could push a quote NARROWER on one side, breaking
    observability.  "Champion, but further out" has no such feedback path.

    Fields (all dollar amounts, matching the pod's units):
      widen_cents      — uniform outward shift applied to both sides
      event_window_s   — seconds after an OBSERVED state change during
                         which `event_action` applies.  Observed, not
                         actual: the pod's `secs_since_state_change` is
                         measured from when the poll saw the change, which
                         is the operative quantity for our own feed lag.
      event_action     — "suppress" (pull quotes in the window) or
                         "widen" (add `event_widen_cents` in the window)
      event_widen_cents— extra width inside the window when action="widen"
    """

    name: str
    description: str
    widen_cents: float = 0.0
    event_window_s: float = 0.0
    event_action: str = "none"
    event_widen_cents: float = 0.0

    def validate(self) -> None:
        if self.event_action not in ("none", "suppress", "widen"):
            raise ValueError(f"{self.name}: unknown event_action "
                             f"{self.event_action!r}")
        # The direction rule from RECURSIVE_REVIEW_DESIGN.md, enforced in
        # code.  A negative widen is a TIGHTER spread — more aggressive,
        # Tier 3, and unobservable from this tape.
        if self.widen_cents < 0:
            raise NotConservativeError(
                f"{self.name}: widen_cents={self.widen_cents} tightens the "
                "spread. Tightening is Tier 3 (human approval) and its fills "
                "are not in the recorded tape.")
        if self.event_widen_cents < 0:
            raise NotConservativeError(
                f"{self.name}: event_widen_cents={self.event_widen_cents} "
                "tightens the spread inside the event window. Tier 3.")
        if self.event_window_s < 0:
            raise ValueError(f"{self.name}: event_window_s must be >= 0")
        if self.event_action != "none" and self.event_window_s <= 0:
            raise ValueError(
                f"{self.name}: event_action={self.event_action!r} needs a "
                "positive event_window_s")

    def is_champion(self) -> bool:
        """True when the arm is a no-op — the champion itself."""
        return (self.widen_cents == 0.0 and self.event_action == "none")

    def extra_width(self, secs_since_state_change: Optional[float]
                    ) -> Optional[float]:
        """Extra half-width this arm applies to a fill, or None if the arm
        was not quoting at all.

        `None` for secs_since_state_change means the pod could not measure
        staleness (no observed state change yet on that book).  We treat
        that as OUTSIDE the event window: assuming it is inside would
        silently suppress fills on evidence we do not have.
        """
        w = self.widen_cents
        if self.event_action == "none" or secs_since_state_change is None:
            return w
        in_window = secs_since_state_change <= self.event_window_s
        if not in_window:
            return w
        if self.event_action == "suppress":
            return None          # not quoting → no fill possible
        return w + self.event_widen_cents


@dataclass
class ArmFill:
    """One champion fill as seen by an arm, repriced onto the arm's quote."""
    fill_id: str
    game_pk: int
    side: str
    champ_price: float
    arm_price: float
    qty: float
    markout_c: float             # fee-adjusted, cents per contract
    secs_since_state_change: Optional[float]


@dataclass
class ArmResult:
    spec: ArmSpec
    fills: List[ArmFill] = field(default_factory=list)
    # Fills the champion took that this arm did not.  Kept because "what
    # did the arm give up" is the whole question — an arm that improves
    # mean markout by dropping its best fills is not an improvement.
    dropped: List[ArmFill] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.fills)

    @property
    def retention(self) -> float:
        total = len(self.fills) + len(self.dropped)
        return len(self.fills) / total if total else 0.0

    @property
    def mean_markout_c(self) -> float:
        return _mean([f.markout_c for f in self.fills])

    @property
    def games(self) -> int:
        return len({f.game_pk for f in self.fills})

    @property
    def mean_dropped_markout_c(self) -> float:
        return _mean([f.markout_c for f in self.dropped])


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _pstdev(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return float("nan")
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


# ── Tape loading ─────────────────────────────────────────────────────

def build_tape(records: Iterable[dict],
               horizon_s: int = GATE_HORIZON_S,
               include_shadow: bool = False) -> List[dict]:
    """Pair FILL records with their markout at `horizon_s`.

    Shadow fills are excluded by default.  They are counterfactuals for
    states where the champion deliberately did NOT quote (inning gate,
    inventory cap), so folding them in would compare an arm against a
    champion that never existed.  The guardrail-cost question they answer
    is Tier 1's (`maker_diagnostics.py` panel 7), not Tier 2's.
    """
    records = list(records)
    fills = [r for r in records if r.get("type") == "FILL"
             and (include_shadow or not r.get("shadow"))]
    markouts: Dict[str, dict] = {
        r["fill_id"]: r for r in records
        if r.get("type") == "MARKOUT" and r.get("horizon_s") == horizon_s
        and (include_shadow or not r.get("shadow"))
    }
    tape = []
    for f in fills:
        m = markouts.get(f.get("fill_id"))
        if m is None:
            continue          # markout not yet due; not an error
        tape.append({**f, "markout_per_contract": m["markout_per_contract"]})
    return tape


# ── Evaluation ───────────────────────────────────────────────────────

def evaluate_arm(tape: Sequence[dict], spec: ArmSpec) -> ArmResult:
    """Replay one arm against the champion's recorded print tape.

    Exact for conservative arms (see module docstring).  Raises
    NotConservativeError for arms whose fills are unobservable.
    """
    spec.validate()
    result = ArmResult(spec=spec)

    for rec in tape:
        side = rec["side"]                     # "buy" (bid) / "sell" (ask)
        champ_price = float(rec["price"])
        trade_price = rec.get("trade_price")
        ssc = rec.get("secs_since_state_change")
        # Fee-adjust at the CHAMPION's price first, matching the gate
        # convention in scripts/maker_report.py, then re-adjust below at
        # the arm's own price (the fee is price-dependent).
        champ_markout = float(rec["markout_per_contract"])

        extra = spec.extra_width(ssc)
        if extra is None:
            arm_price = champ_price
            arm_markout_c = _fee_adj_cents(champ_markout, champ_price)
            result.dropped.append(_mk(rec, champ_price, arm_markout_c))
            continue

        # Move the quote outward.  Prices are rounded to the cent because
        # that is what the pod posts (`round(..., 2)` in _compute_quotes),
        # and rounding a widened quote can only move it further out or
        # leave it — never inward — so the subset property survives.
        arm_price = (round(champ_price - extra, 2) if side == "buy"
                     else round(champ_price + extra, 2))

        # Guard the invariant rather than trusting the arithmetic.
        if side == "buy" and arm_price > champ_price + 1e-9:
            raise NotConservativeError(
                f"{spec.name}: bid moved inward ({champ_price}->{arm_price})")
        if side == "sell" and arm_price < champ_price - 1e-9:
            raise NotConservativeError(
                f"{spec.name}: ask moved inward ({champ_price}->{arm_price})")

        # Did the same public print still go strictly THROUGH the wider
        # quote?  Identical predicate to the pod's `_check_fills`.
        if trade_price is not None:
            tp = float(trade_price)
            through = (tp < arm_price - 1e-9 if side == "buy"
                       else tp > arm_price + 1e-9)
            if not through:
                arm_markout_c = _fee_adj_cents(champ_markout, champ_price)
                result.dropped.append(_mk(rec, champ_price, arm_markout_c))
                continue

        # Retained.  Widening by `extra` adds exactly `extra` to the
        # per-contract markout on BOTH sides (see module docstring).
        arm_markout_c = _fee_adj_cents(champ_markout + extra, arm_price)
        result.fills.append(_mk(rec, arm_price, arm_markout_c))

    return result


def _fee_adj_cents(markout_per_contract: float, price: float) -> float:
    """Dollars-per-contract markout → fee-adjusted cents.

    P-016 passes no series_ticker, so the general maker rate applies —
    matching the pod's own `_settle` and the gate report.
    """
    return (markout_per_contract - fee_per_contract(price, maker=True)) * 100.0


def _mk(rec: dict, arm_price: float, markout_c: float) -> ArmFill:
    return ArmFill(
        fill_id=rec.get("fill_id", ""),
        game_pk=int(rec.get("game_pk") or 0),
        side=rec["side"],
        champ_price=float(rec["price"]),
        arm_price=arm_price,
        qty=float(rec.get("qty") or 0.0),
        markout_c=markout_c,
        secs_since_state_change=rec.get("secs_since_state_change"),
    )


def verify_subset(champion: ArmResult, arm: ArmResult) -> None:
    """Assert the arm's fills are a subset of the champion's.

    This is the observability guarantee the whole method rests on, so it
    is checked against the data rather than argued from the code.
    """
    champ_ids = {f.fill_id for f in champion.fills}
    extra = {f.fill_id for f in arm.fills} - champ_ids
    if extra:
        raise NotConservativeError(
            f"{arm.spec.name}: {len(extra)} fills outside the champion's "
            f"tape (e.g. {sorted(extra)[:3]}). Arm is not conservative and "
            "its measured fill set is not trustworthy.")


# ── Inference ────────────────────────────────────────────────────────

def cluster_bootstrap_delta(
    champion: ArmResult,
    arm: ArmResult,
    reps: int = BOOTSTRAP_REPS,
    seed: int = BOOTSTRAP_SEED,
    n_arms: int = 1,
) -> Dict[str, float]:
    """Cluster bootstrap (resampling GAMES) of mean(arm) − mean(champion).

    Games, not fills, because settlement and most in-game price shocks are
    one draw per game — the markout-vs-settlement study found the naive
    fill-level CI understates width badly.  Because the arm's fills are a
    subset of the champion's, the two arms share game-level shocks and
    those shocks largely cancel in the difference; this paired structure
    is exactly why the design doc says to run arms in parallel on the same
    tape rather than tuning sequentially over weeks.

    `n_arms` applies a Bonferroni widening to the reported interval — the
    design doc requires "multiple-comparison correction on the number of
    arms run".  With n_arms=1 this is the plain 95% interval.
    """
    by_game_arm: Dict[int, List[float]] = defaultdict(list)
    by_game_champ: Dict[int, List[float]] = defaultdict(list)
    for f in arm.fills:
        by_game_arm[f.game_pk].append(f.markout_c)
    for f in champion.fills:
        by_game_champ[f.game_pk].append(f.markout_c)
    games = sorted(by_game_champ)

    def delta(sample: Sequence[int]) -> Optional[float]:
        a = [x for g in sample for x in by_game_arm.get(g, [])]
        c = [x for g in sample for x in by_game_champ.get(g, [])]
        if not a or not c:
            return None
        return _mean(a) - _mean(c)

    point = delta(games)
    rng = random.Random(seed)
    draws: List[float] = []
    for _ in range(reps):
        sample = [rng.choice(games) for _ in games]
        d = delta(sample)
        if d is not None:
            draws.append(d)
    draws.sort()

    alpha = 0.05 / max(1, n_arms)
    lo_i = int((alpha / 2) * len(draws))
    hi_i = min(len(draws) - 1, int((1 - alpha / 2) * len(draws)))
    return {
        "delta_c": point if point is not None else float("nan"),
        "ci_lo_c": draws[lo_i] if draws else float("nan"),
        "ci_hi_c": draws[hi_i] if draws else float("nan"),
        "p_improve": (sum(1 for d in draws if d > 0) / len(draws)
                      if draws else float("nan")),
        "alpha": alpha,
        "n_games": len(games),
        "reps": len(draws),
    }


def intracluster_correlation(result: ArmResult) -> Tuple[float, float]:
    """(ICC, design effect) of per-fill markout, clustered by game.

    Reported so a reader can convert a raw fill count into an effective
    sample size.  With few games the ICC is poorly identified — the
    markout-vs-settlement study made this point at 5 clusters — so this is
    a diagnostic, never an input to a promotion decision.
    """
    by_game: Dict[int, List[float]] = defaultdict(list)
    for f in result.fills:
        by_game[f.game_pk].append(f.markout_c)
    groups = [v for v in by_game.values() if v]
    k, n = len(groups), sum(len(v) for v in groups)
    if k < 2 or n <= k:
        return float("nan"), float("nan")
    grand = _mean([x for v in groups for x in v])
    msb = sum(len(v) * (_mean(v) - grand) ** 2 for v in groups) / (k - 1)
    msw = sum(sum((x - _mean(v)) ** 2 for x in v) for v in groups) / (n - k)
    n0 = (n - sum(len(v) ** 2 for v in groups) / n) / (k - 1)
    if msb + (n0 - 1) * msw == 0:
        return float("nan"), float("nan")
    icc = (msb - msw) / (msb + (n0 - 1) * msw)
    deff = 1.0 + (n / k - 1) * max(icc, 0.0)
    return icc, deff


def detectable_effect_c(n_fills: float, design_effect: float,
                        sd_c: float, n_arms: int = 1,
                        power: float = 0.80) -> float:
    """Minimum detectable difference in cents at `power`, unpaired.

    Deliberately the UNPAIRED formula: arms sharing a tape are positively
    correlated, so their true paired SE is smaller and this is a
    conservative upper bound on what a given fill count can resolve.
    """
    if n_fills <= 0 or design_effect <= 0 or not math.isfinite(sd_c):
        return float("nan")
    nd = _norm_ppf(1 - (0.05 / max(1, n_arms)) / 2) + _norm_ppf(power)
    return sd_c * math.sqrt(2 * nd * nd / (n_fills / design_effect))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF (Acklam's rational approximation).

    Local rather than scipy: the project's runtime deps are minimal and
    this is the only place an inverse CDF is needed.
    """
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p <= 0 or p >= 1:
        return float("nan")
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


# ── Pre-registered arm set ───────────────────────────────────────────
#
# Three arms, per the design doc's rule: "few arms, each a coarse
# theory-motivated contrast ... never a fine grid sweep."  Each maps to a
# mechanism the doc names explicitly, and every one is conservative, so
# all three sit inside Tier 2's authority AND inside the observable tape.
#
# NOTE ON BOUNDS: RECURSIVE_REVIEW_DESIGN.md requires that "every
# auto-adjustable parameter gets a hard [min, max] in config" but never
# records the numbers.  No numeric bounds are invented here.  The only
# bound this module enforces is the one the doc does state — the
# direction rule (conservative-only), checked in ArmSpec.validate() and
# again in verify_subset().  Numeric [min, max] bounds must be set by a
# human before any auto-apply layer is built; none exists today.

CHAMPION_ARM = ArmSpec(
    name="champion",
    description="P-016 as deployed. Frozen; the only arm the gate judges.",
)

DEFAULT_ARMS: Tuple[ArmSpec, ...] = (
    ArmSpec(
        name="postevent_suppress_15s",
        description=(
            "Glosten-Milgrom: stop quoting for 15s after an OBSERVED game "
            "state change, where our StatsAPI feed lag concentrates "
            "informed flow. The doc's named response to a negative "
            "low-staleness markout bucket."),
        event_window_s=15.0,
        event_action="suppress",
    ),
    ArmSpec(
        name="postevent_widen_30s",
        description=(
            "Same mechanism, softer instrument: widen by 1c for 30s after "
            "an observed state change instead of going dark. Tests whether "
            "the pick-off is priceable rather than only avoidable."),
        event_window_s=30.0,
        event_action="widen",
        event_widen_cents=0.01,
    ),
    ArmSpec(
        name="base_widen_1c",
        description=(
            "The doc's 'wide vs narrow base' contrast — wide side only, "
            "since the narrow side is both Tier 3 and unobservable. "
            "Uniform +1c half-width, no event conditioning."),
        widen_cents=0.01,
    ),
)


def arms_from_config(config: Optional[dict]) -> Tuple[ArmSpec, ...]:
    """Read arms from `pods.P-016.challenger.arms`, else DEFAULT_ARMS.

    Behaviour-preserving by default: the key does not exist in
    config_multi_pod.yaml and nothing writes it.  The champion's
    `from_config` reads only `quoting` and `risk`, so a `challenger`
    block is inert for the champion even if one is added later.
    """
    if not config:
        return DEFAULT_ARMS
    block = (((config.get("pods") or {}).get("P-016") or {})
             .get("challenger") or {})
    raw = block.get("arms")
    if not raw:
        return DEFAULT_ARMS
    specs = []
    for item in raw:
        spec = ArmSpec(
            name=item["name"],
            description=item.get("description", ""),
            widen_cents=float(item.get("widen_cents", 0.0)),
            event_window_s=float(item.get("event_window_s", 0.0)),
            event_action=item.get("event_action", "none"),
            event_widen_cents=float(item.get("event_widen_cents", 0.0)),
        )
        spec.validate()
        specs.append(spec)
    return tuple(specs)
