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
    if (d.available === false) {
      el("ov-kpis").innerHTML = '<div class="empty">' + esc(d.label || "This property") +
        ' is not available right now' + (d.reason === "not_configured" ? " (bridge not configured)." : ".") + '</div>';
      ["ch-visits","ch-signups","tbl-sources","tbl-pages","tbl-geo","tbl-settle","ov-nps"].forEach(function (id) {
        var n = el(id); if (n) n.innerHTML = '<div class="empty">—</div>';
      });
      return;
    }
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

  function renderAll(d) {
    var c = d.combined || {};
    el("ov-kpis").innerHTML = [
      card("Total visits", num(c.visits)),
      card("Unique visitors", num(c.unique_visitors)),
      card("Total customers", num(c.total_customers), "+" + num(c.new_customers) + " in period"),
      card("Bookings", num(c.bookings)),
    ].join("") +
    '<div class="kpi" style="grid-column:1/-1"><div class="lbl">Revenue (per business — not summed; mixed currency)</div><div style="display:flex;gap:24px;margin-top:8px;flex-wrap:wrap">' +
      (c.revenue_by_property || []).map(function (r) {
        return '<div><div class="val" style="font-size:1.3rem">' + esc(money(r.revenue_minor, r.currency)) +
          '</div><div class="sub">' + esc(r.label) + '</div></div>';
      }).join("") + '</div></div>';
    // Per-property quick lines for visits + customers.
    var props = (d.properties || []).filter(function (p) { return p.available !== false; });
    function strip(id, title, fn) {
      var node = el(id); if (!node) return;
      node.innerHTML = '<table><tbody>' + props.map(function (p) {
        return '<tr><td>' + esc(p.label) + '</td><td class="num">' + fn(p) + '</td></tr>';
      }).join("") + '</tbody></table>';
    }
    strip("tbl-sources", "", function (p) { return num((p.kpis || {}).visits) + " visits"; });
    strip("tbl-pages", "", function (p) { return num((p.kpis || {}).total_customers) + " customers"; });
    strip("tbl-geo", "", function (p) { return money((p.kpis || {}).revenue_minor, p.currency) + " revenue"; });
    el("tbl-settle").innerHTML = '<div class="empty">Per-business settlement — switch to a single business.</div>';
    el("ov-nps").innerHTML = '<div class="empty">NPS — switch to a single business.</div>';
    try { if (charts["ch-visits"]) charts["ch-visits"].dispose(); if (charts["ch-signups"]) charts["ch-signups"].dispose(); } catch (e) {}
    el("ch-visits").innerHTML = '<div class="empty">Combined counts above. Switch to a single business for trend charts.</div>';
    el("ch-signups").innerHTML = '<div class="empty">—</div>';
  }

  function render(d) {
    if (d && d.property === "all") return renderAll(d);
    return renderSingle(d || {});
  }

  var state = { days: 30, club_id: "", property: "courtflow" };

  async function load() {
    var auth = window.TFAuth;
    var qs = "?days=" + state.days + "&property=" + encodeURIComponent(state.property);
    // The club filter only narrows CourtFlow's own data.
    if (state.property === "courtflow" && state.club_id) qs += "&club_id=" + encodeURIComponent(state.club_id);
    var clubSel = el("ov-club");
    if (clubSel) clubSel.style.opacity = (state.property === "courtflow") ? "1" : "0.4";
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

      // Platform-admin gets a club filter + a business (property) switcher (1050 bridge).
      try {
        var who = await auth.apiJSON("/api/whoami");
        if (who && who.role === "platform_admin") {
          var sel = el("ov-club");
          try {
            var cl = await auth.apiJSON("/api/analytics/clubs");
            sel.innerHTML = '<option value="">All clubs</option>' +
              (cl.clubs || []).map(function (c) { return '<option value="' + esc(c.id) + '">' + esc(c.name) + '</option>'; }).join("");
            sel.style.display = "";
            sel.addEventListener("change", function () { state.club_id = sel.value; load(); });
          } catch (e) {}
          // Business switcher — only show Ten-Fifty5 / All when the bridge is configured.
          try {
            var pr = await auth.apiJSON("/api/analytics/properties");
            var psel = el("ov-property");
            var hasBridge = (pr.properties || []).some(function (p) { return p.id !== "courtflow" && p.available; });
            var opts = '<option value="courtflow">NextPoint / CourtFlow</option>';
            if (hasBridge) {
              opts += '<option value="ten-fifty5">Ten-Fifty5</option><option value="all">All businesses</option>';
              psel.innerHTML = opts; psel.style.display = "";
              psel.addEventListener("change", function () {
                state.property = psel.value;
                el("ov-scope").textContent = psel.options[psel.selectedIndex].text;
                load();
              });
            }
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
