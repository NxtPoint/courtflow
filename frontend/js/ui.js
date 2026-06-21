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

  window.UI = {
    CLUB_TZ: CLUB_TZ,
    fmtTime: fmtTime, fmtDate: fmtDate, fmtDateTime: fmtDateTime, fmtRange: fmtRange,
    dateKey: dateKey, addDays: addDays, money: money,
    SETTLEMENT: SETTLEMENT, settlementLabel: settlementLabel,
    el: el, esc: esc, clear: clear, toast: toast, errMsg: errMsg, groupByDay: groupByDay,
  };
})();
