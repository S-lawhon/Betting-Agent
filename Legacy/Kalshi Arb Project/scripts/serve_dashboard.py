#!/usr/bin/env python3
"""
scripts/serve_dashboard.py
──────────────────────────
Start a local web server and open the betting agent dashboard in your browser.

Usage
─────
    python scripts/serve_dashboard.py              # default port 8765
    python scripts/serve_dashboard.py --port 8080
    python scripts/serve_dashboard.py --no-open    # don't auto-open browser

No third-party dependencies — stdlib only (http.server, json, csv, yaml).
Stop the server with Ctrl+C.
"""
from __future__ import annotations

import csv
import json
import sys
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Timer

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Embedded HTML ─────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Betting Agent Dashboard</title>
<style>
  :root {
    --bg:      #0f172a;
    --surface: #1e293b;
    --raised:  #273449;
    --border:  #334155;
    --text:    #e2e8f0;
    --muted:   #94a3b8;
    --green:   #22c55e;
    --red:     #ef4444;
    --yellow:  #f59e0b;
    --blue:    #38bdf8;
    --radius:  10px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text);
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         font-size: 14px; min-height: 100vh; }

  /* ── Header ── */
  header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 14px 24px;
    display: flex; align-items: center; justify-content: space-between;
    position: sticky; top: 0; z-index: 10;
  }
  header h1 { font-size: 16px; font-weight: 700; letter-spacing: .04em; }
  .badge {
    display: inline-block; padding: 2px 10px; border-radius: 999px;
    font-size: 11px; font-weight: 700; letter-spacing: .06em;
    text-transform: uppercase; margin-left: 10px; vertical-align: middle;
  }
  .badge-paper  { background: #0e4066; color: var(--blue); }
  .badge-live   { background: #4c1414; color: var(--red);  }
  .header-right { display: flex; align-items: center; gap: 14px; }
  .refresh-info { color: var(--muted); font-size: 12px; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
         background: var(--green); margin-right: 5px;
         animation: pulse 2s ease-in-out infinite; }
  @keyframes pulse {
    0%,100% { opacity: 1; } 50% { opacity: .3; }
  }
  button#refreshBtn {
    background: var(--raised); border: 1px solid var(--border);
    color: var(--text); padding: 5px 12px; border-radius: 6px;
    cursor: pointer; font-size: 12px;
  }
  button#refreshBtn:hover { background: var(--border); }

  /* ── Layout ── */
  main { padding: 20px 24px; max-width: 1200px; margin: 0 auto; }
  section { margin-bottom: 24px; }
  section h2 { font-size: 12px; font-weight: 600; letter-spacing: .08em;
               text-transform: uppercase; color: var(--muted);
               margin-bottom: 12px; padding-left: 2px; }

  /* ── Stat cards ── */
  .cards { display: grid; gap: 14px;
           grid-template-columns: repeat(auto-fill, minmax(175px, 1fr)); }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 16px 18px;
  }
  .card.warning { border-color: var(--yellow); background: #2a1e06; }
  .card-label { font-size: 11px; color: var(--muted);
                text-transform: uppercase; letter-spacing: .06em; margin-bottom: 6px; }
  .card-value { font-size: 22px; font-weight: 700; line-height: 1.2; }
  .card-sub   { font-size: 12px; color: var(--muted); margin-top: 4px; }

  /* ── Tables ── */
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; }
  thead th {
    background: var(--raised); color: var(--muted);
    font-size: 11px; font-weight: 600; letter-spacing: .06em;
    text-transform: uppercase; padding: 8px 12px;
    border-bottom: 1px solid var(--border); text-align: left;
  }
  tbody tr { border-bottom: 1px solid var(--border); transition: background .1s; }
  tbody tr:last-child { border-bottom: none; }
  tbody tr:hover { background: var(--raised); }
  tbody td { padding: 9px 12px; vertical-align: middle; }
  .empty-row td {
    text-align: center; color: var(--muted); padding: 28px;
    font-style: italic;
  }

  /* ── Value colours ── */
  .green  { color: var(--green); }
  .red    { color: var(--red);   }
  .yellow { color: var(--yellow);}
  .blue   { color: var(--blue);  }
  .muted  { color: var(--muted); }

  /* ── Pill badges ── */
  .pill {
    display: inline-block; padding: 2px 8px; border-radius: 999px;
    font-size: 11px; font-weight: 600;
  }
  .pill-win     { background:#14532d; color:var(--green);  }
  .pill-loss    { background:#4c1414; color:var(--red);    }
  .pill-pending { background:#1e3a5f; color:var(--blue);   }
  .pill-void    { background:#2d2d2d; color:var(--muted);  }
  .pill-placed  { background:#14532d; color:var(--green);  }
  .pill-skipped { background:#3b2c08; color:var(--yellow); }
  .pill-error   { background:#4c1414; color:var(--red);    }

  /* ── Learner / stat row ── */
  .stat-grid { display: grid; gap: 12px;
               grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }
  .stat-item { background: var(--surface); border: 1px solid var(--border);
               border-radius: var(--radius); padding: 14px 16px; }
  .stat-label { font-size: 11px; color: var(--muted);
                text-transform: uppercase; letter-spacing: .06em; margin-bottom: 4px; }
  .stat-value { font-size: 16px; font-weight: 600; }

  /* ── Loading overlay ── */
  #loading {
    position: fixed; inset: 0; background: var(--bg);
    display: flex; align-items: center; justify-content: center;
    font-size: 15px; color: var(--muted); z-index: 100;
  }
  #loading.hidden { display: none; }

  /* ── Error banner ── */
  #errorBanner {
    background: #4c1414; color: var(--red); border: 1px solid #7f1d1d;
    border-radius: 8px; padding: 10px 16px; margin-bottom: 16px;
    display: none;
  }

  /* ── P&L Chart ── */
  .chart-wrap {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 16px 18px;
    position: relative;
  }
  #pnlCanvas { width: 100%; height: 200px; display: block; cursor: crosshair; }
  #chartTooltip {
    position: absolute; top: 16px; right: 18px;
    background: var(--raised); border: 1px solid var(--border);
    border-radius: 6px; padding: 6px 10px; font-size: 12px;
    pointer-events: none; display: none; text-align: right;
    min-width: 140px;
  }
</style>
</head>
<body>

<div id="loading">Loading dashboard…</div>

<header>
  <div>
    <h1>BETTING AGENT DASHBOARD<span id="modeBadge" class="badge badge-paper">paper</span></h1>
  </div>
  <div class="header-right">
    <span class="refresh-info">
      <span class="dot"></span>
      <span id="lastUpdated">—</span> &nbsp;·&nbsp; refresh in <span id="countdown">10</span>s
    </span>
    <button id="refreshBtn" onclick="fetchData()">↻ Refresh</button>
  </div>
</header>

<main>
  <div id="errorBanner"></div>

  <!-- ── Account cards ── -->
  <section>
    <h2>Account</h2>
    <div class="cards" id="accountCards">
      <div class="card"><div class="card-label">Bankroll</div>
        <div class="card-value" id="bankroll">—</div>
        <div class="card-sub" id="peakEquity">peak —</div></div>

      <div class="card"><div class="card-label">Daily P&amp;L</div>
        <div class="card-value" id="dailyPnl">—</div>
        <div class="card-sub" id="dailyPnlPct">— %</div></div>

      <div class="card"><div class="card-label">Exposure</div>
        <div class="card-value" id="exposure">—</div>
        <div class="card-sub" id="exposurePct">— %</div></div>

      <div class="card"><div class="card-label">Drawdown</div>
        <div class="card-value" id="drawdown">—</div>
        <div class="card-sub" id="drawdownPct">— %</div></div>

      <div class="card" id="cooldownCard">
        <div class="card-label">Cooldown</div>
        <div class="card-value" id="cooldownVal">—</div>
        <div class="card-sub" id="cooldownUntil"></div></div>
    </div>
  </section>

  <!-- ── Equity curve ── -->
  <section>
    <h2>Equity Curve</h2>
    <div class="chart-wrap">
      <canvas id="pnlCanvas"></canvas>
      <div id="chartTooltip"></div>
    </div>
  </section>

  <!-- ── Open positions ── -->
  <section>
    <h2>Open Positions (<span id="nPositions">0</span>)</h2>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Event</th><th>Side</th><th>Size (USD)</th>
          <th>Fill Price</th><th>Opened (UTC)</th>
        </tr></thead>
        <tbody id="positionsBody">
          <tr class="empty-row"><td colspan="5">No open positions.</td></tr>
        </tbody>
      </table>
    </div>
  </section>

  <!-- ── Recent trades ── -->
  <section>
    <h2>Recent Trades (last 20)</h2>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Time (UTC)</th><th>Event</th><th>Side</th>
          <th>Action</th><th>Edge</th><th>EV</th>
          <th>Size (USD)</th><th>Outcome</th><th>P&amp;L</th>
        </tr></thead>
        <tbody id="tradesBody">
          <tr class="empty-row"><td colspan="9">No trades logged yet.</td></tr>
        </tbody>
      </table>
    </div>
  </section>

  <!-- ── Edge history summary ── -->
  <section>
    <h2>All-time Edge History</h2>
    <div class="stat-grid" id="edgeStats">
      <div class="stat-item"><div class="stat-label">Trades</div>
        <div class="stat-value" id="ehTrades">—</div></div>
      <div class="stat-item"><div class="stat-label">Win Rate</div>
        <div class="stat-value" id="ehWinRate">—</div></div>
      <div class="stat-item"><div class="stat-label">Total P&amp;L</div>
        <div class="stat-value" id="ehPnl">—</div></div>
      <div class="stat-item"><div class="stat-label">Avg Edge</div>
        <div class="stat-value" id="ehAvgEdge">—</div></div>
    </div>
  </section>

  <!-- ── Learner ── -->
  <section>
    <h2>Learner / Calibration</h2>
    <div class="stat-grid" id="learnerStats">
      <div class="stat-item"><div class="stat-label">Records</div>
        <div class="stat-value" id="lrRecords">—</div></div>
      <div class="stat-item"><div class="stat-label">Global Bias</div>
        <div class="stat-value" id="lrBias">—</div></div>
      <div class="stat-item"><div class="stat-label">Model</div>
        <div class="stat-value" id="lrModel">—</div></div>
      <div class="stat-item"><div class="stat-label">Last Updated</div>
        <div class="stat-value" id="lrUpdated">—</div></div>
    </div>
  </section>
</main>

<script>
const fmt = {
  usd:  v => (v >= 0 ? '+' : '') + '$' + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}),
  usdAbs: v => '$' + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}),
  pct:  v => (v >= 0 ? '+' : '') + (v*100).toFixed(2) + '%',
  pctAbs: v => (Math.abs(v)*100).toFixed(2) + '%',
  edge: v => (v*100).toFixed(1) + '%',
  ev:   v => v.toFixed(4),
  ts:   s => s ? s.replace('T',' ').slice(0,19) : '—',
};

function colorPnl(v, el) {
  el.textContent = fmt.usd(v);
  el.className   = 'card-value ' + (v > 0 ? 'green' : v < 0 ? 'red' : '');
}

function pillHtml(label) {
  if (!label) return '<span class="muted">—</span>';
  const l = label.toUpperCase();
  const cls = l === 'WIN'     ? 'pill-win'
            : l === 'LOSS'    ? 'pill-loss'
            : l === 'PENDING' ? 'pill-pending'
            : l === 'VOID'    ? 'pill-void'
            : l === 'PLACED' || l === 'EXECUTED' ? 'pill-placed'
            : l.startsWith('SKIPPED') ? 'pill-skipped'
            : l === 'ERROR'   ? 'pill-error'
            : '';
  return `<span class="pill ${cls}">${label}</span>`;
}

function update(d) {
  // Header
  const badge = document.getElementById('modeBadge');
  badge.textContent = d.mode;
  badge.className   = 'badge ' + (d.mode === 'live' ? 'badge-live' : 'badge-paper');
  document.getElementById('lastUpdated').textContent = fmt.ts(d.as_of) + ' UTC';

  // Bankroll
  document.getElementById('bankroll').textContent = fmt.usdAbs(d.bankroll);
  document.getElementById('peakEquity').textContent = 'peak ' + fmt.usdAbs(d.peak_equity);

  // Daily P&L
  colorPnl(d.daily_pnl_usd, document.getElementById('dailyPnl'));
  document.getElementById('dailyPnlPct').textContent = fmt.pct(d.daily_pnl_pct);

  // Exposure
  document.getElementById('exposure').textContent = fmt.usdAbs(d.exposure_usd);
  document.getElementById('exposurePct').textContent = fmt.pctAbs(d.exposure_pct) + ' of bankroll';

  // Drawdown
  const ddEl = document.getElementById('drawdown');
  ddEl.textContent = fmt.usd(-Math.abs(d.drawdown_usd));
  ddEl.className   = 'card-value ' + (d.drawdown_usd > 0 ? 'red' : '');
  document.getElementById('drawdownPct').textContent = fmt.pctAbs(d.drawdown_pct);

  // Cooldown
  const cd     = document.getElementById('cooldownCard');
  const cdVal  = document.getElementById('cooldownVal');
  const cdUntil= document.getElementById('cooldownUntil');
  if (d.in_cooldown) {
    cd.className       = 'card warning';
    cdVal.className    = 'card-value yellow';
    cdVal.textContent  = 'ACTIVE';
    cdUntil.textContent= 'until ' + fmt.ts(d.cooldown_until);
  } else {
    cd.className       = 'card';
    cdVal.className    = 'card-value green';
    cdVal.textContent  = 'No';
    cdUntil.textContent= '';
  }

  // Positions
  document.getElementById('nPositions').textContent = d.positions.length;
  const pb = document.getElementById('positionsBody');
  if (d.positions.length === 0) {
    pb.innerHTML = '<tr class="empty-row"><td colspan="5">No open positions.</td></tr>';
  } else {
    pb.innerHTML = d.positions.map(p => `<tr>
      <td>
        <div class="blue">${p.event || p.ticker}</div>
        <div class="muted" style="font-size:11px;margin-top:2px">${p.event && p.event !== p.ticker ? p.ticker : ''}</div>
      </td>
      <td>${p.side}</td>
      <td>${fmt.usdAbs(p.size_usd)}</td>
      <td>${p.fill_price != null ? (p.fill_price*100).toFixed(1)+'¢' : '—'}</td>
      <td class="muted">${fmt.ts(p.opened_at)}</td>
    </tr>`).join('');
  }

  // Trades
  const tb = document.getElementById('tradesBody');
  if (d.recent_trades.length === 0) {
    tb.innerHTML = '<tr class="empty-row"><td colspan="9">No trades logged yet.</td></tr>';
  } else {
    tb.innerHTML = d.recent_trades.map(t => {
      const pnl = t.pnl_usd != null
        ? `<span class="${t.pnl_usd>0?'green':t.pnl_usd<0?'red':''}">${fmt.usd(t.pnl_usd)}</span>`
        : '<span class="muted">—</span>';
      const eventLabel = t.event || t.market_ticker || '—';
      const showTicker = t.event && t.market_ticker && t.event !== t.market_ticker;
      return `<tr>
        <td class="muted">${fmt.ts(t.timestamp)}</td>
        <td>
          <div class="blue">${eventLabel}</div>
          ${showTicker ? `<div class="muted" style="font-size:11px;margin-top:2px">${t.market_ticker}</div>` : ''}
        </td>
        <td>${t.side || '—'}</td>
        <td>${pillHtml(t.action)}</td>
        <td>${t.edge_pct != null ? fmt.edge(t.edge_pct) : '—'}</td>
        <td>${t.ev != null ? fmt.ev(t.ev) : '—'}</td>
        <td>${t.position_size_usd != null ? fmt.usdAbs(t.position_size_usd) : '—'}</td>
        <td>${pillHtml(t.outcome)}</td>
        <td>${pnl}</td>
      </tr>`;
    }).join('');
  }

  // Edge history
  const eh = d.edge_history;
  document.getElementById('ehTrades').textContent   = eh.n_trades;
  const wrEl = document.getElementById('ehWinRate');
  wrEl.textContent = eh.n_trades > 0 ? fmt.pctAbs(eh.win_rate) : '—';
  wrEl.className = 'stat-value ' + (eh.win_rate > 0.5 ? 'green' : eh.win_rate > 0 && eh.win_rate < 0.5 ? 'red' : '');
  const pnlEl = document.getElementById('ehPnl');
  pnlEl.textContent = eh.n_trades > 0 ? fmt.usd(eh.total_pnl_usd) : '—';
  pnlEl.className   = 'stat-value ' + (eh.total_pnl_usd > 0 ? 'green' : eh.total_pnl_usd < 0 ? 'red' : '');
  document.getElementById('ehAvgEdge').textContent  = eh.n_trades > 0 ? fmt.edge(eh.avg_edge_pct) : '—';

  // Equity chart
  drawChart(d.equity_curve, d.initial_bankroll);

  // Learner
  const lr = d.learner;
  document.getElementById('lrRecords').textContent = lr.n_records;
  const biasEl = document.getElementById('lrBias');
  biasEl.textContent = lr.n_records > 0 ? (lr.global_bias >= 0 ? '+' : '') + lr.global_bias.toFixed(4) : '—';
  biasEl.className   = 'stat-value ' + (lr.global_bias > 0.005 ? 'green' : lr.global_bias < -0.005 ? 'red' : '');
  document.getElementById('lrModel').textContent   = lr.trained ? 'Logistic' : lr.n_records > 0 ? 'Bias-only' : 'No data';
  document.getElementById('lrModel').className     = 'stat-value ' + (lr.trained ? 'green' : 'muted');
  document.getElementById('lrUpdated').textContent = lr.last_updated ? fmt.ts(lr.last_updated) : '—';
}

// ── Equity Chart ────────────────────────────────────────────────────────────
let _chartPoints = [];
let _chartInitialBk = 10000;

function drawChart(points, initialBk) {
  _chartPoints  = points  || [];
  _chartInitialBk = initialBk || 10000;
  _renderChart();
}

function _renderChart() {
  const canvas = document.getElementById('pnlCanvas');
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  if (rect.width === 0) { requestAnimationFrame(_renderChart); return; }

  const dpr = window.devicePixelRatio || 1;
  canvas.width  = rect.width  * dpr;
  canvas.height = rect.height * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const W = rect.width, H = rect.height;
  const pad = { top: 16, right: 16, bottom: 36, left: 74 };
  const cW  = W - pad.left - pad.right;
  const cH  = H - pad.top  - pad.bottom;

  const C = { green:'#22c55e', red:'#ef4444', muted:'#94a3b8', border:'#334155' };
  const pts = _chartPoints, initBk = _chartInitialBk;

  // Empty / single-point state
  if (!pts || pts.length < 2) {
    ctx.fillStyle = C.muted;
    ctx.font = '13px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    const msg = pts && pts.length === 1
      ? `Bankroll $${initBk.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})} — chart will build as trades are placed and settled.`
      : 'No trades yet — chart will populate once positions are placed.';
    ctx.fillText(msg, W/2, H/2);
    return;
  }

  const parsed = pts.map(p => ({ t: new Date(p.t).getTime(), v: p.v }))
                    .filter(p => !isNaN(p.t) && p.t > 0);
  if (parsed.length === 0) return;

  // Y range — pad 12% around data + baseline
  const vals = parsed.map(p => p.v).concat([initBk]);
  let minV = Math.min(...vals), maxV = Math.max(...vals);
  const range = maxV - minV || 500;
  minV -= range * 0.12; maxV += range * 0.12;

  // T range
  const minT = parsed[0].t, maxT = parsed[parsed.length - 1].t;
  const tRange = maxT - minT || 1;

  const xOf = t => pad.left + ((t - minT) / tRange) * cW;
  const yOf = v => pad.top  + (1 - (v - minV) / (maxV - minV)) * cH;

  // ── Grid + Y axis labels ──
  ctx.font = '10px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
  ctx.textBaseline = 'middle';
  const nY = 5;
  for (let i = 0; i <= nY; i++) {
    const v = minV + (maxV - minV) * (i / nY);
    const y = yOf(v);
    ctx.strokeStyle = C.border; ctx.lineWidth = 0.5; ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + cW, y); ctx.stroke();
    ctx.fillStyle = C.muted; ctx.textAlign = 'right';
    ctx.fillText('$' + Math.round(v).toLocaleString('en-US'), pad.left - 6, y);
  }

  // ── Baseline (initial bankroll) dashed ──
  const baseY = yOf(initBk);
  ctx.strokeStyle = 'rgba(148,163,184,0.45)'; ctx.lineWidth = 1; ctx.setLineDash([5, 4]);
  ctx.beginPath(); ctx.moveTo(pad.left, baseY); ctx.lineTo(pad.left + cW, baseY); ctx.stroke();
  ctx.setLineDash([]);

  // ── Determine colour from final value ──
  const lastV   = parsed[parsed.length - 1].v;
  const lineClr = lastV >= initBk ? C.green : C.red;
  const fillClr = lastV >= initBk ? 'rgba(34,197,94,0.13)' : 'rgba(239,68,68,0.13)';

  // ── Fill area ──
  ctx.beginPath();
  ctx.moveTo(xOf(parsed[0].t), yOf(parsed[0].v));
  parsed.forEach(p => ctx.lineTo(xOf(p.t), yOf(p.v)));
  ctx.lineTo(xOf(parsed[parsed.length-1].t), baseY);
  ctx.lineTo(xOf(parsed[0].t), baseY);
  ctx.closePath();
  ctx.fillStyle = fillClr; ctx.fill();

  // ── Line ──
  ctx.beginPath();
  ctx.moveTo(xOf(parsed[0].t), yOf(parsed[0].v));
  parsed.forEach(p => ctx.lineTo(xOf(p.t), yOf(p.v)));
  ctx.strokeStyle = lineClr; ctx.lineWidth = 2; ctx.lineJoin = 'round'; ctx.stroke();

  // ── X axis labels ──
  ctx.fillStyle = C.muted; ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  const nX = Math.min(6, parsed.length);
  for (let i = 0; i <= nX; i++) {
    const t = minT + (tRange / nX) * i;
    const x = xOf(t);
    if (x < pad.left - 2 || x > pad.left + cW + 2) continue;
    const d  = new Date(t);
    const mo = d.getMonth() + 1, dy = d.getDate();
    const hh = String(d.getHours()).padStart(2,'0'), mm = String(d.getMinutes()).padStart(2,'0');
    ctx.fillText(`${mo}/${dy} ${hh}:${mm}`, x, pad.top + cH + 6);
  }

  // ── Chart border ──
  ctx.strokeStyle = C.border; ctx.lineWidth = 1;
  ctx.strokeRect(pad.left, pad.top, cW, cH);
}

function _initChartTooltip() {
  const canvas  = document.getElementById('pnlCanvas');
  const tooltip = document.getElementById('chartTooltip');
  if (!canvas || !tooltip) return;

  canvas.addEventListener('mousemove', function(e) {
    if (!_chartPoints || _chartPoints.length === 0) { tooltip.style.display = 'none'; return; }
    const rect  = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const pad    = { left: 74, right: 16 };
    const cW     = rect.width - pad.left - pad.right;
    const parsed = _chartPoints.map(p => ({ t: new Date(p.t).getTime(), v: p.v }))
                               .filter(p => !isNaN(p.t));
    if (parsed.length === 0) return;
    const minT = parsed[0].t, maxT = parsed[parsed.length-1].t;
    const tRange = maxT - minT || 1;
    let nearest = null, nearestDist = Infinity;
    parsed.forEach(p => {
      const x = pad.left + ((p.t - minT) / tRange) * cW;
      const d = Math.abs(x - mouseX);
      if (d < nearestDist) { nearestDist = d; nearest = p; }
    });
    if (nearest && nearestDist < 60) {
      const d   = new Date(nearest.t);
      const pnl = nearest.v - _chartInitialBk;
      const pnlStr = (pnl >= 0 ? '+' : '') + '$' + Math.abs(pnl).toLocaleString('en-US', {minimumFractionDigits:2,maximumFractionDigits:2});
      const pnlClr = pnl >= 0 ? '#22c55e' : '#ef4444';
      tooltip.style.display = 'block';
      tooltip.innerHTML = `
        <div style="color:#94a3b8;font-size:10px;margin-bottom:3px">${d.toLocaleString()}</div>
        <div style="font-weight:700">$${nearest.v.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}</div>
        <div style="color:${pnlClr};font-size:11px">${pnlStr} vs start</div>`;
    } else {
      tooltip.style.display = 'none';
    }
  });
  canvas.addEventListener('mouseleave', () => { tooltip.style.display = 'none'; });
}

window.addEventListener('resize', () => { if (_chartPoints.length > 0) _renderChart(); });

let countdown = 10;
let timer;

function startCountdown() {
  clearInterval(timer);
  countdown = 10;
  document.getElementById('countdown').textContent = countdown;
  timer = setInterval(() => {
    countdown--;
    document.getElementById('countdown').textContent = Math.max(0, countdown);
    if (countdown <= 0) { fetchData(); }
  }, 1000);
}

async function fetchData() {
  try {
    const r = await fetch('/api/data');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    update(d);
    document.getElementById('loading').classList.add('hidden');
    document.getElementById('errorBanner').style.display = 'none';
    startCountdown();
  } catch(e) {
    document.getElementById('errorBanner').textContent = 'Could not reach server: ' + e.message;
    document.getElementById('errorBanner').style.display = 'block';
    document.getElementById('loading').classList.add('hidden');
    startCountdown();
  }
}

fetchData();
_initChartTooltip();
</script>
</body>
</html>
"""


# ── Data collection ───────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        import yaml  # type: ignore[import]
        return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _collect_data(config: dict) -> dict:
    paths      = config.get("paths", {})
    risk_cfg   = config.get("risk", {})
    initial_bk = float(risk_cfg.get("initial_bankroll", 10_000.0))

    trade_log_path   = ROOT / paths.get("trade_log",     "data/trade_logs/trade_log.jsonl")
    unmatched_path   = ROOT / paths.get("unmatched_log", "data/trade_logs/unmatched_markets.jsonl")
    edge_history_path= ROOT / paths.get("edge_history",  "data/edge_history.csv")
    model_state_path = ROOT / paths.get("model_state",   "data/model_state.json")

    now_utc   = datetime.now(tz=timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")

    # ── Replay trade_log to reconstruct account state ─────────────────────────
    bankroll    = initial_bk
    peak_equity = initial_bk
    daily_pnl   = 0.0
    open_pos: dict[str, dict] = {}   # ticker → position dict
    recent_trades: list[dict] = []
    in_cooldown   = False
    cooldown_until: str | None = None
    equity_curve: list[dict] = []    # {"t": ISO str, "v": float}
    _equity_seeded = False           # have we emitted the opening baseline point?

    if trade_log_path.exists():
        for raw in trade_log_path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue

            action  = (rec.get("action") or "").upper()
            outcome = (rec.get("outcome") or "").upper()
            ticker  = rec.get("market_ticker", "") or rec.get("market_id", "")
            side    = rec.get("side", "YES")
            size    = float(rec.get("position_size_usd") or 0)
            pnl_val = float(rec.get("pnl_usd") or 0)
            ts_str  = rec.get("timestamp") or rec.get("timestamp_utc") or ""

            # Seed the curve with the opening baseline on the first timestamped entry
            if not _equity_seeded and ts_str:
                equity_curve.append({"t": ts_str, "v": round(bankroll, 2)})
                _equity_seeded = True

            if action == "BANKROLL_RESET":
                reset_bk     = float(rec.get("bankroll_usd") or initial_bk)
                bankroll     = reset_bk
                peak_equity  = reset_bk
                open_pos     = {}
                daily_pnl    = 0.0
                recent_trades  = []          # clear pre-reset trade history
                equity_curve   = []          # clear pre-reset chart history
                _equity_seeded = False       # re-seed baseline from next entry
                if ts_str:
                    equity_curve.append({"t": ts_str, "v": round(bankroll, 2)})
                    _equity_seeded = True
                continue

            if action in ("PLACED", "EXECUTED") and ticker and size > 0:
                open_pos[ticker] = {
                    "ticker":    ticker,
                    "event":     rec.get("event") or ticker,
                    "side":      side,
                    "size_usd":  size,
                    "fill_price": rec.get("fill_price"),
                    "opened_at": ts_str,
                }
                bankroll    -= size
                peak_equity  = max(peak_equity, bankroll)
                if ts_str:
                    equity_curve.append({"t": ts_str, "v": round(bankroll, 2)})

            if outcome in ("WIN", "LOSS", "VOID") and ticker:
                open_pos.pop(ticker, None)
                bankroll    += size + pnl_val
                peak_equity  = max(peak_equity, bankroll)
                if ts_str:
                    equity_curve.append({"t": ts_str, "v": round(bankroll, 2)})
                if ts_str.startswith(today_str):
                    daily_pnl += pnl_val

            # Collect most recent 20 records as "recent trades"
            recent_trades.append({
                "timestamp":        ts_str,
                "market_ticker":    ticker,
                "event":            rec.get("event") or None,
                "side":             side,
                "action":           action or None,
                "edge_pct":         rec.get("edge_pct"),
                "ev":               rec.get("ev"),
                "position_size_usd":size if size else None,
                "outcome":          outcome or None,
                "pnl_usd":          pnl_val if outcome in ("WIN","LOSS") else None,
            })

            # Cooldown from latest log entry
            if rec.get("in_cooldown"):
                in_cooldown    = True
                cooldown_until = rec.get("cooldown_until")

    recent_trades = recent_trades[-20:]
    recent_trades.reverse()

    exposure_usd  = sum(p["size_usd"] for p in open_pos.values())
    exposure_pct  = exposure_usd / bankroll if bankroll > 0 else 0.0
    drawdown_usd  = max(0.0, peak_equity - bankroll)
    drawdown_pct  = drawdown_usd / peak_equity if peak_equity > 0 else 0.0
    daily_pnl_pct = daily_pnl / initial_bk if initial_bk > 0 else 0.0

    # ── Edge history summary ───────────────────────────────────────────────────
    eh = {"n_trades": 0, "n_wins": 0, "win_rate": 0.0,
          "total_pnl_usd": 0.0, "avg_edge_pct": 0.0}
    if edge_history_path.exists():
        try:
            with edge_history_path.open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            resolved = [r for r in rows if r.get("outcome","").upper() in ("WIN","LOSS")]
            if resolved:
                wins       = sum(1 for r in resolved if r["outcome"].upper()=="WIN")
                total_pnl  = sum(float(r.get("pnl_usd") or 0) for r in resolved)
                avg_edge   = sum(float(r.get("edge_pct") or 0) for r in resolved) / len(resolved)
                eh = {
                    "n_trades":     len(resolved),
                    "n_wins":       wins,
                    "win_rate":     wins / len(resolved),
                    "total_pnl_usd": total_pnl,
                    "avg_edge_pct": avg_edge,
                }
        except Exception:
            pass

    # ── Learner state ─────────────────────────────────────────────────────────
    learner = {"n_records": 0, "global_bias": 0.0,
               "trained": False, "last_updated": None}
    if model_state_path.exists():
        try:
            state   = json.loads(model_state_path.read_text(encoding="utf-8"))
            records = state.get("records", [])
            n       = len(records)
            bias    = 0.0
            if n > 0:
                outcomes   = [r["outcome"]   for r in records]
                fair_probs = [r["fair_prob"] for r in records]
                bias = sum(outcomes)/n - sum(fair_probs)/n
            learner = {
                "n_records":    n,
                "global_bias":  round(bias, 6),
                "trained":      state.get("logistic_weights") is not None,
                "last_updated": state.get("last_updated"),
            }
        except Exception:
            pass

    mode = config.get("environment", "demo")
    mode = "live" if mode == "live" else "paper"

    return {
        "as_of":           now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode":            mode,
        "initial_bankroll": round(initial_bk, 2),
        "equity_curve":    equity_curve,
        "bankroll":        round(bankroll, 2),
        "peak_equity":   round(peak_equity, 2),
        "daily_pnl_usd": round(daily_pnl, 2),
        "daily_pnl_pct": round(daily_pnl_pct, 6),
        "exposure_usd":  round(exposure_usd, 2),
        "exposure_pct":  round(exposure_pct, 6),
        "drawdown_usd":  round(drawdown_usd, 2),
        "drawdown_pct":  round(drawdown_pct, 6),
        "in_cooldown":   in_cooldown,
        "cooldown_until": cooldown_until,
        "positions":     list(open_pos.values()),
        "recent_trades": recent_trades,
        "edge_history":  eh,
        "learner":       learner,
    }


# ── HTTP handler ──────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):

    _config: dict = {}

    def do_GET(self):  # noqa: N802
        if self.path in ("/", "/index.html"):
            body = _HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/api/data":
            try:
                data = _collect_data(self._config)
                body = json.dumps(data).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", len(body))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                msg = json.dumps({"error": str(exc)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", len(msg))
                self.end_headers()
                self.wfile.write(msg)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):  # silence default access log
        pass


# ── Entry point ───────────────────────────────────────────────────────────────

def _parse_args(argv: list) -> dict:
    args = {"port": 8765, "open_browser": True}
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--no-open":
            args["open_browser"] = False
        elif tok == "--port" and i + 1 < len(argv):
            i += 1
            args["port"] = int(argv[i])
        i += 1
    return args


def main(argv: list | None = None) -> int:
    args   = _parse_args(argv if argv is not None else sys.argv)
    port   = args["port"]
    config = _load_config()

    # Attach config to handler class (shared state, single-threaded server)
    _Handler._config = config

    server = HTTPServer(("127.0.0.1", port), _Handler)
    url    = f"http://localhost:{port}"

    print(f"  Betting Agent Dashboard  →  {url}")
    print("  Press Ctrl+C to stop.\n")

    if args["open_browser"]:
        Timer(0.3, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] Server stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
