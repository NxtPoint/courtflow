// ui.js — small shared UI helpers for the CourtFlow portal SPAs (vanilla, no deps).
// Formatting, DOM helpers, toasts, and a settlement-mode catalogue. Pure presentation.
(function () {
  // Club timezone — NextPoint is Africa/Johannesburg (docs/03 §10). Display in club tz.
  var CLUB_TZ = window.__CLUB_TZ || "Africa/Johannesburg";

  function _dt(iso) { try { return new Date(iso); } catch (e) { return null; } }

  function fmtTime(iso) {
    var d = _dt(iso); if (!d) return "";
    return d.toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: CLUB_TZ });
  }
  function fmtDate(iso) {
    var d = _dt(iso); if (!d) return "";
    return d.toLocaleDateString("en-ZA", { weekday: "short", day: "numeric", month: "short", timeZone: CLUB_TZ });
  }
  function fmtDateTime(iso) {
    var d = _dt(iso); if (!d) return "";
    return fmtDate(iso) + " " + fmtTime(iso);
  }
  function fmtRange(startIso, endIso) {
    return fmtDate(startIso) + " · " + fmtTime(startIso) + "–" + fmtTime(endIso);
  }
  // YYYY-MM-DD for date inputs / API date_from-date_to (interpreted in club tz server-side).
  function dateKey(d) {
    d = d || new Date();
    var y = d.getFullYear(), m = ("0" + (d.getMonth() + 1)).slice(-2), day = ("0" + d.getDate()).slice(-2);
    return y + "-" + m + "-" + day;
  }
  function addDays(d, n) { var x = new Date(d); x.setDate(x.getDate() + n); return x; }

  // amount_minor (cents) -> "R123.45". Currency from billing config.
  function money(minor, currency) {
    var n = Number(minor);
    if (minor === null || minor === undefined || isNaN(n)) return "—";
    var sym = ({ ZAR: "R", USD: "$", GBP: "£", EUR: "€" })[currency] || (currency ? currency + " " : "R");
    return sym + (n / 100).toFixed(2);
  }

  // ---- settlement modes (docs/05 §5) — what the wizard offers per club policy ----
  // online is added dynamically only when billingConfig.online_enabled.
  var SETTLEMENT = {
    at_court:          { label: "Pay at court",       hint: "Settle at the front desk (cash/card)." },
    monthly_account:   { label: "Monthly account",    hint: "Charged to your member tab; billed monthly." },
    membership_covered:{ label: "Covered by membership", hint: "Included in your membership — no charge." },
    online:            { label: "Pay online now",     hint: "Secure card payment to confirm." },
    free:              { label: "Complimentary",      hint: "No charge." },
  };
  function settlementLabel(mode) { return (SETTLEMENT[mode] || {}).label || mode; }

  // ---- DOM helpers -----------------------------------------------------------
  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "class") n.className = attrs[k];
      else if (k === "html") n.innerHTML = attrs[k];
      else if (k === "text") n.textContent = attrs[k];
      else if (k.indexOf("on") === 0 && typeof attrs[k] === "function") n.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) {
      if (c == null) return;
      n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return n;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }
  function clear(node) {
    // Also drop the loading state: .cf-loading paints a CSS ::before spinner, so emptying a
    // node WITHOUT removing the class leaves the spinner animating over the new content.
    if (node && node.classList) node.classList.remove("cf-loading");
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  // ---- toast / inline status -------------------------------------------------
  function toast(msg, kind) {
    var host = document.getElementById("cf-toasts");
    if (!host) {
      host = el("div", { id: "cf-toasts", class: "cf-toasts" });
      document.body.appendChild(host);
    }
    var t = el("div", { class: "cf-toast cf-toast-" + (kind || "info"), text: msg });
    host.appendChild(t);
    setTimeout(function () { t.classList.add("cf-toast-out"); }, 3200);
    setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 3600);
  }
  function errMsg(e) {
    if (!e) return "Something went wrong.";
    if (e.body && (e.body.message || e.body.error)) return e.body.message || e.body.error;
    return e.message || "Something went wrong.";
  }

  // Group availability slots by club-local day key for rendering.
  function groupByDay(slots) {
    var by = {};
    (slots || []).forEach(function (sl) {
      var d = _dt(sl.start); if (!d) return;
      var key = d.toLocaleDateString("en-CA", { timeZone: CLUB_TZ }); // YYYY-MM-DD
      (by[key] = by[key] || []).push(sl);
    });
    return by;
  }

  // ---- lifecycle (Active / Deactivated / Terminated) — one consistent model everywhere -------
  function lifecycleBar(current, onChange) {
    var bar = el("div", { class: "cf-lifefilter" });
    [["all", "All"], ["active", "Active"], ["deactivated", "Deactivated"], ["terminated", "Terminated"]].forEach(function (o) {
      bar.appendChild(el("button", { type: "button", class: current === o[0] ? "on" : "", text: o[1], onclick: function () { onChange(o[0]); } }));
    });
    return bar;
  }
  // A horizontal sub-tab strip (underline style). items = [[key,label], …]; onChange(key).
  function subtabs(current, items, onChange) {
    var bar = el("div", { class: "cf-subtabs" });
    items.forEach(function (o) {
      bar.appendChild(el("button", { type: "button", class: current === o[0] ? "on" : "", text: o[1], onclick: function () { onChange(o[0]); } }));
    });
    return bar;
  }
  // Row actions for an item's status; `set(newStatus)` performs the change. Returns button elements
  // (Deactivate/Reactivate + Terminate). Clicks stop propagation so they don't trigger a row's edit.
  function lifeActions(status, set, opts) {
    opts = opts || {};
    function b(label, tone, ns, conf) {
      return el("button", { class: "cf-btn cf-btn-sm" + (tone ? " " + tone : ""), text: label,
        onclick: function (ev) { ev.stopPropagation(); if (conf && !window.confirm(conf)) return; set(ns); } });
    }
    if (status === "terminated") return [b("Reactivate", "", "active")];
    return [
      b(status === "deactivated" ? "Reactivate" : "Deactivate", "", status === "deactivated" ? "active" : "deactivated"),
      b("Terminate", "cf-btn-danger", "terminated", opts.terminateConfirm || "Terminate this? It's kept for history but removed from use."),
    ];
  }
  // One status vocabulary (booking · payment · lifecycle), role-NEUTRAL labels. Shared by every app
  // + the widgets, so a chip reads the same everywhere (FRONTEND-STANDARDISATION).
  function statusChip(status) {
    var m = {
      confirmed: ["confirmed", "Confirmed"], held: ["held", "Pending"], completed: ["ok", "Completed"],
      cancelled: ["cancelled", "Cancelled"], no_show: ["cancelled", "No-show"],
      requested: ["held", "Requested"], proposed: ["held", "Proposed"],
      enrolled: ["confirmed", "Enrolled"], waitlisted: ["held", "Waitlisted"],
      paid: ["confirmed", "Paid"], owed: ["held", "Owed"], pending: ["held", "Pending"],
      refunded: ["cancelled", "Refunded"], covered: ["court", "Covered"], written_off: ["cancelled", "Written off"],
      discounted: ["confirmed", "Discounted"],
      active: ["ok", "Active"], deactivated: ["held", "Deactivated"], terminated: ["cancelled", "Terminated"],
    };
    var e = m[status] || ["", status || "active"];
    return el("span", { class: "cf-chip " + e[0], text: e[1] });
  }

  // ---- shared DOM helpers (promoted from the three role apps — ONE implementation each;
  // FRONTEND-STANDARDISATION.md Wave 1). Pure builders: no role logic, no routing state. ----
  function card(children, extra) { return el("div", { class: "cf-card" + (extra ? " " + extra : "") }, children); }
  function backBar(label, hash) {
    return el("div", { class: "cf-backbar" }, [
      el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "‹ " + (label || "Back"),
        onclick: function () { if (hash) location.hash = hash; else history.back(); } }),
    ]);
  }
  function kv(k, v) {
    return el("div", { class: "cf-kv" }, [el("div", { class: "cf-kv-k", text: k }),
      el("div", { class: "cf-kv-v" }, typeof v === "string" ? [document.createTextNode(v)] : [v])]);
  }
  // Slim on-brand page header for drill-through screens (back chip + title) so no page looks barren.
  // backHash omitted → history.back(). One consistent header everywhere instead of a bare back bar.
  function pageHeader(title, backLabel, backHash) {
    return el("div", { class: "cf-pagehead" }, [
      el("button", { class: "cf-ph-back", type: "button", text: "‹ " + (backLabel || "Back"),
        onclick: function () { if (backHash) location.hash = backHash; else history.back(); } }),
      el("h1", { class: "cf-ph-title", text: title || "" }),
    ]);
  }
  function modal(title, opts) {
    opts = opts || {};
    var bg = el("div", { class: "cf-modal-bg" }), body = el("div", {});
    bg.appendChild(el("div", { class: "cf-modal" + (opts.lg ? " cf-modal-lg" : "") }, [
      el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [
        el("h2", { style: "margin:0", text: title }),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "✕", onclick: function () { close(); } }),
      ]), body,
    ]));
    document.body.appendChild(bg);
    function close() { if (bg.parentNode) document.body.removeChild(bg); }
    return { body: body, close: close };
  }
  function toLocal(iso) {
    try { var d = new Date(iso), p = function (n) { return (n < 10 ? "0" : "") + n; };
      return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" + p(d.getHours()) + ":" + p(d.getMinutes()); }
    catch (e) { return ""; }
  }
  // The .ics lives on the API host and needs auth — fetch via apiFetch (base + Bearer), then download.
  function addToCalendar(icsUrl) {
    window.TFAuth.apiFetch(icsUrl).then(function (r) { if (!r.ok) throw new Error("Couldn't build the calendar file."); return r.blob(); })
      .then(function (blob) { var u = URL.createObjectURL(blob); var a = document.createElement("a"); a.href = u; a.download = "booking.ics"; document.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(u); }, 1500); })
      .catch(function (e) { toast(errMsg(e), "error"); });
  }

  // ---- ONE transaction-log row (a chronological "what happened" entry) --------
  // Used by the transaction RECORD (Widgets.TransactionDetail) AND the account activity feed, so a
  // log line reads identically everywhere. entry = {at, kind, title|label, detail, amount_minor,
  // direction('in'|'out'|'neutral'), currency}. amount is SIGNED for display via `direction`.
  var _LOG_ICON = {
    payment: "💳", refund: "↩️", order_created: "🧾", order_voided: "✖️", order_written_off: "🚫",
    commission_earned: "＋", refund_clawback: "↩️", arrears_accrued: "🎾", arrears_collected: "✅",
    arrears_written_off: "🚫", membership_started: "⭐", membership_cancelled: "✖️",
  };
  function logRow(e, currency) {
    var dir = e.direction || "neutral", amt = e.amount_minor || 0;
    var kids = [el("div", { class: "cf-item-main" }, [
      el("div", { class: "cf-item-t" }, [
        el("span", { class: "cf-act-ic", text: (_LOG_ICON[e.kind] || "•") + " " }),
        document.createTextNode(e.title || e.label || e.kind || "Activity"),
      ]),
      el("div", { class: "cf-item-s", text: [e.detail, e.at ? fmtDate(e.at) : ""].filter(Boolean).join(" · ") }),
    ])];
    if (amt) {
      var sign = dir === "out" ? "−" : (dir === "in" ? "+" : "");
      kids.push(el("span", {
        class: "cf-chip" + (dir === "in" ? " cf-chip-good" : (dir === "out" ? " cf-chip-bad" : " cf-chip-muted")),
        text: sign + money(Math.abs(amt), e.currency || currency),
      }));
    }
    return el("div", { class: "cf-item" }, kids);
  }

  // ---- anchored dropdown menu (the avatar "account" menu — ONE implementation, all apps) ----
  // items: array of {label, onClick, tone?} | "-" (a separator). Positions under/right of anchor;
  // closes on outside click, Escape, scroll or resize. Returns its close() fn.
  var _openMenu = null;
  function menu(anchorEl, items) {
    if (_openMenu) _openMenu();
    var m = el("div", { class: "cf-menu", role: "menu" });
    (items || []).forEach(function (it) {
      if (it === "-" || (it && it.divider)) { m.appendChild(el("div", { class: "cf-menu-sep" })); return; }
      m.appendChild(el("button", { class: "cf-menu-item" + (it.tone ? " cf-menu-" + it.tone : ""), type: "button",
        text: it.label, onclick: function (ev) { ev.stopPropagation(); close(); if (it.onClick) it.onClick(); } }));
    });
    document.body.appendChild(m);
    var r = anchorEl.getBoundingClientRect();
    m.style.top = Math.round(r.bottom + 6) + "px";
    m.style.right = Math.round(Math.max(8, window.innerWidth - r.right)) + "px";
    function close() {
      if (m.parentNode) m.parentNode.removeChild(m);
      document.removeEventListener("mousedown", onDoc, true);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("resize", close); window.removeEventListener("scroll", close, true);
      if (_openMenu === close) _openMenu = null;
    }
    function onKey(e) { if (e.key === "Escape") close(); }
    function onDoc(e) { if (!m.contains(e.target) && !anchorEl.contains(e.target)) close(); }
    setTimeout(function () {
      document.addEventListener("mousedown", onDoc, true);
      document.addEventListener("keydown", onKey, true);
      window.addEventListener("resize", close); window.addEventListener("scroll", close, true);
    }, 0);
    _openMenu = close;
    return close;
  }

  // Open/download a Bearer-auth'd binary file (e.g. an invoice/receipt PDF) — plain links can't
  // carry the token. Fetches via TFAuth.apiFetch → blob → opens in a new tab (falls back to a
  // download if the popup is blocked). Shared by all three SPAs.
  async function openAuthedFile(path, filename) {
    var auth = window.TFAuth;
    if (!auth || !auth.apiFetch) { toast("Sign-in required.", "warn"); return; }
    try {
      var res = await auth.apiFetch(path);
      if (!res || !res.ok) throw new Error("Could not load file (" + (res && res.status) + ")");
      var blob = await res.blob();
      var url = URL.createObjectURL(blob);
      var w = window.open(url, "_blank");
      if (!w) {
        var a = document.createElement("a");
        a.href = url; a.download = filename || "document.pdf";
        document.body.appendChild(a); a.click(); a.remove();
      }
      setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
    } catch (e) { toast(errMsg(e), "error"); }
  }

  window.UI = {
    CLUB_TZ: CLUB_TZ,
    fmtTime: fmtTime, fmtDate: fmtDate, fmtDateTime: fmtDateTime, fmtRange: fmtRange,
    dateKey: dateKey, addDays: addDays, money: money,
    SETTLEMENT: SETTLEMENT, settlementLabel: settlementLabel,
    el: el, esc: esc, clear: clear, toast: toast, errMsg: errMsg, groupByDay: groupByDay,
    lifecycleBar: lifecycleBar, subtabs: subtabs, lifeActions: lifeActions, statusChip: statusChip,
    card: card, backBar: backBar, kv: kv, pageHeader: pageHeader, modal: modal, toLocal: toLocal, addToCalendar: addToCalendar,
    menu: menu, logRow: logRow, openAuthedFile: openAuthedFile,
  };
})();
