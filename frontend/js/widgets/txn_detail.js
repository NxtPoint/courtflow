// widgets/txn_detail.js — Widgets.TransactionDetail: the ONE booking / transaction detail
// ("event story"), shared by the client, coach and admin apps (FRONTEND-STANDARDISATION Wave 2).
//
// Role differences flow ONLY through cfg — never a fork in this render code:
//   cfg.data.get(id) -> Promise<booking>   (each app wires its own /api/me|coach|admin endpoint;
//                                            the payloads already share the same shape + `can{}`)
//   cfg.actions       -> capability map: { <canKey>: { run(b), label?, tone?, group?, confirm?,
//                                          done?, back?, manual? } }  — a button per `can` flag that
//                                          BOTH the payload allows AND the app wired a handler for.
//   cfg.fields        -> visibility: { showCoach?, showNotes? }
//   cfg.onNavigate    -> fn({kind:"person", id}) — drill to a record; the app owns the route.
//
// The widget is pure render + events: no endpoints, no location.hash, no globals mutated. It reads
// via cfg.data and emits via cfg.actions[*].run / cfg.onNavigate. Handles the small payload variants
// (client story has a flat `coach_name`; coach/admin carry a `client{}`; admin adds `coach{}`+notes).
(function () {
  // Canonical action order + default label/group/tone. An app overrides any of these per key.
  var ACTIONS = [
    ["accept", "Approval", "Accept", "primary"],
    ["propose", "Approval", "Propose time", ""],
    ["pay", "Approval", "Pay now", "primary"],
    ["decline", "Approval", "Decline", "danger"],
    ["mark_completed", "Session", "Mark completed", "primary"],
    ["mark_no_show", "Session", "No-show", ""],
    ["reschedule", "Session", "Reschedule", ""],
    ["reassign_coach", "Session", "Reassign coach", ""],
    ["add_to_calendar", "Session", "Add to calendar", "ghost"],
    ["cancel", "Session", "Cancel", "danger"],
    ["withdraw", "Session", "Withdraw", "danger"],
    ["desk_pay", "Client charge", "Mark as paid", "primary"],
    ["receipt", "Client charge", "Receipt", "ghost"],
    ["refund", "Client charge", "Refund", ""],
    ["request_refund", "Client charge", "Request refund", ""],
    ["void", "Client charge", "Void", ""],
    ["write_off", "Client charge", "Write off", "danger"],
    ["collect", "Coaching charge", "Mark collected", "primary"],
    ["discount", "Coaching charge", "Discount", ""],
    ["write_off_coaching", "Coaching charge", "Write off coaching", "danger"],
  ];
  var GROUP_ORDER = ["Approval", "Session", "Client charge", "Coaching charge"];

  function mount(host, cfg) {
    var UI = window.UI, el = UI.el, fields = cfg.fields || {};
    function money(m, c) { return UI.money(m || 0, c || "ZAR"); }
    function tRange(b) { try { return UI.fmtTime(b.starts_at) + "–" + UI.fmtTime(b.ends_at); } catch (e) { return ""; } }
    function typeLabel(t) { return ({ court: "Court", lesson: "Lesson", class: "Class" })[t] || "Session"; }

    function loading() { UI.clear(host); host.appendChild(el("div", { class: "cf-loading", style: "min-height:200px", text: "Loading…" })); }
    function fail(e) { UI.clear(host); host.appendChild(el("div", {}, [UI.backBar("Back"), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); }

    function run(a, b) {
      if (a.confirm && !window.confirm(a.confirm)) return;
      if (a.manual) { try { a.run(b); } catch (e) { UI.toast(UI.errMsg(e), "error"); } return; }
      try {
        Promise.resolve(a.run(b)).then(
          function () { UI.toast(a.done || "Done.", "info"); if (a.back) history.back(); else load(); },
          function (e) { UI.toast(UI.errMsg(e), "error"); });
      } catch (e) { UI.toast(UI.errMsg(e), "error"); }
    }

    function render(b) {
      var cur = (b.charge && b.charge.currency) || "ZAR";
      var coachName = b.coach ? b.coach.name : b.coach_name;
      var coachId = b.coach ? b.coach.user_id : null;
      var wrap = el("div", {});
      wrap.appendChild(UI.backBar("Back"));

      var head = UI.card([
        el("div", { class: "cf-detail-h" }, [
          el("div", {}, [
            el("span", { class: "cf-chip " + b.booking_type, text: typeLabel(b.booking_type) + (b.duration_minutes ? " · " + b.duration_minutes + " min" : "") }),
            el("h1", { style: "margin:8px 0 2px;font-size:1.3rem", text: (function () { try { return UI.fmtDate(b.starts_at); } catch (e) { return b.starts_at || ""; } })() }),
            el("div", { class: "cf-muted", text: tRange(b) }),
          ]),
          UI.statusChip(b.status),
        ]),
      ]);
      // event subtitle (plain English: coach for a lesson, class name for a class)
      var sub = b.booking_type === "lesson" ? (coachName ? "with " + coachName : "")
              : (b.booking_type === "class" ? (b.class_name || "") : "");
      if (sub) head.appendChild(el("div", { class: "cf-muted", style: "margin-top:2px", text: sub }));

      // money line — the reconciliation headline (paid / owed / covered / refunded) + Settle CTA when owed
      var ch = b.charge || {}, state = ch.state || ch.status;
      var owedAmt = (ch.owed_minor != null ? ch.owed_minor : ch.amount_minor);
      var moneyLine =
        state === "covered" ? "Covered by your membership" :
        state === "owed" ? "You owe " + money(owedAmt, cur) :
        state === "pending" ? "Payment pending · " + money(ch.amount_minor, cur) :
        state === "part_refunded" ? ("Paid " + money(ch.paid_minor, cur) + " · " + money(ch.refunded_minor, cur) + " refunded") :
        state === "refunded" ? "Refunded " + money(ch.refunded_minor || ch.amount_minor, cur) :
        state === "written_off" ? "Written off — nothing to pay" :
        state === "paid" ? "Paid " + money(ch.paid_minor || ch.amount_minor, cur) : null;
      if (moneyLine) {
        var settleAct = cfg.actions && (cfg.actions.settle || cfg.actions.pay);
        head.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;gap:10px;margin-top:12px;padding-top:12px;border-top:1px solid var(--border)" }, [
          el("span", { style: "font-weight:700;font-size:1.02rem", text: moneyLine }),
          (state === "owed" && settleAct) ? el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Settle " + money(owedAmt, cur) + " ›", onclick: function () { run(settleAct, b); } }) : null,
        ].filter(Boolean)));
      }

      // last action — the current status in words, from the newest log entry
      var lastE = (b.log && b.log.length) ? b.log[b.log.length - 1] : null;
      if (lastE) head.appendChild(el("div", { class: "cf-muted", style: "font-size:.82rem;margin-top:8px", text: "Latest: " + (lastE.title || lastE.label || "") + (lastE.at ? " · " + (function () { try { return UI.fmtDate(lastE.at); } catch (e) { return ""; } })() : "") }));

      var det = el("div", { style: "margin-top:6px" });
      // Client (present on coach/admin stories; the client's own story omits it — they ARE the client)
      if (b.client && b.client.name) {
        var cc = el("div", { class: "cf-row", style: "gap:8px;margin-top:4px;flex-wrap:wrap" });
        if (b.client.phone) cc.appendChild(el("a", { class: "cf-btn cf-btn-sm cf-btn-ghost", href: "tel:" + b.client.phone, text: "📞 Call" }));
        if (b.client.email) cc.appendChild(el("a", { class: "cf-btn cf-btn-sm cf-btn-ghost", href: "mailto:" + b.client.email, text: "✉ Email" }));
        if (b.client.user_id && cfg.onNavigate) cc.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Full record ›", onclick: function () { cfg.onNavigate({ kind: "person", id: b.client.user_id }); } }));
        det.appendChild(UI.kv("Client", el("div", {}, [el("div", { style: "font-weight:600", text: b.client.name }), el("div", { class: "cf-muted", style: "font-size:.85rem", text: [b.client.email, b.client.phone].filter(Boolean).join(" · ") }), cc])));
      }
      // Coach (admin shows a drill link; hidden where fields.showCoach === false)
      if (coachName && fields.showCoach !== false) {
        var cnode = el("div", { style: "font-weight:600", text: coachName });
        if (coachId && cfg.onNavigate) cnode = el("div", {}, [el("div", { style: "font-weight:600", text: coachName }), el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", style: "margin-top:4px", text: "Coach record ›", onclick: function () { cfg.onNavigate({ kind: "person", id: coachId }); } })]);
        det.appendChild(UI.kv("Coach", cnode));
      }
      if (b.venue && (b.venue.club_name || b.court_name)) det.appendChild(UI.kv("Where", el("div", {}, [el("div", { text: [b.venue.club_name, b.court_name].filter(Boolean).join(" · ") || "—" }), b.venue.address ? el("div", { class: "cf-muted", style: "font-size:.85rem", text: b.venue.address }) : null].filter(Boolean))));
      if (b.players && b.players.length) det.appendChild(UI.kv("Players", b.players.map(function (p) { return p.name + (p.attended === true ? " ✓" : p.attended === false ? " ✗" : ""); }).join(", ")));
      // (the headline money line lives in the summary above; here we keep only the coaching split detail)
      if (b.arrears) det.appendChild(UI.kv("Coaching", el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [el("span", { style: "font-weight:700", text: money(b.arrears.gross_minor, cur) }), UI.statusChip(b.arrears.status)])));
      if (b.notes && fields.showNotes !== false) det.appendChild(UI.kv("Notes", b.notes));
      head.appendChild(det);
      wrap.appendChild(head);

      // Actions — a button per `can` flag the payload allows AND the app wired, in canonical order.
      // cfg.grouped (default true) shows category headers (admin/coach god-view); pass false for a
      // clean flat row (the client's fewer, simpler actions).
      var built = [];
      ACTIONS.forEach(function (spec) {
        var key = spec[0];
        if (!b.can || !b.can[key]) return;
        var a = cfg.actions && cfg.actions[key];
        if (!a) return;
        var tone = a.tone || spec[3], group = a.group || spec[1], label = a.label || spec[2];
        var cls = "cf-btn cf-btn-sm" + (tone === "ghost" ? " cf-btn-ghost" : (tone ? " cf-btn-" + tone : ""));
        built.push({ group: group, node: el("button", { class: cls, text: label, onclick: function () { run(a, b); } }) });
      });
      if (cfg.grouped === false) {
        if (built.length) wrap.appendChild(el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap;margin-top:14px" }, built.map(function (x) { return x.node; })));
      } else {
        var groups = {};
        built.forEach(function (x) { (groups[x.group] = groups[x.group] || []).push(x.node); });
        GROUP_ORDER.concat(Object.keys(groups).filter(function (g) { return GROUP_ORDER.indexOf(g) < 0; })).forEach(function (g) {
          var btns = groups[g]; if (!btns || !btns.length) return;
          wrap.appendChild(el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px" },
            [el("span", { class: "cf-muted", style: "font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;width:100%", text: g })].concat(btns)));
        });
      }

      // History — the chronological "what happened" log (summary → drill; collapse when long).
      var log = b.log || [];
      if (log.length) {
        var expanded = false, histBox = el("div", {});
        var toggleBtn = el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost" });
        var paintHist = function () {
          UI.clear(histBox);
          ((expanded || log.length <= 4) ? log : log.slice(-3)).forEach(function (e) { histBox.appendChild(UI.logRow(e, cur)); });
          toggleBtn.textContent = expanded ? "Show less ⌃" : "Full history ⌄";
          toggleBtn.style.display = (log.length <= 4) ? "none" : "";
        };
        toggleBtn.addEventListener("click", function () { expanded = !expanded; paintHist(); });
        var histCard = UI.card([el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [el("h2", { style: "margin:0;font-size:1.05rem", text: "History" }), toggleBtn]), histBox]);
        paintHist();
        wrap.appendChild(histCard);
      }

      UI.clear(host); host.appendChild(wrap);
    }

    function load() { loading(); Promise.resolve(cfg.data.get(cfg.scope.id)).then(render, fail); }
    load();
    return { refresh: load, destroy: function () { UI.clear(host); } };
  }

  window.Widgets = window.Widgets || {};
  window.Widgets.TransactionDetail = { mount: mount };
})();
