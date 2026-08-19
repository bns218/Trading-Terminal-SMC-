(function () {
  "use strict";

  let currentToken = "";
  let currentTimeframe = "1min";
  let killSwitchOn = false;

  // Bar counts per timeframe, sized to cover several trading days of context
  // rather than a fixed 200-bar window (which was only ~3 hours on 1m).
  const CHART_LIMITS = { "1min": 1500, "3min": 1200, "5min": 900, "15min": 600, "30min": 400, "60min": 300 };
  function chartLimit(tf) { return CHART_LIMITS[tf] || 500; }

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
  let lastSmcPro = { order_blocks: [], fair_value_gaps: [], premium_discount: null, opening_range: [], signals: [] };
  let smcProEnabled = true;

  // --- zoom/pan state: a window [viewStart, viewStart+viewLen) into lastCandles ---
  const MIN_VISIBLE = 15;
  let viewStart = 0;
  let viewLen = 0; // 0 = "not yet set", resolved to full length on first data load
  let isDragging = false;
  let dragStartX = 0;
  let dragStartViewStart = 0;

  function clampView() {
    if (!lastCandles.length) { viewStart = 0; viewLen = 0; return; }
    viewLen = Math.max(MIN_VISIBLE, Math.min(lastCandles.length, viewLen || lastCandles.length));
    viewStart = Math.max(0, Math.min(lastCandles.length - viewLen, viewStart));
  }

  function resetZoom() {
    viewLen = lastCandles.length;
    viewStart = 0;
    redraw();
  }

  function setupZoomPan() {
    canvas.addEventListener("wheel", (e) => {
      if (!lastCandles.length) return;
      e.preventDefault();
      clampView();
      const rect = canvas.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const oldLen = viewLen;
      const oldW = canvas.width / oldLen;
      const barUnderMouse = viewStart + mouseX / oldW;
      const factor = e.deltaY < 0 ? 0.85 : 1 / 0.85; // scroll up = zoom in
      viewLen = Math.round(oldLen * factor);
      clampView();
      const newW = canvas.width / viewLen;
      viewStart = Math.round(barUnderMouse - mouseX / newW);
      clampView();
      redraw();
    }, { passive: false });

    canvas.addEventListener("mousedown", (e) => {
      if (!lastCandles.length) return;
      isDragging = true;
      dragStartX = e.clientX;
      dragStartViewStart = viewStart;
      canvas.style.cursor = "grabbing";
    });
    window.addEventListener("mousemove", (e) => {
      if (!isDragging) return;
      const w = canvas.width / viewLen;
      const barDelta = Math.round(-(e.clientX - dragStartX) / w);
      viewStart = dragStartViewStart + barDelta;
      clampView();
      redraw();
    });
    window.addEventListener("mouseup", () => {
      isDragging = false;
      canvas.style.cursor = "grab";
    });

    document.getElementById("zoom-in-btn").addEventListener("click", () => {
      clampView();
      viewLen = Math.round(viewLen * 0.8);
      clampView();
      redraw();
    });
    document.getElementById("zoom-out-btn").addEventListener("click", () => {
      clampView();
      viewLen = Math.round(viewLen * 1.25);
      clampView();
      redraw();
    });
    document.getElementById("zoom-reset-btn").addEventListener("click", resetZoom);
  }

  async function fetchJson(url, options) {
    const res = await fetch(url, options);
    if (!res.ok) throw new Error(url + " -> " + res.status);
    return res.json();
  }

  async function loadMeta() {
    const meta = await fetchJson("/api/meta");
    const badgeText = meta.trading_mode + (meta.demo_mode ? " / DEMO" : "");
    document.getElementById("mode-badge").textContent = badgeText;
    document.getElementById("mode-badge-home").textContent = badgeText;
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
    const limit = chartLimit(currentTimeframe);
    const [candles, indicators, overlays, smcPro] = await Promise.all([
      fetchJson(`/api/candles/${currentToken}?timeframe=${currentTimeframe}&limit=${limit}`),
      fetchJson(`/api/indicators/${currentToken}?timeframe=${currentTimeframe}&limit=${limit}`),
      fetchJson(`/api/candles/${currentToken}/overlays?timeframe=${currentTimeframe}&limit=${limit}`),
      fetchJson(`/api/candles/${currentToken}/smc_pro?timeframe=${currentTimeframe}&limit=${limit}`),
    ]);
    // Keep showing "all data" across auto-refresh if the user hadn't zoomed in
    // (viewLen===0 means "never set" or explicitly reset by a fresh symbol/timeframe change);
    // otherwise preserve their zoom window across the refresh.
    const wasFullyZoomedOut = viewLen === 0 || viewLen >= lastCandles.length;
    lastCandles = candles;
    lastIndicators = indicators;
    lastOverlays = overlays;
    lastSmcPro = smcPro;
    if (wasFullyZoomedOut) {
      viewLen = lastCandles.length;
      viewStart = 0;
    }
    clampView();
    updateDataRangeLabel();
    redraw();
  }

  function updateDataRangeLabel() {
    const el = document.getElementById("data-range-label");
    if (!lastCandles.length) { el.textContent = ""; return; }
    const first = new Date(lastCandles[0].open_time);
    const last = new Date(lastCandles[lastCandles.length - 1].close_time);
    const fmt = (d) => d.toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
    el.textContent = `Showing ${lastCandles.length} bars: ${fmt(first)} → ${fmt(last)}`;
  }

  function redraw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!lastCandles.length) {
      ctx.fillStyle = "#8b93a3";
      ctx.fillText("No candle data yet.", 12, 20);
      return;
    }

    clampView();
    const visible = lastCandles.slice(viewStart, viewStart + viewLen);
    const n = visible.length;
    const w = canvas.width / n;
    const toLocalX = (fullIdx) => (fullIdx - viewStart) * w + w / 2;

    const prices = visible.flatMap((c) => [c.high, c.low]);
    const minP = Math.min(...prices), maxP = Math.max(...prices);
    const pad = (maxP - minP) * 0.08 || 1;
    const yMin = minP - pad, yMax = maxP + pad;
    const y = (price) => canvas.height - ((price - yMin) / (yMax - yMin)) * canvas.height;
    const timeIndex = new Map(lastCandles.map((c, i) => [c.close_time, i]));

    if (smcProEnabled) drawSmcZones(y, w, n, toLocalX);

    visible.forEach((c, i) => {
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

    drawLine((lastIndicators.ema_9 || []).slice(viewStart, viewStart + viewLen), y, w, "#5b8def");
    drawLine((lastIndicators.ema_20 || []).slice(viewStart, viewStart + viewLen), y, w, "#e8b339");

    const drawMarkers = (events, aboveOffset) => {
      events.forEach((e) => {
        const idx = timeIndex.get(e.timestamp);
        if (idx === undefined || idx < viewStart || idx >= viewStart + viewLen) return;
        const x = toLocalX(idx);
        const candle = lastCandles[idx];
        const yy = e.direction === "bearish" ? y(candle.high) - aboveOffset : y(candle.low) + aboveOffset;
        ctx.fillStyle = e.direction === "bearish" ? "#ef5a5a" : e.direction === "bullish" ? "#3ecf8e" : "#8b93a3";
        ctx.beginPath();
        ctx.arc(x, yy, 3, 0, Math.PI * 2);
        ctx.fill();
      });
    };
    drawMarkers(lastOverlays.candlestick || [], 10);
    drawMarkers(lastOverlays.structure || [], 18);
    drawMarkers(lastOverlays.chart_patterns || [], 26);

    if (smcProEnabled) {
      drawPremiumDiscount(y);
      drawOpeningRange(y, toLocalX);
      drawSignals(y, toLocalX);
    }
  }

  function timeIdxOr(timeIndex, isoTime, fallback) {
    const idx = timeIndex.get(isoTime);
    return idx === undefined ? fallback : idx;
  }

  function drawSmcZones(y, w, n, toLocalX) {
    const timeIndex = new Map(lastCandles.map((c, i) => [c.close_time, i]));
    const viewEnd = viewStart + viewLen;
    const zoneColor = (kind, bias) => {
      if (kind === "fair_value_gap") return bias === "bullish" ? ["rgba(91,141,239,0.16)", "#5b8def"] : ["rgba(232,179,57,0.16)", "#e8b339"];
      return bias === "bullish" ? ["rgba(62,207,142,0.20)", "#3ecf8e"] : ["rgba(239,90,90,0.20)", "#ef5a5a"];
    };
    const drawZoneList = (zones) => {
      zones.forEach((z) => {
        const startIdx = timeIdxOr(timeIndex, z.start_time, 0);
        const endIdx = z.end_time ? timeIdxOr(timeIndex, z.end_time, lastCandles.length - 1) : lastCandles.length - 1;
        if (endIdx < viewStart || startIdx > viewEnd - 1) return; // entirely outside the visible window
        const x1 = toLocalX(Math.max(viewStart, startIdx)) - w / 2;
        const x2 = toLocalX(Math.min(viewEnd - 1, endIdx)) + w / 2;
        const [fill, stroke] = zoneColor(z.kind, z.bias);
        ctx.fillStyle = fill;
        ctx.fillRect(x1, y(z.top), x2 - x1, y(z.bottom) - y(z.top));
        ctx.strokeStyle = stroke;
        ctx.lineWidth = 1;
        ctx.setLineDash(z.kind === "breaker_block" || z.is_inverse ? [4, 3] : []);
        ctx.strokeRect(x1, y(z.top), x2 - x1, y(z.bottom) - y(z.top));
        ctx.setLineDash([]);
      });
    };
    drawZoneList(lastSmcPro.order_blocks || []);
    drawZoneList(lastSmcPro.fair_value_gaps || []);
  }

  function drawPremiumDiscount(y) {
    const pdz = lastSmcPro.premium_discount;
    if (!pdz) return;
    const levels = [
      [pdz.premium_level, "Premium"],
      [pdz.equilibrium_level, "Equilibrium"],
      [pdz.discount_level, "Discount"],
    ];
    ctx.strokeStyle = "#4a90d9";
    ctx.fillStyle = "#4a90d9";
    ctx.font = "10px sans-serif";
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    levels.forEach(([level, label]) => {
      const yy = y(level);
      ctx.beginPath();
      ctx.moveTo(0, yy);
      ctx.lineTo(canvas.width, yy);
      ctx.stroke();
      ctx.fillText(label, canvas.width - ctx.measureText(label).width - 4, yy - 2);
    });
    ctx.setLineDash([]);
  }

  function drawOpeningRange(y, toLocalX) {
    const ranges = lastSmcPro.opening_range || [];
    if (!ranges.length) return;
    const latest = ranges[ranges.length - 1];
    const viewEnd = viewStart + viewLen;
    const dayIndices = lastCandles
      .map((c, i) => [i, c.close_time])
      .filter(([i, t]) => i >= viewStart && i < viewEnd && t.slice(0, 10) === latest.session_date)
      .map(([i]) => i);
    if (!dayIndices.length) return;
    const startX = toLocalX(Math.min(...dayIndices));
    ctx.strokeStyle = "#3ecf8e";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(startX, y(latest.high));
    ctx.lineTo(canvas.width, y(latest.high));
    ctx.stroke();
    ctx.strokeStyle = "#ef5a5a";
    ctx.beginPath();
    ctx.moveTo(startX, y(latest.low));
    ctx.lineTo(canvas.width, y(latest.low));
    ctx.stroke();
  }

  function drawSignals(y, toLocalX) {
    const signals = lastSmcPro.signals || [];
    if (!signals.length) return;
    const timeIndex = new Map(lastCandles.map((c, i) => [c.close_time, i]));
    const s = signals[signals.length - 1]; // most recent only, matches Pine's single-active-trade lines
    const idx = timeIndex.get(s.timestamp);
    if (idx === undefined || idx < viewStart || idx >= viewStart + viewLen) return;
    const x = toLocalX(idx);
    const endX = canvas.width;

    ctx.font = "bold 11px sans-serif";
    const label = s.direction === "long" ? "BUY" : "SELL";
    const labelY = s.direction === "long" ? y(s.entry) + 16 : y(s.entry) - 8;
    ctx.fillStyle = s.direction === "long" ? "#3ecf8e" : "#ef5a5a";
    ctx.fillRect(x - 14, labelY - 10, 28, 14);
    ctx.fillStyle = "#0f1115";
    ctx.fillText(label, x - 12, labelY + 1);

    const drawTargetLine = (price, color, dashed) => {
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.setLineDash(dashed ? [4, 3] : []);
      ctx.beginPath();
      ctx.moveTo(x, y(price));
      ctx.lineTo(endX, y(price));
      ctx.stroke();
      ctx.setLineDash([]);
    };
    drawTargetLine(s.stop_loss, "#ef5a5a", false);
    drawTargetLine(s.tp1, "#3ecf8e", true);
    drawTargetLine(s.tp2, "#3ecf8e", true);
    drawTargetLine(s.tp3, "#3ecf8e", true);
  }

  function drawLine(points, y, w, color) {
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

  function fmtNewsDate(pubDate) {
    if (!pubDate) return "";
    const d = new Date(pubDate);
    return isNaN(d) ? pubDate : d.toLocaleString();
  }

  async function loadNews() {
    if (!currentToken) return;
    const body = document.getElementById("news-body");
    body.innerHTML = `<div class="empty-state">Loading news...</div>`;
    const data = await fetchJson(`/api/news/${currentToken}`);
    const articles = data.articles || [];
    document.getElementById("news-empty").style.display = articles.length ? "none" : "block";
    body.innerHTML = articles
      .map(
        (a) => `<a class="news-item" href="${a.link}" target="_blank" rel="noopener noreferrer">
          <div class="news-title">${a.title}</div>
          <div class="news-meta">${a.source || ""}${a.source && a.published ? " · " : ""}${fmtNewsDate(a.published)}</div>
        </a>`
      )
      .join("");
  }

  // Stocks: 15m + 60m. Indices: 5m + 15m. Fixed per the product decision that
  // pattern reliability/noise differs by instrument type, independent of
  // whatever timeframe the main chart happens to be showing.
  const STOCK_PATTERN_TIMEFRAMES = ["15min", "60min"];
  const INDEX_PATTERN_TIMEFRAMES = ["5min", "15min"];
  const TF_LABELS = { "5min": "5m", "15min": "15m", "60min": "60m" };

  function patternListHtml(events) {
    if (!events.length) return `<div class="empty-state">None in the last 2 trading days.</div>`;
    return events
      .map((e) => {
        const colorClass = e.direction === "bullish" ? "pnl-pos" : e.direction === "bearish" ? "pnl-neg" : "";
        const t = new Date(e.timestamp);
        const timeLabel = isNaN(t) ? e.timestamp : t.toLocaleString();
        return `<div class="pattern-item">
          <span class="pattern-name ${colorClass}">${e.pattern.replace(/_/g, " ")}</span>
          <span class="pattern-time">${timeLabel}</span>
        </div>`;
      })
      .join("");
  }

  async function loadPatterns() {
    if (!currentToken) return;
    const container = document.getElementById("patterns-content");
    container.innerHTML = `<div class="empty-state">Loading patterns...</div>`;

    const entry = lastWatchlist.find((c) => c.token === currentToken);
    const kind = entry ? entry.kind : "stock";
    const timeframes = kind === "index" ? INDEX_PATTERN_TIMEFRAMES : STOCK_PATTERN_TIMEFRAMES;

    const results = await Promise.all(
      timeframes.map((tf) => fetchJson(`/api/patterns/${currentToken}?timeframe=${tf}&days=2`))
    );

    container.innerHTML = timeframes
      .map((tf, i) => {
        const data = results[i];
        const rangeLabel = data.trading_dates.length ? `Trading days: ${data.trading_dates.join(", ")}` : "";
        return `
          <div class="patterns-timeframe-block">
            <div class="patterns-timeframe-header">${TF_LABELS[tf] || tf}<span class="patterns-range-label">${rangeLabel}</span></div>
            <div class="patterns-section-label">Chart Patterns</div>
            ${patternListHtml(data.chart_patterns || [])}
            <div class="patterns-section-label">Candle Patterns</div>
            ${patternListHtml(data.candlestick_patterns || [])}
          </div>`;
      })
      .join("");
  }

  let lastWatchlist = [];

  function fmtChangePct(pct) {
    if (pct === null || pct === undefined) return "--";
    return (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%";
  }

  function fmtRangePct(c) {
    if (c.day_range_pct === null || c.day_range_pct === undefined) return "";
    return `H-L ${c.day_low.toFixed(2)}-${c.day_high.toFixed(2)} (${c.day_range_pct.toFixed(2)}%)`;
  }

  // Gainers first, losers last; symbols with no change_pct (no prior close yet) sink to the bottom.
  function sortByChangePct(cards) {
    return [...cards].sort((a, b) => {
      if (a.change_pct === null) return 1;
      if (b.change_pct === null) return -1;
      return b.change_pct - a.change_pct;
    });
  }

  async function loadWatchlist() {
    lastWatchlist = await fetchJson(`/api/watchlist?timeframe=${currentTimeframe}`);
    renderHomeCards();
  }

  // --- Home page: landing grid of Indices + F&O stock cards ---

  function renderHomeCards() {
    const filter = document.getElementById("home-filter").value.trim().toUpperCase();
    const filtered = filter ? lastWatchlist.filter((c) => c.symbol.includes(filter)) : lastWatchlist;
    const groupsEl = document.getElementById("home-groups");
    const groups = [
      ["Indices", filtered.filter((c) => c.kind === "index")],
      ["F&O Stocks: Gainers → Losers", sortByChangePct(filtered.filter((c) => c.kind === "stock"))],
    ];
    groupsEl.innerHTML = groups
      .map(([label, cards]) => {
        if (!cards.length) return "";
        const cardsHtml = cards
          .map((c) => {
            const isUp = c.change === null ? null : c.change >= 0;
            const colorClass = isUp === null ? "" : isUp ? "pnl-pos" : "pnl-neg";
            return `<div class="home-card" data-token="${c.token}">
              <span class="hc-symbol">${c.symbol}</span>
              <span class="hc-ltp">${c.ltp.toFixed(2)}</span>
              <span class="hc-change ${colorClass}">${c.change === null ? "--" : fmtMoney(c.change)} (${fmtChangePct(c.change_pct)})</span>
              <span class="hc-range">${fmtRangePct(c)}</span>
            </div>`;
          })
          .join("");
        return `<div class="home-group-label">${label}</div><div class="home-card-grid">${cardsHtml}</div>`;
      })
      .join("");

    groupsEl.querySelectorAll(".home-card").forEach((el) => {
      el.addEventListener("click", () => openChart(el.dataset.token));
    });
  }

  function showHomePage() {
    document.getElementById("home-page").style.display = "";
    document.getElementById("chart-page").style.display = "none";
  }

  function openChart(token) {
    currentToken = token;
    document.getElementById("token-input").value = token;
    viewStart = 0;
    viewLen = 0; // fresh symbol -> reset zoom to full view
    document.getElementById("home-page").style.display = "none";
    document.getElementById("chart-page").style.display = "";
    resizeCanvas(); // canvas was hidden (display:none) and had zero size when last measured
    refreshAll();
    loadNews();
    loadPatterns();
  }

  function setupTabs() {
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById(btn.dataset.tab).classList.add("active");
        if (btn.dataset.tab === "news") loadNews();
        if (btn.dataset.tab === "patterns") loadPatterns();
      });
    });
  }

  async function refreshAll() {
    await Promise.all([loadChartData(), loadPositions(), loadJournal()]);
  }

  function setup() {
    resizeCanvas();
    setupTabs();
    setupZoomPan();
    canvas.style.cursor = "grab";

    document.getElementById("token-input").addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      const token = e.target.value.trim();
      if (token && token !== currentToken) openChart(token);
    });

    document.getElementById("timeframe-select").addEventListener("change", (e) => {
      currentTimeframe = e.target.value;
      viewStart = 0;
      viewLen = 0; // new timeframe -> reset zoom to full view
      refreshAll(); // News/Patterns are independent of the chart's timeframe, no need to reload them
    });

    document.getElementById("smc-pro-toggle").addEventListener("change", (e) => {
      smcProEnabled = e.target.checked;
      redraw();
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

    document.getElementById("home-filter").addEventListener("input", renderHomeCards);
    document.getElementById("home-refresh-btn").addEventListener("click", loadWatchlist);
    document.getElementById("back-to-home-btn").addEventListener("click", showHomePage);

    loadMeta().then(() => {
      if (currentToken) {
        openChart(currentToken); // demo mode pre-selects an instrument
      } else {
        refreshAll(); // populates positions/journal even with the chart page hidden
      }
    });
    loadWatchlist();
    setInterval(refreshAll, 15000); // fragment-scoped auto-refresh, no WebSocket owned by this page
    setInterval(loadWatchlist, 60000); // watchlist scans 200+ tokens - refresh less aggressively
  }

  document.addEventListener("DOMContentLoaded", setup);
})();
