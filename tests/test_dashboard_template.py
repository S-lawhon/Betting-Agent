"""
tests/test_dashboard_template.py
────────────────────────────────
Static checks on ``src/templates/dashboard_v2.html``. No browser required.

The important one is ``test_no_fabricated_zero_fallbacks``. The previous
dashboard did this at ``dashboard.html:879``::

    const bankroll = (d.risk && d.risk.bankroll) ? d.risk.bankroll : 10000;

so whenever the risk snapshot was absent, ROI, Sharpe, drawdown and
return-on-bankroll all rendered — confidently — against a bankroll that did not
exist. That is the JavaScript form of the fabricated default that
``docs/GATE_INSTRUMENTATION_STANDARD.md`` §3 forbids, and it is a large part of
why the old dashboard did not feel trustworthy.

This file is the ratchet that stops it coming back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATE = (Path(__file__).resolve().parent.parent
            / "src" / "templates" / "dashboard_v2.html")


@pytest.fixture(scope="module")
def html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def code_lines(html: str):
    """Lines excluding the block-comment prose, so docs can discuss `|| 0`."""
    out, in_comment = [], False
    for i, line in enumerate(html.split("\n"), start=1):
        stripped = line.strip()
        if stripped.startswith("<!--"):
            in_comment = True
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        # JS block comments used for section headers and rationale
        if stripped.startswith("/*") or stripped.startswith("*"):
            continue
        if stripped.startswith("//"):
            continue
        out.append((i, line))
    return out


# ── the ratchet ───────────────────────────────────────────────────────


FABRICATED = [
    (r"\|\|\s*0\b", "|| 0"),
    (r"\?\?\s*0\b", "?? 0"),
    (r"""\|\|\s*['"]0""", "|| '0'"),
    (r"""\?\?\s*['"]0""", "?? '0'"),
    (r"\|\|\s*10000\b", "|| 10000"),
]


def test_no_fabricated_zero_fallbacks(html):
    """A missing number must render as unknown, never as zero.

    If this fails, use numCell()/numTxt()/unk() instead of a `|| 0` fallback.
    """
    bad = []
    for lineno, line in code_lines(html):
        for pattern, label in FABRICATED:
            if re.search(pattern, line):
                bad.append("line %d uses %s: %s" % (lineno, label, line.strip()))
    assert not bad, (
        "fabricated-zero fallback(s) reintroduced — see "
        "docs/GATE_INSTRUMENTATION_STANDARD.md §3:\n  " + "\n  ".join(bad))


def test_the_unknown_helpers_exist(html):
    for fn in ("function unk(", "function numCell(", "function numTxt(",
               "function isNum("):
        assert fn in html, "missing %s — the unknown-state contract needs it" % fn


def test_crossvenue_research_has_a_dashboard_surface(html):
    assert 'id="r-crossvenue"' in html
    assert "r.crossvenue_pilot" in html
    assert "ProphetX sandbox" in html
    assert "cvPxProduction.rollout_ready" in html
    assert "quote completeness" in html
    assert "persistent episodes" in html
    assert "research alerts" in html
    assert "venue expansion" in html
    assert 'id="r-cases"' in html
    assert "cv.research_cases" in html
    assert "Settlement basis" in html
    assert "observed exception lower bound" in html
    assert "cvCases.outcome_evidence" in html


def test_isnum_rejects_non_finite(html):
    assert 'typeof v === "number" && isFinite(v)' in html, (
        "isNum must reject NaN/Infinity, not just non-numbers")


# ── self-containment (no CDN, offline, behind Caddy) ──────────────────


def test_no_external_scripts_or_stylesheets(html):
    assert "<script src=" not in html
    assert not re.search(r"<link[^>]+stylesheet", html)
    assert not re.search(r'\b(?:src|href)\s*=\s*["\']https?://', html), (
        "no external asset may be referenced — the box may have no egress")


def test_no_web_font_or_import(html):
    assert "@import" not in html
    assert "fonts.googleapis" not in html


def test_no_browser_storage(html):
    for api in ("localStorage", "sessionStorage", "indexedDB"):
        assert api not in html, "%s is not available in this context" % api


# ── the v1 contract on the served page ───────────────────────────────


def test_title_string_is_present(html):
    """tests/test_web_dashboard.py asserts GET / contains these bytes."""
    assert "BETTING POD SHOP" in html


def test_poll_token_is_present_for_substitution(html):
    assert "__POLL_MS__" in html
    assert "var POLL = __POLL_MS__;" in html


# ── the paper ribbon ──────────────────────────────────────────────────


def test_ribbon_has_all_three_states(html):
    assert 'id="ribbon"' in html
    for cls in ("#ribbon.paper", "#ribbon.unknown", "#ribbon.live"):
        assert cls in html, "missing CSS for %s" % cls
    for state in ('className = "paper"', 'className = "unknown"',
                  'className = "live"'):
        assert state in html, "missing JS for %s" % state


def test_ribbon_never_silently_disappears(html):
    """A missing invariant must render MODE UNKNOWN, not nothing.

    The ribbon vanishing is indistinguishable from the ribbon saying "paper",
    and only one of those is safe.
    """
    assert "MODE UNKNOWN" in html
    assert "PAPER — NO REAL CAPITAL AT RISK" in html
    assert "LIVE CAPITAL" in html


def test_paper_values_are_prefixed(html):
    assert 'p$' in html, "paper currency must be visibly distinguished"
    assert "paper_usd" in html or "unit_prefix" in html or 'unit || "p$"' in html


# ── responsive ────────────────────────────────────────────────────────


def test_has_both_breakpoints(html):
    assert "@media(max-width:640px)" in html, "mobile bottom nav breakpoint"
    assert "@media(max-width:520px)" in html, "table reflow breakpoint"


def test_tables_reflow_via_data_label(html):
    assert "content:attr(data-label)" in html
    assert 'data-label="' in html, "cells need labels for the reflow to work"


def test_research_operations_shows_agent_queues_and_execution_semantics(html):
    assert 'id="r-agents"' in html
    assert 'id="r-day"' in html
    assert 'id="r-health"' in html
    assert 'id="r-quality"' in html
    assert "Worker claims/start state" in html
    assert "model invocation" in html
    assert "academic_feed_items_raw" in html
    assert "expected_empty_academic_feeds" in html
    assert "legacy_dispatches_quarantined" in html
    for field in ("dispatched_24h", "reviewed_24h", "advance_24h",
                  "started_24h", "in_progress", "research_minutes_24h",
                  "oldest_pending_age_hours"):
        assert field in html


def test_reduced_motion_is_respected(html):
    assert "prefers-reduced-motion" in html


def test_viewport_meta_present(html):
    assert 'name="viewport"' in html


# ── dataviz house rules ───────────────────────────────────────────────


def test_no_dashed_gridlines(html):
    """Dashing reads as "projection" or "threshold" when it is just a grid."""
    assert "setLineDash" not in html


def test_equity_line_is_two_px_and_single_hue(html):
    assert "g.lineWidth = 2" in html
    assert 'g.strokeStyle = "#58a6ff"' in html, (
        "a single series must not change colour mid-stroke — that reads as two")


def test_equity_area_is_split_at_zero(html):
    """Colouring the whole area by the final value paints profitable stretches
    red, which states the opposite of the data."""
    assert 'wash("#3fb950"' in html
    assert 'wash("#f85149"' in html


def test_axis_ticks_are_snapped_to_clean_numbers(html):
    assert "function niceStep(" in html


def test_chart_has_a_table_view_twin(html):
    """A tooltip must never be the only way to read a value."""
    assert 'id="eq-table"' in html
    assert 'id="eq-rows"' in html


def test_unmeasured_meter_is_hatched_not_empty(html):
    """An empty progress bar reads as zero; unmeasured is not zero."""
    assert ".meter.unmeasured" in html
    assert "repeating-linear-gradient" in html


def test_funnel_bars_share_one_hue(html):
    """The funnel's order is carried by position, not by 8 lightness steps —
    which cannot pass the adjacent-ΔL gate on a dark surface anyway."""
    assert ".fnbar>i{" in html
    assert "background:var(--accent)" in html


def test_signed_values_carry_an_explicit_sign(html):
    """Polarity must not depend on red-vs-green alone (deuteranopia)."""
    assert 'var s = v < 0 ? "−" : "+";' in html


# ── wiring ────────────────────────────────────────────────────────────


def test_every_referenced_element_id_exists(html):
    declared = set(re.findall(r'\bid="([^"]+)"', html))
    referenced = set(re.findall(r'el\("([^"]+)"\)', html))
    referenced |= set(re.findall(r'getElementById\("([^"]+)"\)', html))
    # ids built dynamically (setDot("dot-now") etc.) resolve through el() too
    referenced |= set(re.findall(r'setDot\("([^"]+)"', html))
    missing = sorted(referenced - declared)
    assert not missing, "JS references ids that do not exist: %s" % missing


def test_view_sections_exist_for_every_tab(html):
    tabs = set(re.findall(r'data-view="([^"]+)"', html))
    declared = set(re.findall(r'\bid="v-([^"]+)"', html))
    assert tabs, "no tabs found"
    assert tabs <= declared, "tabs without a section: %s" % sorted(tabs - declared)
    views = re.search(r'var VIEWS = \[([^\]]+)\]', html)
    assert views
    listed = set(re.findall(r'"([^"]+)"', views.group(1)))
    assert listed == tabs, "VIEWS and the tab strip disagree: %s vs %s" % (
        sorted(listed), sorted(tabs))


def test_polls_the_v2_endpoint_with_etag_revalidation(html):
    assert 'fetch("/api/v2/dashboard"' in html
    assert "If-None-Match" in html
    assert "res.status === 304" in html


def test_polling_pauses_when_hidden(html):
    assert "visibilitychange" in html
    assert "document.hidden" in html
    assert "clearInterval" in html


def test_refetch_holds_the_previous_render(html):
    """No skeleton flash: hold the old render at reduced opacity."""
    assert "main.refetching" in html or ".refetching" in html
    assert 'classList.add("refetching")' in html


def test_manager_tab_is_an_iframe_not_a_js_port(html):
    """manager/brief.py is the alerting path's renderer; duplicating it in JS
    would let the page and the pager disagree."""
    assert '<iframe' in html
    assert 'el("mgr").src = "/manager"' in html


def test_html_escaping_helper_is_used_for_payload_text(html):
    assert "function esc(" in html
    assert 'replace(/&/g,"&amp;")' in html


def test_fetch_failure_is_surfaced_not_swallowed(html):
    assert "data unreachable" in html
