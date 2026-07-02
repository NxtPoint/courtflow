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
      tb.appendChild(el("tr", {}, [
        el("td", { text: r[nameKey] || "—" }),
        el("td", { class: "num", text: String(r.lessons || 0) }),
        el("td", { class: "num", text: money(r.paid_minor, cur) }),
        el("td", { class: "num", text: money(r.owed_minor, cur) }),
        el("td", { class: "num", text: money(r.net_minor, cur) }),
      ]));
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

  // ---- transaction log / activity feed ---------------------------------------
  // entries: [{at, kind, title, detail, amount_minor, currency, direction('in'|'out'|'neutral')}]
  // One chronological, transparent "what happened" list shared by client / coach / owner.
  var ACT_ICON = {
    payment: "💳", refund: "↩️", order_created: "🧾", order_voided: "✖️",
    order_written_off: "🚫", commission_earned: "＋", refund_clawback: "↩️",
    arrears_accrued: "🎾", arrears_collected: "✅", arrears_written_off: "🚫",
    membership_started: "⭐", membership_cancelled: "✖️",
  };
  function activityFeed(entries, opts) {
    opts = opts || {};
    entries = entries || [];
    if (!entries.length) return el("div", { class: "cf-empty", text: opts.empty || "No activity yet." });
    var list = el("div", { class: "cf-list cf-act" });
    entries.forEach(function (e) {
      var dir = e.direction || "neutral";
      var amt = e.amount_minor || 0;
      var kids = [
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t" }, [
            el("span", { class: "cf-act-ic", text: (ACT_ICON[e.kind] || "•") + " " }),
            document.createTextNode(e.title || e.kind || "Activity"),
          ]),
          el("div", { class: "cf-item-s", text: [e.detail, e.at ? UI.fmtDate(e.at) : ""].filter(Boolean).join(" · ") }),
        ]),
      ];
      if (amt) {
        var sign = dir === "out" ? "−" : (dir === "in" ? "+" : "");
        kids.push(el("span", {
          class: "cf-chip" + (dir === "in" ? " cf-chip-good" : (dir === "out" ? " cf-chip-bad" : " cf-chip-muted")),
          text: sign + money(Math.abs(amt), e.currency),
        }));
      }
      list.appendChild(el("div", { class: "cf-item" }, kids));
    });
    return list;
  }

  window.CRMUI = {
    money: money,
    stats: stats,
    bars: bars,
    statementTable: statementTable,
    lineItems: lineItems,
    requestQueue: requestQueue,
    drawer: drawer,
    sectionHead: sectionHead,
    activityFeed: activityFeed,
  };
})();
