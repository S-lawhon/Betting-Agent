"""
tests/test_order_flow.py
────────────────────────
Unit tests for src/order_flow.py — VPIN + rolling one-sidedness on
synthetic print streams.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.order_flow import OrderFlowTracker, classify


def _prints(*pairs):
    """pairs of (taker_side, count) → trades_since-shaped dicts."""
    return [{"taker_side": s, "count": c} for s, c in pairs]


# ── Classification ───────────────────────────────────────────────────

def test_classify():
    assert classify("yes") == "buy"
    assert classify("no") == "sell"
    assert classify("") is None
    assert classify(None) is None
    assert classify("maybe") is None


# ── Cold start ───────────────────────────────────────────────────────

def test_vpin_none_until_bucket_closes():
    t = OrderFlowTracker(bucket_size=50, n_buckets=20, roll_window=200)
    assert t.vpin is None
    assert t.one_sidedness is None
    t.update(_prints(("yes", 10)))     # 10 < bucket_size, no closed bucket
    assert t.vpin is None
    # one_sidedness is live as soon as there is any classified volume
    assert t.one_sidedness == 1.0


def test_unclassifiable_prints_ignored():
    t = OrderFlowTracker(bucket_size=10, n_buckets=5, roll_window=100)
    t.update(_prints(("", 100), (None, 100), ("junk", 100)))
    assert t.vpin is None
    assert t.one_sidedness is None
    # zero / negative counts ignored too
    t.update(_prints(("yes", 0), ("no", -5)))
    assert t.one_sidedness is None


# ── Fully one-sided flow is maximally toxic ──────────────────────────

def test_all_buys_is_fully_toxic():
    t = OrderFlowTracker(bucket_size=50, n_buckets=20, roll_window=200)
    t.update(_prints(("yes", 50), ("yes", 50), ("yes", 50)))
    assert t.vpin == 1.0            # every closed bucket is 100% one side
    assert t.one_sidedness == 1.0


def test_perfectly_balanced_flow_is_zero():
    t = OrderFlowTracker(bucket_size=100, n_buckets=20, roll_window=400)
    # each bucket of 100 gets 50 buy + 50 sell → imbalance 0
    for _ in range(4):
        t.update(_prints(("yes", 50), ("no", 50)))
    assert t.vpin == 0.0
    assert t.one_sidedness == 0.0


# ── Bucket boundary splitting ────────────────────────────────────────

def test_print_straddling_bucket_boundary():
    # bucket_size 50; a single 120-buy print fills 2 full buckets (100)
    # and leaves 20 in a partial bucket.
    t = OrderFlowTracker(bucket_size=50, n_buckets=20, roll_window=500)
    t.update(_prints(("yes", 120)))
    # two closed buckets, both fully one-sided
    assert t.vpin == 1.0
    # add a sell that closes the third bucket balanced-ish: partial has
    # 20 buy; add 30 sell → bucket = 20 buy / 30 sell, imbalance 0.2
    t.update(_prints(("no", 30)))
    # three closed buckets: 1.0, 1.0, 0.2 → mean 0.7333...
    assert abs(t.vpin - (1.0 + 1.0 + 0.2) / 3.0) < 1e-9


# ── VPIN window only keeps last n_buckets ────────────────────────────

def test_vpin_windowed_to_n_buckets():
    t = OrderFlowTracker(bucket_size=10, n_buckets=2, roll_window=1000)
    # 3 fully one-sided buckets; window keeps only last 2 → still 1.0
    t.update(_prints(("yes", 10), ("no", 10), ("yes", 10)))
    assert t.vpin == 1.0
    # now push a balanced bucket (5 buy / 5 sell)
    t.update(_prints(("yes", 5), ("no", 5)))
    # last 2 buckets: [one-sided 1.0, balanced 0.0] → mean 0.5
    assert abs(t.vpin - 0.5) < 1e-9


# ── Rolling one-sidedness trims to roll_window contracts ─────────────

def test_one_sidedness_rolls_off_old_volume():
    t = OrderFlowTracker(bucket_size=1000, n_buckets=20, roll_window=100)
    # 100 buys → fully one-sided over the window
    t.update(_prints(("yes", 100)))
    assert t.one_sidedness == 1.0
    # 100 sells → window now holds last 100 contracts = all sells
    t.update(_prints(("no", 100)))
    assert abs(t.one_sidedness - 1.0) < 1e-9
    # a balanced follow-up: 50 buy 50 sell, window = last 100 = that batch
    t.update(_prints(("yes", 50), ("no", 50)))
    assert abs(t.one_sidedness - 0.0) < 1e-9


def test_one_sidedness_partial_rolloff():
    # roll_window 100: push 60 buy then 60 sell → total 120, trims 20 of
    # the oldest (buy) → window holds 40 buy + 60 sell.
    t = OrderFlowTracker(bucket_size=1000, n_buckets=20, roll_window=100)
    t.update(_prints(("yes", 60)))
    t.update(_prints(("no", 60)))
    # 40 buy, 60 sell → |40-60|/100 = 0.2
    assert abs(t.one_sidedness - 0.2) < 1e-9


# ── Config validation ────────────────────────────────────────────────

def test_bad_config_rejected():
    for kw in ({"bucket_size": 0}, {"n_buckets": 0}, {"roll_window": -1}):
        try:
            OrderFlowTracker(**kw)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kw}")
