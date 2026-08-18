(function () {
  "use strict";

  let currentToken = "";
  let currentTimeframe = "1min";
  let killSwitchOn = false;

  const canvas = document.getElementById("chart-canvas");
  const ctx = canvas.getContext("2d");

  function resizeCanvas() {
    const rect = canvas.parentElement.getBoundingClientRect();
    canvas.width = rect.width - 24;
    canvas.height = rect.height - 60;
  }
  window.addEventListener("resize", () => { resizeCanvas(); redraw(); });

  let lastCandles = [];
  let lastIndicators = {};
  let lastOverlays = { candlestick: [], structure: [], fair_value_gaps: [], chart_patterns: [] };

  async function fetchJson(url, options) {
    const res = await fetch(url, options);
    if (!res.ok) throw new Error(url + " -> " + res.status);
    return res.json();
  }

  async function loadMeta() {
    const meta = await fetchJson("/api/meta");
    document.getElementById("mode-badge").textContent = meta.trading_mode + (meta.demo_mode ? " / DEMO" : "");
    document.getElementById("demo-banner").classList.toggle("visible", meta.demo_mode);
    killSwitchOn = meta.kill_switch_enabled;
    updateKillSwitchButton();
    if (meta.demo_mode) {
      const instruments = await fetchJson("/api/instruments/search");
      if (instruments.length) {
        currentToken = instruments[0].token;
        document.getElementById("token-input").value = currentToken;
      }
    }
  }

  function updateKillSwitchButton() {
    const btn = document.getElementById("kill-switch-btn");
    btn.textContent = "Kill Switch: " + (killSwitchOn ? "ON" : "OFF");
    btn.classList.toggle("active", killSwitchOn);
  }

  async function loadChartData() {
    if (!currentToken) return;
    const [candles, indicators, overlays] = await Promise.all([
      fetchJson(`/api/candles/${currentToken}?timeframe=${currentTimeframe}&limit=200`),
      fetchJson(`/api/indicators/${currentToken}?timeframe=${currentTimeframe}&limit=200`),
      fetchJson(`/api/candles/${currentToken}/overlays?timeframe=${currentTimeframe}&limit=200`),
    ]);
    lastCandles = candles;
    lastIndicators = indicators;
    lastOverlays = overlays;
    redraw();
  }

  function redraw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!lastCandles.length) {
      ctx.fillStyle = "#8b93a3";
      ctx.fillText("No candle data yet.", 12, 20);
      return;
    }

    const prices = lastCandles.flatMap((c) => [c.high, c.low]);
    const minP = Math.min(...prices), maxP = Math.max(...prices);
    const pad = (maxP - minP) * 0.08 || 1;
    const yMin = minP - pad, yMax = maxP + pad;

    const n = lastCandles.length;
    const w = canvas.width / n;
    const y = (price) => canvas.height - ((price - yMin) / (yMax - yMin)) * canvas.height;

    lastCandles.forEach((c, i) => {
      const x = i * w + w / 2;
      const up = c.close >= c.open;
      ctx.strokeStyle = up ? "#3ecf8e" : "#ef5a5a";
      ctx.fillStyle = up ? "#3ecf8e" : "#ef5a5a";
      ctx.beginPath();
      ctx.moveTo(x, y(c.high));
      ctx.lineTo(x, y(c.low));
      ctx.stroke();
      const bodyTop = y(Math.max(c.open, c.close));
      const bodyBottom = y(Math.min(c.open, c.close));
      ctx.fillRect(x - w * 0.35, bodyTop, w * 0.7, Math.max(1, bodyBottom - bodyTop));
    });

    drawLine(lastIndicators.ema_9, y, n, w, "#5b8def");
    drawLine(lastIndicators.ema_20, y, n, w, "#e8b339");

    const timeIndex = new Map(lastCandles.map((c, i) => [c.close_time, i]));
    const drawMarkers = (events, aboveOffset) => {
      events.forEach((e) => {
        const idx = timeIndex.get(e.timestamp);
        if (idx === undefined) return;
        const x = idx * w + w / 2;
        const yy = e.direction === "bearish" ? y(lastCandles[idx].high) - aboveOffset : y(lastCandles[idx].low) + aboveOffset;
        ctx.fillStyle = e.direction === "bearish" ? "#ef5a5a" : e.direction === "bullish" ? "#3ecf8e" : "#8b93a3";
        ctx.beginPath();
        ctx.arc(x, yy, 3, 0, Math.PI * 2);
        ctx.fill();
      });
    };
    drawMarkers(lastOverlays.candlestick || [], 10);
    drawMarkers(lastOverlays.structure || [], 18);
    drawMarkers(lastOverlays.chart_patterns || [], 26);
  }

  function drawLine(points, y, n, w, color) {
    if (!points) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    let started = false;
    points.forEach((p, i) => {
      const value = p[1];
      if (value === null) { started = false; return; }
      const x = i * w + w / 2;
      const yy = y(value);
      if (!started) { ctx.moveTo(x, yy); started = true; } else { ctx.lineTo(x, yy); }
    });
    ctx.stroke();
    ctx.lineWidth = 1;
  }

  function fmtMoney(v) {
    const n = parseFloat(v);
    return (n >= 0 ? "+" : "") + n.toFixed(2);
  }

  async function loadPositions() {
    const positions = await fetchJson("/api/positions");
    const body = document.getElementById("positions-body");
    body.innerHTML = "";
    document.getElementById("positions-empty").style.display = positions.length ? "none" : "block";
    positions.forEach((p) => {
      const pnl = parseFloat(p.unrealized_pnl);
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${p.instrument_symbol}</td><td>${p.direction}</td><td>${p.quantity}</td>
        <td>${p.entry_price}</td><td>${p.current_price}</td>
        <td class="${pnl >= 0 ? "pnl-pos" : "pnl-neg"}">${fmtMoney(p.unrealized_pnl)}</td>`;
      body.appendChild(tr);
    });
  }

  async function loadJournal() {
    const entries = await fetchJson("/api/journal?limit=50");
    const body = document.getElementById("journal-body");
    body.innerHTML = "";
    document.getElementById("journal-empty").style.display = entries.length ? "none" : "block";
    entries.forEach((e) => {
      const pnl = parseFloat(e.pnl_net);
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${e.instrument_symbol}</td><td>${e.direction}</td>
        <td class="${pnl >= 0 ? "pnl-pos" : "pnl-neg"}">${fmtMoney(e.pnl_net)}</td><td>${e.exit_reason}</td>`;
      body.appendChild(tr);
    });
  }

  function renderHealth(h) {
    const body = document.getElementById("health-body");
    const rows = [
      ["Last tick age", h.last_tick_age_seconds === null ? "no ticks yet" : h.last_tick_age_seconds.toFixed(1) + "s", h.is_stale],
      ["Reconnects (24h)", h.reconnect_count_24h, false],
      ["Rejected bars (24h)", h.rejected_bar_count_24h, false],
      ["Backfills (24h)", h.backfill_count_24h, false],
    ];
    body.innerHTML = rows
      .map(([label, val, stale]) => `<div class="health-row"><span>${label}</span><span class="val ${stale ? "stale" : "fresh"}">${val}</span></div>`)
      .join("");
  }

  async function loadHealth() {
    if (!currentToken) return;
    const health = await fetchJson(`/api/data-health/${currentToken}`);
    renderHealth(health);
  }

  function setupTabs() {
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById(btn.dataset.tab).classList.add("active");
      });
    });
  }

  async function refreshAll() {
    await Promise.all([loadChartData(), loadPositions(), loadJournal(), loadHealth()]);
  }

  function setup() {
    resizeCanvas();
    setupTabs();

    document.getElementById("load-btn").addEventListener("click", () => {
      currentToken = document.getElementById("token-input").value.trim();
      currentTimeframe = document.getElementById("timeframe-select").value;
      refreshAll();
    });

    document.getElementById("kill-switch-btn").addEventListener("click", async () => {
      killSwitchOn = !killSwitchOn;
      await fetch("/api/risk/kill-switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: killSwitchOn }),
      });
      updateKillSwitchButton();
    });

    loadMeta().then(refreshAll);
    setInterval(refreshAll, 15000); // fragment-scoped auto-refresh, no WebSocket owned by this page
  }

  document.addEventListener("DOMContentLoaded", setup);
})();
