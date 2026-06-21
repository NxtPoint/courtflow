// statement.js — the coach month-end statement (Phase D, owner lane — the coach's
// most-wanted surface, docs/specs/01). Per client: lessons, paid via Yoco, owed (arrears),
// net balance; the coach marks an arrears invoice collected (off-platform EFT) which accrues
// its commission. Uses TFAuth.apiJSON directly against /api/admin/coach-statement* — it does
// NOT touch coach.js / coach_api.js (the Coach agent owns those). Reuses cf-* classes only.
(function () {
  var UI, el;
  var state = { month: null, data: null };

  function root() { return document.getElementById("cf-statement"); }
  function api(path, opts) { return window.TFAuth.apiJSON(path, opts); }

  function thisMonth() {
    var d = new Date();
    return d.getFullYear() + "-" + (d.getMonth() < 9 ? "0" : "") + (d.getMonth() + 1);
  }
  function shiftMonth(ym, delta) {
    var parts = ym.split("-"); var y = parseInt(parts[0], 10), m = parseInt(parts[1], 10) - 1 + delta;
    while (m < 0) { m += 12; y -= 1; } while (m > 11) { m -= 12; y += 1; }
    return y + "-" + (m < 9 ? "0" : "") + (m + 1);
  }

  function money(minor, cur) { return UI.money(minor || 0, cur || "ZAR"); }

  function header() {
    var bar = el("div", { class: "cf-row", style: "gap:8px;align-items:center;margin-bottom:12px" });
    var prev = el("button", { class: "cf-btn cf-btn-sm", text: "‹ Prev" });
    var next = el("button", { class: "cf-btn cf-btn-sm", text: "Next ›" });
    prev.addEventListener("click", function () { state.month = shiftMonth(state.month, -1); load(); });
    next.addEventListener("click", function () { state.month = shiftMonth(state.month, 1); load(); });
    bar.appendChild(prev);
    bar.appendChild(el("strong", { text: state.month }));
    bar.appendChild(next);
    return bar;
  }

  function render() {
    var host = root(); UI.clear(host);
    host.appendChild(el("div", { class: "cf-card" }, [
      el("h2", { text: "My statement" }),
      el("p", { class: "cf-muted", text:
        "Per client this month: lessons taken, paid online, and amounts still owed. " +
        "For unpaid (arrears) lessons, send the client a statement, collect by EFT, then mark it collected." }),
    ]));
    host.appendChild(header());

    var d = state.data;
    if (!d) { host.appendChild(el("div", { class: "cf-loading", text: "Loading…" })); return; }
    var cur = d.currency || "ZAR";

    // Totals strip
    var tot = d.totals || {};
    host.appendChild(el("div", { class: "cf-row", style: "gap:10px;flex-wrap:wrap;margin-bottom:14px" }, [
      statCard("Paid online", money(tot.paid_minor, cur)),
      statCard("Owed (arrears)", money(tot.owed_minor, cur)),
      statCard("Net (clients)", money(tot.net_minor, cur)),
      statCard("Rent (month)", money(tot.rent_minor, cur)),
      statCard("Account balance", money(tot.balance_minor, cur), tot.balance_minor < 0 ? "you owe the club" : "owed to you"),
    ]));

    // Per-client table
    var card = el("div", { class: "cf-card" });
    card.appendChild(el("h3", { text: "By client" }));
    var clients = d.clients || [];
    if (!clients.length) {
      card.appendChild(el("div", { class: "cf-empty", text: "No lessons this month yet." }));
    } else {
      var t = el("table", { class: "cf-table" });
      t.appendChild(el("thead", {}, [el("tr", {}, ["Client", "Lessons", "Paid online", "Owed", "Net"].map(function (h) {
        return el("th", { text: h }); }))]));
      var tb = el("tbody");
      clients.forEach(function (c) {
        tb.appendChild(el("tr", {}, [
          el("td", { text: c.client_name || "Client" }),
          el("td", { class: "num", text: String(c.lessons) }),
          el("td", { class: "num", text: money(c.paid_minor, cur) }),
          el("td", { class: "num", text: money(c.owed_minor, cur) }),
          el("td", { class: "num", text: money(c.net_minor, cur) }),
        ]));
      });
      t.appendChild(tb); card.appendChild(t);
    }
    host.appendChild(card);

    // Arrears items + mark collected
    var ar = el("div", { class: "cf-card" });
    ar.appendChild(el("h3", { text: "Outstanding (arrears) lessons" }));
    ar.appendChild(el("p", { class: "cf-muted", style: "margin:-4px 0 10px", text:
      "Off-platform: chase the EFT yourself, then mark collected. Marking collected accrues the club's commission." }));
    var items = d.arrears_items || [];
    if (!items.length) {
      ar.appendChild(el("div", { class: "cf-empty", text: "Nothing outstanding — you're all settled." }));
    } else {
      var at = el("table", { class: "cf-table" });
      at.appendChild(el("thead", {}, [el("tr", {}, ["When", "Client", "Owed", ""].map(function (h) {
        return el("th", { text: h }); }))]));
      var atb = el("tbody");
      items.forEach(function (it) {
        var btn = el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Mark collected" });
        btn.addEventListener("click", function () { collect(it, btn); });
        atb.appendChild(el("tr", {}, [
          el("td", { text: (it.starts_at || "").replace("T", " ").slice(0, 16) || "—" }),
          el("td", { text: it.client_name || "Client" }),
          el("td", { class: "num", text: money(it.gross_minor, it.currency || cur) }),
          el("td", {}, [btn]),
        ]));
      });
      at.appendChild(atb); ar.appendChild(at);
    }
    host.appendChild(ar);
  }

  function statCard(label, value, sub) {
    return el("div", { class: "cf-card", style: "flex:1;min-width:150px" }, [
      el("div", { class: "cf-muted", style: "font-size:13px", text: label }),
      el("div", { style: "font-size:22px;font-weight:700;margin:4px 0 2px", text: value }),
      sub ? el("div", { class: "cf-muted", style: "font-size:12px", text: sub }) : el("span"),
    ]);
  }

  function collect(it, btn) {
    if (!window.confirm("Mark " + money(it.gross_minor, it.currency) + " from " +
        (it.client_name || "this client") + " as collected? This accrues the club's commission.")) return;
    btn.disabled = true; btn.textContent = "Saving…";
    api("/api/admin/coach-statement/arrears/" + encodeURIComponent(it.id) + "/collected", { method: "POST", body: {} })
      .then(function () { UI.toast("Marked collected.", "info"); load(); })
      .catch(function (e) { UI.toast(UI.errMsg(e), "error"); btn.disabled = false; btn.textContent = "Mark collected"; });
  }

  function load() {
    state.data = null; render();
    api("/api/admin/coach-statement?month=" + encodeURIComponent(state.month))
      .then(function (d) { state.data = d; render(); })
      .catch(function (e) {
        var host = root(); UI.clear(host);
        host.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) }));
      });
  }

  window.CoachStatement = {
    start: function (principal) {
      UI = window.UI; el = UI.el;
      state.month = thisMonth();
      load();
    },
  };
})();
