// overview.js — renders the Business Overview dashboard from GET /api/analytics/overview.
// Platform-admin sees all clubs (with a club filter); club-admin sees their own club.
// Self-contained via window.TFAuth + ECharts (loaded by overview.html).
(function () {
  function el(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function money(minor, ccy) {
    var sym = (ccy === "USD") ? "$" : "R";
    return sym + (Number(minor || 0) / 100).toLocaleString(undefined, { maximumFractionDigits: 0 });
  }
  function num(n) { return (n == null) ? "—" : Number(n || 0).toLocaleString(); }

  var charts = {};
  function chart(id) {
    var node = el(id);
    if (!node || !window.echarts) return null;
    if (charts[id]) charts[id].dispose();
    charts[id] = window.echarts.init(node);
    return charts[id];
  }
  window.addEventListener("resize", function () {
    Object.keys(charts).forEach(function (k) { try { charts[k].resize(); } catch (e) {} });
  });

  function card(lbl, val, sub) {
    return '<div class="kpi"><div class="lbl">' + esc(lbl) + '</div>' +
      '<div class="val">' + esc(val) + '</div>' +
      (sub ? '<div class="sub">' + esc(sub) + '</div>' : '') + '</div>';
  }

  function kpis(k, currency, extra) {
    var visSub = (k.new_visitors == null) ? "" : (num(k.new_visitors) + " new · " + num(k.returning_visitors) + " returning");
    var cards = [
      card("Website visits", num(k.visits)),
      card("Unique visitors", num(k.unique_visitors), visSub),
      card("Customers", num(k.total_customers), "+" + num(k.new_customers) + " in period"),
    ];
    if (k.bookings != null) cards.push(card("Bookings", num(k.bookings)));
    cards.push(card("Revenue (net)", money(k.net_minor, currency), "gross " + money(k.revenue_minor, currency)));
    (extra || []).forEach(function (x) {
      cards.push(card(x.label, x.value_minor != null ? money(x.value_minor, x.currency || currency) : num(x.value)));
    });
    el("ov-kpis").innerHTML = cards.join("");
  }

  function lineChart(id, rows, xKey, series, emptyMsg) {
    var c = chart(id);
    if (!c) return;
    if (!rows || !rows.length) { el(id).innerHTML = '<div class="empty">' + emptyMsg + '</div>'; return; }
    c.setOption({
      grid: { left: 38, right: 16, top: 22, bottom: 28 },
      tooltip: { trigger: "axis" },
      legend: { bottom: 0, data: series.map(function (s) { return s.name; }) },
      xAxis: { type: "category", data: rows.map(function (r) { return r[xKey].slice(5); }),
        axisLine: { lineStyle: { color: "#cbd2dc" } } },
      yAxis: { type: "value", splitLine: { lineStyle: { color: "#eef0f4" } } },
      series: series.map(function (s) {
        return { name: s.name, type: "line", smooth: true, showSymbol: false,
          data: rows.map(function (r) { return r[s.key] || 0; }),
          lineStyle: { width: 2, color: s.color },
          areaStyle: s.area ? { color: s.color, opacity: 0.08 } : null };
      }),
    });
  }

  function barTable(id, rows, labelKey, valKey, fmt) {
    var node = el(id);
    if (!rows || !rows.length) { node.innerHTML = '<div class="empty">No data yet.</div>'; return; }
    var max = Math.max.apply(null, rows.map(function (r) { return r[valKey] || 0; })) || 1;
    node.innerHTML = '<table><tbody>' + rows.map(function (r) {
      var w = Math.max(4, Math.round((r[valKey] / max) * 120));
      return '<tr><td>' + esc(r[labelKey] || "—") + '</td>' +
        '<td class="num"><span class="bar" style="width:' + w + 'px"></span> ' +
        (fmt ? fmt(r[valKey]) : num(r[valKey])) + '</td></tr>';
    }).join("") + '</tbody></table>';
  }

  function settleTable(rows, currency) {
    var node = el("tbl-settle");
    if (!rows || !rows.length) { node.innerHTML = '<div class="empty">No orders yet.</div>'; return; }
    node.innerHTML = '<table><thead><tr><th>Mode</th><th class="num">Orders</th><th class="num">Value</th></tr></thead><tbody>' +
      rows.map(function (r) {
        return '<tr><td>' + esc(r.mode) + '</td><td class="num">' + num(r.count) +
          '</td><td class="num">' + money(r.amount_minor, currency) + '</td></tr>';
      }).join("") + '</tbody></table>';
  }

  function nps(n) {
    var node = el("ov-nps");
    if (!n || !n.total) { node.innerHTML = '<div class="empty">No NPS responses yet.</div>'; return; }
    node.innerHTML = '<div style="font-size:2rem;font-weight:800">' +
      (n.score == null ? "—" : n.score) + '<span class="muted" style="font-size:1rem;font-weight:500"> NPS · ' +
      num(n.total) + ' responses</span></div>' +
      '<div class="nps-buckets">' +
      '<div style="background:#e6f6ec;color:#15803d">' + num(n.promoters) + '<br><small>Promoters</small></div>' +
      '<div style="background:#f4f5f7;color:#4b5563">' + num(n.passives) + '<br><small>Passives</small></div>' +
      '<div style="background:#fdeaea;color:#b91c1c">' + num(n.detractors) + '<br><small>Detractors</small></div>' +
      '</div>';
  }

  function renderSingle(d) {
    var ccy = d.currency || "ZAR";
    kpis(d.kpis || {}, ccy, d.extra_kpis);
    lineChart("ch-visits", d.visits_daily, "day",
      [{ name: "Visits", key: "visits", color: "#2563eb", area: true },
       { name: "Unique", key: "unique_visitors", color: "#16a34a" }],
      "No website traffic in this period.");
    lineChart("ch-signups", d.signups_daily, "day",
      [{ name: "Sign-ups", key: "signups", color: "#d97706", area: true }],
      "No sign-ups in this period.");
    barTable("tbl-sources", d.traffic_sources, "source", "visits");
    barTable("tbl-pages", d.top_pages, "path", "visits");
    barTable("tbl-geo", d.by_country, "country", "visits");
    settleTable(d.settlement_mix, ccy);
    nps(d.nps);
  }

  function render(d) {
    return renderSingle(d || {});
  }

  var state = { days: 30, club_id: "" };

  async function load() {
    var auth = window.TFAuth;
    var qs = "?days=" + state.days + (state.club_id ? "&club_id=" + encodeURIComponent(state.club_id) : "");
    try {
      var d = await auth.apiJSON("/api/analytics/overview" + qs);
      render(d);
    } catch (e) {
      el("ov-kpis").innerHTML = '<div class="empty">' +
        (e && e.status === 403 ? "You don't have access to analytics." :
         e && e.status === 401 ? "Please sign in." : "Couldn't load analytics.") + '</div>';
    }
  }

  var Overview = {
    start: async function () {
      var auth = window.TFAuth;
      if (!auth) { el("ov-kpis").innerHTML = '<div class="empty">Auth not loaded.</div>'; return; }
      try { await auth.ready(); } catch (e) {}
      if (auth.isAuthed && !auth.isAuthed()) { if (auth.requireAuth) return auth.requireAuth(); }

      // Platform-admin gets a club filter (all clubs / one club). club_admin = own club.
      try {
        var who = await auth.apiJSON("/api/whoami");
        if (who && who.role === "platform_admin") {
          var sel = el("ov-club");
          try {
            var cl = await auth.apiJSON("/api/analytics/clubs");
            sel.innerHTML = '<option value="">All clubs</option>' +
              (cl.clubs || []).map(function (c) { return '<option value="' + esc(c.id) + '">' + esc(c.name) + '</option>'; }).join("");
            sel.style.display = "";
            sel.addEventListener("change", function () {
              state.club_id = sel.value;
              el("ov-scope").textContent = sel.options[sel.selectedIndex].text;
              load();
            });
          } catch (e) {}
        } else if (who) {
          el("ov-scope").textContent = who.club_id ? "Your club" : "—";
        }
      } catch (e) {}

      el("ov-days").addEventListener("change", function (e) { state.days = parseInt(e.target.value, 10) || 30; load(); });
      load();
    },
  };
  window.Overview = Overview;
})();
