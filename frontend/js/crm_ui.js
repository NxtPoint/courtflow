// crm_ui.js — the SHARED reporting / CRM component library (coach + owner consoles, Phase A4).
//
// Vanilla-JS, render-only components: each takes plain data (+ callbacks) and returns a DOM node
// (or opens an overlay), so the coach console (their slice) and the owner console (the whole club)
// render IDENTICALLY from the same primitives — the thing that guarantees reuse across the two
// flows. No data fetching here (the consoles fetch + pass data in). Reuses the existing cf-*
// design system: cf-stats/cf-stat (KPIs), cf-bars (trend), cf-table/.num, cf-list/cf-item,
// cf-summary, cf-chip, cf-btn*, and a small cf-drawer-* slide-over added to app.css.
//
// Depends on window.UI (el, money, fmtDate, fmtRange, esc, clear, toast). Load AFTER ui.js.
(function () {
  var UI = window.UI, el = function () { return UI.el.apply(UI, arguments); };
  function money(m, cur) { return UI.money(m, cur || "ZAR"); }

  // ---- KPI strip --------------------------------------------------------------
  // items: [{value, label}] — value already formatted (string/number).
  function stats(items) {
    var box = el("div", { class: "cf-stats" });
    (items || []).forEach(function (s) {
      box.appendChild(el("div", { class: "cf-stat" }, [
        el("div", { class: "cf-stat-v", text: String(s.value == null ? "—" : s.value) }),
        el("div", { class: "cf-stat-k", text: s.label || "" }),
      ]));
    });
    return box;
  }

  // ---- trend bars (CSS, no chart lib) ----------------------------------------
  // series: [{label, value, title?}]; opts.fmt(value)->string for the bar caption.
  function bars(series, opts) {
    opts = opts || {};
    series = series || [];
    if (!series.length) return el("div", { class: "cf-empty", text: opts.empty || "No history yet." });
    var max = series.reduce(function (m, s) { return Math.max(m, Number(s.value) || 0); }, 0) || 1;
    var box = el("div", { class: "cf-bars" });
    series.forEach(function (s) {
      var n = Number(s.value) || 0;
      box.appendChild(el("div", { class: "cf-bar", title: s.title || (s.label + ": " + n) }, [
        el("div", { class: "cf-bar-val", text: opts.fmt ? opts.fmt(s.value) : String(n) }),
        el("div", { class: "cf-bar-fill", style: "height:" + Math.round(n / max * 100) + "%" }),
        el("div", { class: "cf-bar-lbl", text: s.label }),
      ]));
    });
    return box;
  }

  // ---- statement table (per-client for the coach / per-coach for the client) --
  // rows: [{<nameKey>, lessons, paid_minor, owed_minor, net_minor}]
  // opts: {currency, nameKey='name', nameLabel='Name', empty}
  function statementTable(rows, opts) {
    opts = opts || {};
    rows = rows || [];
    var cur = opts.currency || "ZAR";
    var nameKey = opts.nameKey || "name";
    if (!rows.length) return el("div", { class: "cf-empty", text: opts.empty || "No lessons this month." });
    var t = el("table", { class: "cf-table" }, [
      el("thead", {}, [el("tr", {}, [
        el("th", { text: opts.nameLabel || "Name" }),
        el("th", { class: "num", text: "Lessons" }),
        el("th", { class: "num", text: "Paid" }),
        el("th", { class: "num", text: "Owed" }),
        el("th", { class: "num", text: "Net" }),
      ])]),
    ]);
    var tb = el("tbody");
    rows.forEach(function (r) {
      var tr = el("tr", {}, [
        el("td", { text: r[nameKey] || "—" }),
        el("td", { class: "num", text: String(r.lessons || 0) }),
        el("td", { class: "num", text: money(r.paid_minor, cur) }),
        el("td", { class: "num", text: money(r.owed_minor, cur) }),
        el("td", { class: "num", text: money(r.net_minor, cur) }),
      ]);
      if (opts.onRow) { tr.style.cursor = "pointer"; tr.addEventListener("click", function () { opts.onRow(r); }); }
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    return t;
  }

  // ---- outstanding line items (arrears) with optional actions -----------------
  // items: [{id, gross_minor, starts_at, ...label fields}]
  // opts: {currency, label(it)->title, sub(it)->subtitle, actions:[{label, tone?, onClick(it)}], empty}
  function lineItems(items, opts) {
    opts = opts || {};
    items = items || [];
    var cur = opts.currency || "ZAR";
    var actions = opts.actions || [];
    if (!items.length) return el("div", { class: "cf-empty", text: opts.empty || "Nothing outstanding." });
    var list = el("div", { class: "cf-list" });
    items.forEach(function (it) {
      var title = opts.label ? opts.label(it) : (it.client_name || it.coach_name || "Lesson");
      var written = it.status === "written_off";
      var sub = opts.sub ? opts.sub(it) : (it.starts_at ? UI.fmtDate(it.starts_at) : "");
      // A written-off line stays VISIBLE for transparency — badged, read-only, with its reason.
      if (written && it.note) sub = (sub ? sub + " · " : "") + "Reason: " + it.note;
      var amt = el("span", { class: "cf-chip" + (written ? " cf-chip-muted" : ""),
        text: (written ? "Written off · " : "") + money(it.gross_minor, cur) });
      if (written) amt.style.textDecoration = "line-through";
      var kids = [
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: title }),
          el("div", { class: "cf-item-s", text: sub }),
        ]),
        amt,
      ];
      // Actions only on still-owed lines; a written-off/collected line is immutable.
      if (actions.length && !written) {
        var row = el("div", { class: "cf-row", style: "gap:6px" });
        actions.forEach(function (a) {
          row.appendChild(el("button", {
            class: "cf-btn cf-btn-sm" + (a.tone ? (" cf-btn-" + a.tone) : ""),
            type: "button", text: a.label, onclick: function () { a.onClick(it); },
          }));
        });
        kids.push(row);
      }
      list.appendChild(el("div", { class: "cf-item" + (written ? " cf-item-off" : "") }, kids));
    });
    return list;
  }

  // ---- pending-requests queue (lesson accept/propose/decline) -----------------
  // items: [{id, title, sub?, status('requested'|'proposed'), starts_at, ends_at}]
  // opts: {onAccept(it), onPropose(it), onDecline(it), empty}
  function requestQueue(items, opts) {
    opts = opts || {};
    items = items || [];
    if (!items.length) return el("div", { class: "cf-empty", text: opts.empty || "No pending requests." });
    var list = el("div", { class: "cf-list" });
    items.forEach(function (it) {
      var actions = el("div", { class: "cf-row", style: "gap:6px" });
      if (opts.onAccept) actions.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", type: "button", text: "Accept", onclick: function () { opts.onAccept(it); } }));
      if (opts.onPropose) actions.appendChild(el("button", { class: "cf-btn cf-btn-sm", type: "button", text: "Propose time", onclick: function () { opts.onPropose(it); } }));
      if (opts.onDecline) actions.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", type: "button", text: "Decline", onclick: function () { opts.onDecline(it); } }));
      list.appendChild(el("div", { class: "cf-item" }, [
        el("span", { class: "cf-chip " + (it.status === "requested" ? "held" : "lesson"),
          text: it.status === "requested" ? "requested" : "proposed" }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: it.title || "Lesson" }),
          el("div", { class: "cf-item-s",
            text: it.sub || (it.starts_at ? UI.fmtRange(it.starts_at, it.ends_at) : "") }),
        ]),
        actions,
      ]));
    });
    return list;
  }

  // ---- 360 drawer (client / coach detail slide-over) -------------------------
  // opts: {title, subtitle?, sections:[{h?, rows?:[[k,v]], node?}]} -> returns a close() fn.
  function drawer(opts) {
    opts = opts || {};
    var overlay = el("div", { class: "cf-drawer-bg", onclick: function (ev) { if (ev.target === overlay) close(); } });
    function close() {
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      document.removeEventListener("keydown", onKey);
    }
    function onKey(e) { if (e.key === "Escape") close(); }
    var body = el("div", { class: "cf-drawer-body" });
    (opts.sections || []).forEach(function (sec) {
      if (sec.h) body.appendChild(el("h3", { style: "margin:16px 0 8px", text: sec.h }));
      if (sec.rows) {
        var sm = el("div", { class: "cf-summary" });
        sec.rows.forEach(function (r) {
          sm.appendChild(el("div", { class: "cf-summary-row" }, [
            el("span", { class: "cf-summary-k", text: r[0] }),
            el("span", { class: "cf-summary-v", text: r[1] == null ? "—" : String(r[1]) }),
          ]));
        });
        body.appendChild(sm);
      }
      if (sec.node) body.appendChild(sec.node);
    });
    overlay.appendChild(el("div", { class: "cf-drawer" }, [
      el("div", { class: "cf-drawer-head" }, [
        el("div", {}, [
          el("h2", { style: "margin:0;font-size:1.12rem", text: opts.title || "" }),
          opts.subtitle ? el("div", { class: "cf-muted", style: "font-size:.86rem;margin-top:2px", text: opts.subtitle }) : null,
        ].filter(Boolean)),
        el("button", { class: "cf-sheet-x", type: "button", text: "✕", onclick: close, title: "Close" }),
      ]),
      body,
    ]));
    document.body.appendChild(overlay);
    document.addEventListener("keydown", onKey);
    return close;
  }

  // ---- section header with a trailing action/link ----------------------------
  function sectionHead(title, trailing) {
    return el("div", { class: "cf-sec-head" }, [el("h2", { text: title }), trailing || null].filter(Boolean));
  }

  // ---- green greeting ribbon (the shared console header) ----------------------
  // opts: {title, subtitle?, chip?, actions:[{label, tone?, onClick}]}. Mirrors the client Home
  // ribbon (.cf-greet) so the coach + owner consoles carry the SAME header pattern, with the
  // profile-edit actions living right in the ribbon.
  function greetBand(opts) {
    opts = opts || {};
    var left = el("div", {}, [el("h1", { text: opts.title || "" })]);
    if (opts.subtitle) left.appendChild(el("p", { text: opts.subtitle }));
    if (opts.actions && opts.actions.length) {
      var row = el("div", { class: "cf-row", style: "gap:8px;margin-top:10px;flex-wrap:wrap" });
      opts.actions.forEach(function (a) {
        if (!a) return;
        row.appendChild(el("button", {
          class: "cf-btn cf-btn-sm" + (a.tone ? (" cf-btn-" + a.tone) : ""),
          type: "button", text: a.label, onclick: function () { if (a.onClick) a.onClick(); },
        }));
      });
      left.appendChild(row);
    }
    var kids = [left];
    if (opts.chip) kids.push(el("span", { class: "cf-greet-plan", text: opts.chip }));
    return el("div", { class: "cf-greet" }, kids);
  }

  // ---- transaction log / activity feed ---------------------------------------
  // entries: [{at, kind, title, detail, amount_minor, currency, direction('in'|'out'|'neutral')}]
  // One chronological, transparent "what happened" list shared by client / coach / owner.
  // One chronological "what happened" list shared by client / coach / owner. Each row is the SAME
  // window.UI.logRow the transaction RECORD uses — one implementation (FRONTEND-STANDARDISATION #7).
  function activityFeed(entries, opts) {
    opts = opts || {};
    entries = entries || [];
    if (!entries.length) return el("div", { class: "cf-empty", text: opts.empty || "No activity yet." });
    var list = el("div", { class: "cf-list cf-act" });
    entries.forEach(function (e) { list.appendChild(window.UI.logRow(e)); });
    return list;
  }

  // ---- activity + spend blocks (ONE renderer, shared: client Home modules + Client 360 rollup) ----
  // Fed by billing.me.activity_summary. Home passes onOpen/onSettle (tappable → Client 360); the
  // record renders them read-only at the top of the 360. cf-* styled, no emoji. (Golden rule.)
  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function monthLabel(ym) {
    if (!ym || String(ym).length < 7) return ym || "";
    var m = parseInt(String(ym).slice(5, 7), 10);
    return (MON[m - 1] || "") + " " + String(ym).slice(0, 4);
  }
  function fmtDur(mins) {
    mins = Math.round(mins || 0); var h = Math.floor(mins / 60), m = mins % 60;
    if (!mins) return "0m";
    return (h ? h + "h" : "") + (m ? (h ? " " : "") + m + "m" : "");
  }
  var SVC_FILL = { lesson: "#1F5BAB", court: "var(--green,#0E7A47)", class: "var(--lime-700,#5B7A12)" };

  // Stacked weekly bars (SVG, no chart lib) — sessions per week, coloured by service type. Shows
  // EVERY week of the month (>=4 slots) with headroom, so a single active week reads as a normal bar
  // among empty ones — not one giant slab.
  function weekChart(byWeek) {
    byWeek = byWeek || [];
    var maxW = byWeek.reduce(function (m, w) { return Math.max(m, w.week || 0); }, 0);
    var nWeeks = Math.max(4, maxW), map = {};
    byWeek.forEach(function (w) { map[w.week] = w; });
    var weeks = [];
    for (var i = 1; i <= nWeeks; i++) weeks.push(map[i] || { week: i, lesson: 0, court: 0, class: 0 });
    var W = 340, H = 120, pad = 16, base = H - 20, top = 14;
    var max = weeks.reduce(function (mx, w) { return Math.max(mx, (w.lesson || 0) + (w.court || 0) + (w["class"] || 0)); }, 0) || 1;
    var slot = (W - pad * 2) / weeks.length, bw = Math.min(38, slot * 0.5);
    var s = ["<svg viewBox='0 0 " + W + " " + H + "' role='img' aria-label='Sessions per week by type'>"];
    s.push("<line x1='0' y1='" + base + "' x2='" + W + "' y2='" + base + "' stroke='var(--border,#E4EBE5)'/>");
    weeks.forEach(function (w, i) {
      var cx = pad + slot * i + slot / 2, x = cx - bw / 2, y = base;
      ["court", "lesson", "class"].forEach(function (k) {
        var v = w[k] || 0; if (!v) return;
        var h = v / max * (base - top); y -= h;
        s.push("<rect x='" + x.toFixed(1) + "' y='" + y.toFixed(1) + "' width='" + bw.toFixed(1) + "' height='" + h.toFixed(1) + "' rx='4' fill='" + SVC_FILL[k] + "'/>");
      });
      s.push("<text x='" + cx.toFixed(1) + "' y='" + (H - 3) + "' text-anchor='middle' fill='var(--dim,#95A69C)' font-size='11' font-weight='600'>Wk " + (w.week || i + 1) + "</text>");
    });
    s.push("</svg>");
    var box = el("div", { class: "cf-chart" }); box.innerHTML = s.join(""); return box;
  }

  function _moduleCard(eyebrow, month, bodyNode, drill) {
    var card = el("div", { class: "cf-mod" + (drill ? " cf-mod-tap" : "") });
    card.appendChild(el("div", { class: "cf-mod-h" }, [
      el("span", { class: "cf-eyebrow", text: eyebrow }),
      month ? el("span", { class: "cf-muted num", style: "font-size:.82rem;font-weight:600", text: monthLabel(month) }) : null,
    ].filter(Boolean)));
    card.appendChild(bodyNode);
    if (drill) {
      card.appendChild(el("div", { class: "cf-drill" }, [el("span", { text: drill.label }), el("span", { class: "cf-drill-a", text: "›" })]));
      card.addEventListener("click", drill.onOpen);
    }
    return card;
  }

  function activityBlock(a, opts) {
    opts = opts || {}; a = a || {}; var c = a.counts || {};
    function metric(v, label, kind) {
      return el("div", { class: "cf-metric " + kind }, [
        el("div", { class: "cf-metric-v num", text: String(v || 0) }),
        el("div", { class: "cf-metric-k", text: label }),
      ]);
    }
    var body = el("div", { class: "cf-mod-b" }, [
      el("div", { class: "cf-metrics" }, [metric(c.lesson, "Lessons", "lesson"), metric(c.court, "Court", "court"), metric(c["class"], "Classes", "class")]),
    ]);
    if (a.minutes) body.appendChild(el("div", { class: "cf-totline" }, [
      el("span", { class: "cf-totline-v num", text: fmtDur(a.minutes) }),
      el("span", { class: "cf-muted", style: "font-size:.88rem", text: "on court · " + (c.total || 0) + " session" + (c.total === 1 ? "" : "s") }),
    ]));
    if ((a.by_week || []).length && !opts.noChart) body.appendChild(weekChart(a.by_week));
    return _moduleCard("Activity", a.month, body, opts.onOpen ? { label: "View all activity", onOpen: opts.onOpen } : null);
  }

  function spendBlock(a, opts) {
    opts = opts || {}; a = a || {}; var cur = a.currency || "ZAR";
    var svc = a.by_service || [], billed = a.billed_minor || 0, paid = a.paid_minor || 0, owe = a.outstanding_minor || 0;
    var body = el("div", { class: "cf-mod-b" });
    body.appendChild(el("div", { style: "display:flex;align-items:baseline;gap:8px" }, [
      el("span", { class: "num", style: "font-size:1.55rem;font-weight:800", text: money(billed, cur) }),
      el("span", { class: "cf-muted", style: "font-size:.9rem", text: "billed" }),
    ]));
    if (svc.length && billed > 0) {
      var seg = el("div", { class: "cf-segbar" });
      svc.forEach(function (x) { if (x.billed_minor > 0) seg.appendChild(el("div", { class: "cf-seg " + x.key, style: "width:" + Math.max(2, Math.round(x.billed_minor / billed * 100)) + "%" })); });
      body.appendChild(seg);
      var leg = el("div", { class: "cf-legend" });
      svc.forEach(function (x) {
        leg.appendChild(el("div", { class: "cf-lrow" }, [
          el("span", { class: "cf-swatch " + x.key }), el("span", { text: x.label }),
          el("span", { class: "cf-lct", text: "· " + x.count }), el("span", { class: "cf-lamt num", text: money(x.billed_minor, cur) }),
        ]));
      });
      body.appendChild(leg);
    }
    body.appendChild(el("div", { class: "cf-paybar" }, [
      el("div", { class: "cf-paycell" }, [el("div", { class: "cf-payk", text: "Paid" }), el("div", { class: "cf-payv num", text: money(paid, cur) })]),
      el("div", { class: "cf-paycell " + (owe > 0 ? "owe-bad" : "owe-ok") }, [el("div", { class: "cf-payk", text: "Outstanding" }), el("div", { class: "cf-payv num", text: money(owe, cur) })]),
    ]));
    // Clarify where the rest went when billed > paid + outstanding (refunds / write-offs / cancellations)
    // — otherwise "billed R1380 · paid R0 · nothing outstanding" reads as a puzzle.
    var reversed = Math.max(0, billed - paid - owe);
    if (reversed > 0) body.appendChild(el("div", { class: "cf-muted", style: "margin-top:9px;font-size:.82rem", text: money(reversed, cur) + " refunded or written off this month" }));
    if (owe > 0 && opts.onSettle) {
      var b = el("button", { class: "cf-settle", text: "Settle " + money(owe, cur) + " now" });
      b.addEventListener("click", function (e) { e.stopPropagation(); opts.onSettle(); });
      body.appendChild(b);
    } else if (billed > 0) {
      body.appendChild(el("div", { class: "cf-settled", text: "All settled — nothing outstanding." }));
    }
    return _moduleCard("Billing", a.month, body, opts.onOpen ? { label: "View statement & history", onOpen: opts.onOpen } : null);
  }

  // ---- money summary band (the reconciling triad, shared coach + owner) -------
  // The ONE money-at-a-glance for a month: Billed → Collected → Outstanding, then a config-driven
  // breakdown line (coach: you keep / club commission / rent; owner: club keeps / coach payouts due).
  // Golden rule: ONE presenter, role differences are the cfg passed in. Caller wraps it in a card().
  //   cfg: {currency, month:'YYYY-MM', billed_minor, collected_minor, outstanding_minor,
  //         breakdown:[{label, value_minor, tone?('bad'|'good'), sub?}], footnote?}
  function moneySummary(cfg) {
    cfg = cfg || {};
    var cur = cfg.currency || "ZAR";
    var billed = cfg.billed_minor || 0, coll = cfg.collected_minor || 0, out = cfg.outstanding_minor || 0;
    var wrap = el("div", { class: "cf-moneysum" });
    if (cfg.month) wrap.appendChild(el("div", { class: "cf-muted", style: "font-size:.8rem;font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-bottom:8px", text: "This month · " + monthLabel(cfg.month) }));
    // The triad — three cells; Outstanding tinted when there's money still owed.
    function cell(label, val, cls) {
      return el("div", { class: "cf-paycell " + (cls || "") }, [
        el("div", { class: "cf-payk", text: label }),
        el("div", { class: "cf-payv num", text: money(val, cur) }),
      ]);
    }
    wrap.appendChild(el("div", { class: "cf-paybar" }, [
      cell("Billed", billed, ""),
      cell("Collected", coll, "owe-ok"),
      cell("Outstanding", out, out > 0 ? "owe-bad" : ""),
    ]));
    // Breakdown line (label … amount), each on its own row.
    var rows = (cfg.breakdown || []).filter(Boolean);
    if (rows.length) {
      var box = el("div", { style: "margin-top:12px;border-top:1px solid var(--border);padding-top:10px" });
      rows.forEach(function (r) {
        box.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:baseline;margin:5px 0" }, [
          el("div", {}, [
            el("span", { class: (r.tone === "bad" ? "" : "cf-muted"), style: "font-size:.9rem", text: r.label }),
            r.sub ? el("span", { class: "cf-muted", style: "font-size:.78rem;margin-left:6px", text: r.sub }) : null,
          ].filter(Boolean)),
          el("span", { class: "num", style: "font-weight:700" + (r.tone === "bad" ? ";color:var(--danger)" : (r.tone === "good" ? ";color:var(--success)" : "")), text: money(r.value_minor || 0, cur) }),
        ]));
      });
      wrap.appendChild(box);
    }
    if (cfg.footnote) wrap.appendChild(el("div", { class: "cf-muted", style: "margin-top:8px;font-size:.82rem", text: cfg.footnote }));
    return wrap;
  }

  // ---- statement fold (the money as an OUTCOME of bookings) -------------------
  // The owner's model, ONE presenter (shared coach → admin → client later): a booking/statement folds
  //   Billed − Discount − Written-off = Invoiced ;  Invoiced = Paid + Outstanding
  // so it ALWAYS reconciles. Caller wraps it in a card().
  //   cfg: {currency, month?, totals:{billed_minor,discount_minor,written_off_minor,invoiced_minor,
  //         paid_minor,outstanding_minor,refunded_minor?}, extra:[{label,value_minor,tone?,sub?}], compact?}
  function statementFold(cfg) {
    cfg = cfg || {};
    var cur = cfg.currency || "ZAR", t = cfg.totals || {};
    var wrap = el("div", { class: "cf-fold" });
    if (cfg.month) wrap.appendChild(el("div", { class: "cf-muted", style: "font-size:.8rem;font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-bottom:8px", text: "This month · " + monthLabel(cfg.month) }));
    function line(label, val, opt) {
      opt = opt || {};
      return el("div", { class: "cf-row", style: "justify-content:space-between;align-items:baseline;margin:" + (opt.tight ? "3px" : "5px") + " 0" }, [
        el("span", { class: opt.strong ? "" : "cf-muted", style: "font-size:" + (opt.strong ? ".95rem;font-weight:700" : ".9rem"), text: label }),
        el("span", { class: "num", style: "font-weight:" + (opt.strong ? "800" : "600") + (opt.tone === "bad" ? ";color:var(--danger)" : ""), text: (opt.minus ? "− " : "") + money(val || 0, cur) }),
      ]);
    }
    wrap.appendChild(line("Billed", t.billed_minor, {}));
    if ((t.discount_minor || 0) > 0) wrap.appendChild(line("Discount", t.discount_minor, { minus: true, tight: true }));
    if ((t.written_off_minor || 0) > 0) wrap.appendChild(line("Written off", t.written_off_minor, { minus: true, tight: true }));
    wrap.appendChild(el("div", { style: "border-top:1px solid var(--border);margin:6px 0 2px" }));
    wrap.appendChild(line("Invoiced", t.invoiced_minor, { strong: true }));
    // Paid vs Outstanding split.
    var out = t.outstanding_minor || 0;
    wrap.appendChild(el("div", { class: "cf-paybar", style: "margin-top:10px" }, [
      el("div", { class: "cf-paycell owe-ok" }, [el("div", { class: "cf-payk", text: "Paid" }), el("div", { class: "cf-payv num", text: money(t.paid_minor, cur) })]),
      el("div", { class: "cf-paycell " + (out > 0 ? "owe-bad" : "") }, [el("div", { class: "cf-payk", text: "Outstanding" }), el("div", { class: "cf-payv num", text: money(out, cur) })]),
    ]));
    if ((t.refunded_minor || 0) > 0) wrap.appendChild(el("div", { class: "cf-muted", style: "margin-top:8px;font-size:.82rem", text: money(t.refunded_minor, cur) + " refunded this month" }));
    var extra = (cfg.extra || []).filter(Boolean);
    if (extra.length) {
      var box = el("div", { style: "margin-top:12px;border-top:1px solid var(--border);padding-top:10px" });
      extra.forEach(function (r) {
        box.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:baseline;margin:5px 0" }, [
          el("div", {}, [
            el("span", { class: (r.tone === "bad" ? "" : "cf-muted"), style: "font-size:.9rem", text: r.label }),
            r.sub ? el("span", { class: "cf-muted", style: "font-size:.78rem;margin-left:6px", text: r.sub }) : null,
          ].filter(Boolean)),
          el("span", { class: "num", style: "font-weight:700" + (r.tone === "bad" ? ";color:var(--danger)" : (r.tone === "good" ? ";color:var(--success)" : "")), text: money(r.value_minor || 0, cur) }),
        ]));
      });
      wrap.appendChild(box);
    }
    return wrap;
  }

  window.CRMUI = {
    money: money,
    stats: stats,
    moneySummary: moneySummary,
    statementFold: statementFold,
    bars: bars,
    weekChart: weekChart,
    activityBlock: activityBlock,
    spendBlock: spendBlock,
    monthLabel: monthLabel,
    statementTable: statementTable,
    lineItems: lineItems,
    requestQueue: requestQueue,
    drawer: drawer,
    sectionHead: sectionHead,
    activityFeed: activityFeed,
    greetBand: greetBand,
  };
})();
