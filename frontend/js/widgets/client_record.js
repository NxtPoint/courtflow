// widgets/client_record.js — Widgets.ClientRecord: the ONE client / person "360" record,
// shared by the admin, coach and client apps (the Client-360 consolidation, golden rule).
//
// Every client view is a VIEW off ONE data layer (client360.get_client_360) rendered by ONE widget.
// Role differences flow ONLY through cfg — never a fork in this render code:
//   cfg.data.get(id) -> Promise<person>   (each app wires its endpoint: AdminAPI.person /
//                                           CoachAPI.client360 / API.my360 — all return the SAME
//                                           composer payload with a `can{}` map)
//   cfg.scope         -> { id, role }      (id passed to data.get; role only picks defaults)
//   cfg.actions       -> capability map keyed by a `can` flag:
//                        { <canKey>: { run(ctx), label?, tone?, confirm?, done?, manual? } }
//                        a control renders only when BOTH payload.can[key] AND the app wired a handler.
//                        ctx is the section object (an owed order / a wallet / a payment / the person).
//   cfg.fields        -> visibility: { showActivity?, showDependents?, showPayments?, showCoaching?,
//                                       showBookings?, showPackages? }  (default: show when present)
//   cfg.onNavigate    -> fn({kind:'event'|'class'|'person', id}) — drill; the app owns the route.
//   cfg.back          -> { label, hash } for the back bar (optional).
//
// Pure render + events: no endpoints, no location.hash, no globals mutated. Reads via cfg.data,
// mutates via cfg.actions[*].run, drills via cfg.onNavigate. Depends on window.UI + window.CRMUI.
(function () {
  function mount(host, cfg) {
    var UI = window.UI, CRMUI = window.CRMUI, el = UI.el;
    var fields = cfg.fields || {};
    function money(m, c) { return UI.money(m || 0, c || "ZAR"); }
    function has(key) { return !!(cur().can && cur().can[key] && cfg.actions && cfg.actions[key]); }
    var _pn = null;
    // The person-360 shows ONE month at a time (the money + events block pages through months).
    var _month = cfg.month || (function () { var d = new Date(); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0"); })();
    function monthLabel(ym) { try { var p = ym.split("-"); return new Date(p[0], parseInt(p[1], 10) - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" }); } catch (e) { return ym; } }
    function shiftMonth(ym, d) { var p = ym.split("-"); var dt = new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1 + d, 1); return dt.getFullYear() + "-" + String(dt.getMonth() + 1).padStart(2, "0"); }
    function cur() { return _pn || {}; }
    function fDate(v) { try { return UI.fmtDate(v); } catch (e) { return v || ""; } }
    function fDT(v) { try { return UI.fmtDate(v) + " " + UI.fmtTime(v); } catch (e) { return v || ""; } }
    // Compact date for list rows: "4 Jul · 09:00" (no weekday / year) — keeps a long list tight.
    function fShort(v) { try { var d = new Date(v); return d.getDate() + " " + d.toLocaleDateString(undefined, { month: "short" }) + " · " + UI.fmtTime(v); } catch (e) { return fDT(v); } }

    function loading() { UI.clear(host); host.appendChild(el("div", { class: "cf-loading", style: "min-height:200px", text: "Loading…" })); }
    function fail(e) { UI.clear(host); var w = el("div", {}); if (cfg.back) w.appendChild(UI.backBar(cfg.back.label || "Back", cfg.back.hash)); w.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); host.appendChild(w); }

    // Run an action: confirm → run → toast → refresh. A cancelled modal resolves without error
    // (or rejects with null) so no error toast fires. Mirrors Widgets.TransactionDetail.
    function runAct(a, ctx) {
      if (!a) return;
      if (a.confirm) { var msg = typeof a.confirm === "function" ? a.confirm(ctx) : a.confirm; if (!window.confirm(msg)) return; }
      if (a.manual) { try { a.run(ctx); } catch (e) { UI.toast(UI.errMsg(e), "error"); } return; }
      try {
        Promise.resolve(a.run(ctx)).then(
          function () { UI.toast(a.done || "Done.", "info"); load(); },
          function (e) { if (e) UI.toast(UI.errMsg(e), "error"); });
      } catch (e) { UI.toast(UI.errMsg(e), "error"); }
    }
    // A gated button for a capability key; null when the payload/app don't allow it.
    function actBtn(key, ctx, opts) {
      opts = opts || {};
      if (!has(key)) return null;
      var a = cfg.actions[key];
      var tone = a.tone || opts.tone || "";
      var cls = "cf-btn cf-btn-sm" + (tone === "ghost" ? " cf-btn-ghost" : (tone ? " cf-btn-" + tone : ""));
      return el("button", { class: cls, type: "button", text: a.label || opts.label || key, onclick: function () { runAct(a, ctx); } });
    }
    function pInit(pn) { var n = (pn.name || pn.email || "?").trim(); return (n[0] || "?").toUpperCase(); }

    // A collapsed expander for lower-priority reference sections — a ghost toggle that reveals the
    // given cards. Keeps the person-360 focused on money + bookings by default.
    function collapsible(title, kids) {
      var open = false;
      var body = el("div", { style: "display:none;margin-top:6px" });
      kids.forEach(function (k) { body.appendChild(k); });
      var toggle = el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", style: "margin-top:14px",
        text: "▸ " + title });
      toggle.addEventListener("click", function () {
        open = !open; body.style.display = open ? "" : "none"; toggle.textContent = (open ? "▾ " : "▸ ") + title;
      });
      return el("div", {}, [toggle, body]);
    }

    function render(pn) {
      _pn = pn;
      var c = pn.currency || "ZAR";
      var _role = (cfg.scope && cfg.scope.role) || "";
      var wrap = el("div", {});
      if (cfg.back) wrap.appendChild(UI.backBar(cfg.back.label || "Back", cfg.back.hash));

      // ---- 1) WHO — identity + status + contact (email·cell) + kids + Edit, all up top (2026-07 redesign).
      var chips = el("div", { class: "cf-row", style: "gap:6px;flex-wrap:wrap;margin-top:6px" });
      chips.appendChild(el("span", { class: "cf-chip " + (pn.member_status === "active" ? "confirmed" : "held"), text: pn.member_status || "—" }));
      (pn.roles || []).filter(function (r) { return r !== "member"; }).forEach(function (r) { chips.appendChild(el("span", { class: "cf-chip", text: r })); });
      if (pn.notifications_unread) chips.appendChild(el("span", { class: "cf-chip held", text: pn.notifications_unread + " unread" }));
      var contactBits = [pn.email, pn.phone].filter(Boolean);
      var head = UI.card([
        el("div", { class: "cf-row", style: "gap:10px;align-items:flex-start;justify-content:space-between" }, [
          el("div", { class: "cf-row", style: "gap:10px;align-items:center" }, [
            el("div", { class: "cf-avatar", text: pInit(pn) }),
            el("div", {}, [
              el("h1", { style: "margin:0;font-size:1.25rem", text: pn.name }),
              el("div", { class: "cf-muted", style: "font-size:.85rem", text: contactBits.join(" · ") || "—" }),
              chips,
            ]),
          ]),
          // Edit — admin/client wire cfg.actions.edit (or cfg.onEditProfile). Opens the contact/details editor.
          (has("edit") || cfg.onEditProfile)
            ? el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Edit", onclick: function () { if (cfg.onEditProfile) cfg.onEditProfile(); else editModal(pn); } })
            : null,
        ].filter(Boolean)),
      ]);
      // Kids (dependents) inline — who this person can book for.
      if (_role !== "coach" && (pn.dependents || []).length) {
        head.appendChild(el("div", { class: "cf-muted", style: "font-size:.82rem;margin-top:8px", text: "Kids: " + pn.dependents.map(function (d) { return d.name || d.first_name || "Child"; }).join(" · ") }));
      }
      // Membership line (the club relationship) — omitted for a coach's scoped view.
      if (_role !== "coach") head.appendChild(membershipLine(pn, c));
      wrap.appendChild(head);

      // ---- 2) PACKAGES — prepaid holdings + change/buy (unchanged). ----
      if (fields.showPackages !== false && pn.packages) wrap.appendChild(packagesCard(pn, c));

      // ---- 3) ACTIVITY & MONEY — ONE month-scrollable block: the reconciling fold, then the month's
      // events GROUPED BY SERVICE TYPE (court/lessons/classes; each sums to the fold), each event
      // drilling to the transaction detail + log. (Replaces the old Upcoming/History/Coaching sections.)
      wrap.appendChild(moneyBlock(pn, c));

      UI.clear(host); host.appendChild(wrap);
    }

    // The month money+events block. Fold on top; the month's events grouped by service type; expand a
    // group → its events → drill to the ONE transaction detail (cfg.onNavigate).
    function moneyBlock(pn, c) {
      var card = UI.card([
        el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [
          el("h2", { style: "margin:0;font-size:1.05rem", text: "Activity & money" }),
          el("div", { class: "cf-row", style: "gap:6px;align-items:center" }, [
            el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "‹", onclick: function () { _month = shiftMonth(_month, -1); load(); } }),
            el("span", { style: "font-weight:600;font-size:.85rem;min-width:96px;text-align:center", text: monthLabel(_month) }),
            el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "›", onclick: function () { _month = shiftMonth(_month, 1); load(); } }),
          ]),
        ]),
      ], "cf-mt");
      if (pn.statement_fold && CRMUI.statementFold) card.appendChild(CRMUI.statementFold({ currency: c, month: pn.month || _month, totals: pn.statement_fold }));
      // Group the month's events by service kind. Owner-first order: Lessons · Court hire · Classes.
      var evs = pn.month_events || [];
      var box = el("div", { style: "margin-top:12px" });
      if (!evs.length) {
        box.appendChild(el("div", { class: "cf-empty", text: "No activity in " + monthLabel(_month) + "." }));
        card.appendChild(box);
        return card;
      }
      var byKind = {};
      evs.forEach(function (e) { var k = (e.kind === "court" ? "court" : e.kind) || "other"; (byKind[k] = byKind[k] || []).push(e); });
      var ORDER = [["lesson", "Lessons"], ["court", "Court hire"], ["class", "Classes"], ["other", "Other"]];
      var seen = {};
      ORDER.forEach(function (kd) { if (byKind[kd[0]]) { seen[kd[0]] = 1; box.appendChild(serviceGroup(kd[1], kd[0], byKind[kd[0]], c)); } });
      Object.keys(byKind).forEach(function (k) { if (!seen[k]) box.appendChild(serviceGroup(k, k, byKind[k], c)); });
      card.appendChild(box);
      return card;
    }

    // A collapsible service-type group: header shows "<label> · <count>  <money>"; expands to its events.
    function serviceGroup(label, kind, events, c) {
      var total = events.reduce(function (a, e) { return a + (e.amount_minor || 0); }, 0);
      var open = false;
      var body = el("div", { style: "display:none;padding:2px 0 6px" });
      events.forEach(function (e) { body.appendChild(eventRow(e, c)); });
      var chev = el("span", { class: "cf-muted", text: "▸" });
      var header = el("div", { class: "cf-item cf-item-tap", onclick: function () { open = !open; body.style.display = open ? "" : "none"; chev.textContent = open ? "▾" : "▸"; } }, [
        el("span", { class: "cf-chip " + (["court", "lesson", "class"].indexOf(kind) >= 0 ? kind : ""), text: kind }),
        el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: label + " · " + events.length })]),
        el("span", { style: "font-weight:700", text: money(total, c) }),
        chev,
      ]);
      return el("div", { style: "margin-bottom:6px" }, [header, body]);
    }

    // ONE event row inside a service group → the transaction detail (fold + transaction log).
    function eventRow(e, c) {
      var id = e.booking_id || e.enrolment_id;
      var orderOnly = !id && e.order_id;                       // an invoice/membership/pack (no booking)
      var tap = (!!id && cfg.onNavigate) || orderOnly;
      var row = el("div", { class: "cf-item" + (tap ? " cf-item-tap" : ""), style: "margin-left:8px" }, [
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: e.is_order_only ? (e.service || "Invoice") : fShort(e.starts_at) }),
          el("div", { class: "cf-item-s", text: e.is_order_only ? [fShort(e.starts_at), e.pay_status].filter(Boolean).join(" · ") : [e.service, e.coach_name, e.pay_status].filter(Boolean).join(" · ") }),
        ]),
        el("span", { style: "font-weight:700", text: money(e.amount_minor, c) }),
        (tap ? el("span", { class: "cf-muted", text: "›" }) : null),
      ].filter(Boolean));
      if (id && cfg.onNavigate) row.addEventListener("click", function () { cfg.onNavigate({ kind: e.booking_id ? "event" : "class", id: id }); });
      else if (orderOnly) row.addEventListener("click", function () { window.open("/receipt.html?order=" + encodeURIComponent(e.order_id), "_blank"); });
      return row;
    }

    // The contact/details editor (admin). Edits the whitelisted profile fields via cfg.actions.edit.
    function editModal(pn) {
      var m = UI.modal("Edit " + (pn.name || "client"), { lg: true });
      var f = {};
      function field(key, label, type) {
        var inp = el("input", { class: "cf-input", type: type || "text", value: (pn.profile && pn.profile[key]) || (key === "phone" ? (pn.phone || "") : "") });
        f[key] = inp;
        m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: label }), inp]));
      }
      field("first_name", "First name"); field("surname", "Surname"); field("phone", "Cell");
      field("dob", "Date of birth", "date");
      field("address_line1", "Address"); field("city", "City");
      field("emergency_contact_name", "Emergency contact"); field("emergency_contact_phone", "Emergency phone");
      m.body.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:12px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: m.close }),
        el("button", { class: "cf-btn cf-btn-primary", text: "Save", onclick: function () {
          var body = {}; Object.keys(f).forEach(function (k) { body[k] = f[k].value; });
          Promise.resolve(cfg.actions.edit.run(body)).then(function () { UI.toast("Saved.", "info"); m.close(); load(); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
        } }),
      ]));
    }

    function membershipLine(pn, c) {
      var box = el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-top:12px;padding-top:12px;border-top:1px solid var(--border)" });
      var m = pn.membership, ms = pn.membership_status;
      var trial = (m && m.is_trial) || (ms && ms.is_trial);
      var daysLeft = ms && ms.trial_days_left;
      var label = trial
        ? ("7 Day Trial Period" + (daysLeft != null ? " · " + daysLeft + " day" + (daysLeft === 1 ? "" : "s") + " left" : "") + " · courts only")
        : (m ? ("Member" + (m.plan_label ? " · " + m.plan_label : "") + (m.current_period_end ? " · until " + fDate(m.current_period_end) : ""))
             : "No active membership · pay-as-you-go");
      box.appendChild(el("div", {}, [
        el("div", { style: "font-weight:700", text: trial ? "On trial" : (m ? "Membership active" : "Pay-as-you-go") }),
        el("div", { class: "cf-muted", style: "font-size:.82rem", text: label }),
      ]));
      var actions = el("div", { class: "cf-row", style: "gap:6px" });
      if (m) { var rv = actBtn("revoke_membership", pn, { label: "Revoke", tone: "ghost" }); if (rv) actions.appendChild(rv); }
      var iss = actBtn("issue", pn, { label: "Issue package", tone: "primary" }); if (iss) actions.appendChild(iss);
      var grant = (!m && !iss) ? actBtn("grant_membership", pn, { label: "Grant membership", tone: "primary" }) : null; if (grant) actions.appendChild(grant);
      if (actions.childNodes.length) box.appendChild(actions);
      return box;
    }

    function packagesCard(pn, c) {
      var card = UI.card([CRMUI.sectionHead("Packages")], "cf-mt");
      var active = (pn.packages.active || []), history = (pn.packages.history || []);
      if (!active.length && !history.length) { card.appendChild(el("div", { class: "cf-empty", text: "No prepaid packages." })); return card; }
      if (active.length) card.appendChild(packList(active, c, false));
      if (history.length) {
        card.appendChild(el("div", { class: "cf-muted", style: "margin:14px 0 4px;font-size:.8rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em", text: "Past packages" }));
        card.appendChild(packList(history, c, true));
      }
      return card;
    }
    function packList(wallets, c, past) {
      var l = el("div", { class: "cf-list" });
      wallets.forEach(function (w) {
        var sess = (w.sessions_remaining != null ? w.sessions_remaining : Math.round((w.minutes_remaining || 0) / (w.base_minutes || 60)));
        var subBits = [(w.service_kind || "pack"), w.coach_name ? ("with " + w.coach_name) : null,
          w.expires_at ? ("expires " + fDate(w.expires_at)) : null].filter(Boolean);
        var kids = [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: (w.label || "Package") + " · " + sess + " left" }),
            el("div", { class: "cf-item-s", text: subBits.join(" · ") }),
          ]),
          el("span", { class: "cf-chip " + (w.status === "active" ? "confirmed" : "held"), text: w.status }),
        ];
        if (!past) {
          var acts = el("div", { class: "cf-row", style: "gap:6px" });
          var adj = actBtn("wallet_adjust", w, { label: "Adjust" }); if (adj) acts.appendChild(adj);
          var exp = actBtn("wallet_expire", w, { label: "Remove", tone: "danger" }); if (exp) acts.appendChild(exp);
          if (acts.childNodes.length) kids.push(acts);
        }
        l.appendChild(el("div", { class: "cf-item" + (past ? " cf-item-off" : "") }, kids));
      });
      return l;
    }

    function moneyCard(pn, c) {
      var card = UI.card([CRMUI.sectionHead("Money")], "cf-mt");
      // The reconciling fold headline — the SAME standard as the coach + admin Money (golden rule):
      // Billed − Discount − Written-off = Invoiced ; Invoiced = Paid + Outstanding. Falls back to the
      // bare "Owed to the club" figure if the fold isn't in the payload (older API).
      var fold = pn.statement_fold;
      if (fold && CRMUI.statementFold && (fold.billed_minor || fold.invoiced_minor || pn.owed_minor)) {
        card.appendChild(CRMUI.statementFold({ currency: c, month: pn.month, totals: fold }));
        // Consolidated context line (was the separate ACTIVITY + BILLING blocks): sessions + minutes +
        // where the money went, by service — one muted line under the fold instead of two more cards.
        var a = pn.activity_summary;
        if (a && (a.counts || a.by_service)) {
          var bits = [];
          var tot = a.counts && a.counts.total;
          if (tot) bits.push(tot + " session" + (tot === 1 ? "" : "s"));
          if (a.minutes) { var h = Math.floor(a.minutes / 60), m = a.minutes % 60; bits.push((h ? h + "h " : "") + (m ? m + "m" : (h ? "" : "0m")).trim()); }
          var svc = (a.by_service || []).filter(function (x) { return x.billed_minor > 0; })
            .map(function (x) { return x.label + " " + money(x.billed_minor, c); });
          var line = bits.filter(Boolean).join(" · ");
          if (svc.length) line += (line ? "  ·  " : "") + svc.join(" · ");
          if (line) card.appendChild(el("div", { class: "cf-muted", style: "margin-top:9px;font-size:.82rem", text: line }));
        }
        card.appendChild(el("div", { style: "height:8px" }));
      } else {
        card.appendChild(el("div", { class: "cf-row", style: "margin:2px 0 10px" }, [
          el("div", {}, [
            el("div", { style: "font-size:1.25rem;font-weight:800;color:" + (pn.owed_minor > 0 ? "var(--danger,#c0392b)" : "inherit"), text: money(pn.owed_minor, c) }),
            el("div", { class: "cf-muted", style: "font-size:.8rem", text: "Owed to the club" }),
          ]),
        ]));
      }
      // Per-row actions on owed lines — role-gated (admin: void/write-off/discount; client: pay).
      var rowActs = [];
      if (has("discount")) rowActs.push({ label: cfg.actions.discount.label || "Discount", onClick: function (it) { runAct(cfg.actions.discount, it); } });
      if (has("void")) rowActs.push({ label: cfg.actions.void.label || "Void", onClick: function (it) { runAct(cfg.actions.void, it); } });
      if (has("write_off")) rowActs.push({ label: cfg.actions.write_off.label || "Write off", tone: "danger", onClick: function (it) { runAct(cfg.actions.write_off, it); } });
      if (has("pay")) rowActs.push({ label: cfg.actions.pay.label || "Pay", onClick: function (it) { runAct(cfg.actions.pay, it); } });
      if (has("request_refund")) rowActs.push({ label: "Request refund", onClick: function (it) { runAct(cfg.actions.request_refund, it); } });
      // The owed line-items are the CLIENT's full-club statement — only admin/client have it. A coach's
      // payload omits it (they see their coaching fold + their bookings-as-events instead).
      if (pn.statement) {
        var owed = (pn.statement.items) || [];
        card.appendChild(CRMUI.lineItems(owed.map(function (it) { return Object.assign({}, it, { gross_minor: it.amount_minor }); }), {
          currency: c,
          empty: "Nothing owed — all settled.",
          label: function (it) { return it.description || it.category || "Owed"; },
          sub: function (it) { return [it.category, it.coach_name, it.date ? fDate(it.date) : ""].filter(Boolean).join(" · "); },
          actions: rowActs,
        }));
      }
      // Online payments (with an admin Refund action per row).
      var pays = pn.payments || [];
      if (fields.showPayments !== false && pays.length) {
        card.appendChild(el("div", { class: "cf-muted", style: "margin:14px 0 4px;font-size:.8rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em", text: "Online payments" }));
        var pl = el("div", { class: "cf-list" });
        pays.forEach(function (pay) {
          var kids = [
            el("div", { class: "cf-item-main" }, [
              el("div", { class: "cf-item-t", text: money(pay.amount_minor, pay.currency_code || c) }),
              el("div", { class: "cf-item-s", text: (pay.provider || "card") + " · " + fDate(pay.created_at) }),
            ]),
            el("span", { class: "cf-chip " + (pay.refunded ? "held" : "confirmed"), text: pay.refunded ? "refunded" : "paid" }),
          ];
          var rf = (!pay.refunded) ? actBtn("refund", pay, { label: "Refund", tone: "ghost" }) : null;
          if (rf) kids.push(rf);
          pl.appendChild(el("div", { class: "cf-item" }, kids));
        });
        card.appendChild(pl);
      }
      return card;
    }

    function coachingCard(pn) {
      var co = pn.coaching, c = co.currency || pn.currency || "ZAR", t = co.totals || {};
      var card = UI.card([CRMUI.sectionHead("Coaching")], "cf-mt");
      card.appendChild(CRMUI.stats([
        { value: money(t.paid_minor, c), label: "Paid" },
        { value: money(t.owed_minor, c), label: "Owed" },
        { value: money(t.net_minor, c), label: "Net" },
      ]));
      // Owed coaching arrears lines, with collect/discount (coach scope).
      var arrears = (co.arrears_items || []).filter(function (a) { return a.status === "owed"; });
      if (arrears.length) {
        var rowActs = [];
        if (has("collect")) rowActs.push({ label: cfg.actions.collect.label || "Mark collected", onClick: function (it) { runAct(cfg.actions.collect, it); } });
        if (has("discount")) rowActs.push({ label: cfg.actions.discount.label || "Discount", onClick: function (it) { runAct(cfg.actions.discount, it); } });
        card.appendChild(CRMUI.lineItems(arrears, {
          currency: c,
          label: function (it) { return it.description || it.client_name || "Coaching"; },
          sub: function (it) { return [it.coach_name, it.starts_at ? fDate(it.starts_at) : ""].filter(Boolean).join(" · "); },
          actions: rowActs,
        }));
      }
      return card;
    }

    function serviceBreakdownCard(pn) {
      // Coaching grouped BY SERVICE for the month: each service expands to its sessions, each session
      // drills into the ONE shared TransactionDetail (golden rule) via cfg.onNavigate. Config, not fork.
      var sb = pn.service_breakdown || {}, svcs = sb.services || [];
      var c = (pn.coaching && pn.coaching.currency) || pn.currency || "ZAR";
      var card = UI.card([CRMUI.sectionHead("Coaching by service" + (pn.month ? " · " + pn.month : ""))], "cf-mt");
      var list = el("div", { class: "cf-list" });
      svcs.forEach(function (svc) {
        var sub = el("div", { style: "display:none" });
        (svc.items || []).forEach(function (it) {
          var tap = !!(it.booking_id || it.enrolment_id) && cfg.onNavigate;
          var row = el("div", { class: "cf-item" + (tap ? " cf-item-tap" : ""), style: "padding-left:16px" }, [
            el("div", { class: "cf-item-main" }, [
              el("div", { class: "cf-item-t", text: fDT(it.starts_at) }),
              el("div", { class: "cf-item-s", text: it.status || "" }),
            ]),
            el("span", { class: "cf-chip", text: money(it.amount_minor, c) }),
            tap ? el("span", { class: "cf-muted", text: "›" }) : null,
          ].filter(Boolean));
          if (tap) row.addEventListener("click", function () { cfg.onNavigate({ kind: it.booking_id ? "event" : "class", id: it.booking_id || it.enrolment_id }); });
          sub.appendChild(row);
        });
        var head = el("div", { class: "cf-item cf-item-tap" }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: svc.label || "Service" }),
            el("div", { class: "cf-item-s", text: (svc.count || 0) + " session" + (svc.count === 1 ? "" : "s") }),
          ]),
          el("span", { class: "cf-chip", text: money(svc.total_minor, c) }),
          el("span", { class: "cf-muted", text: "⌄" }),
        ]);
        head.addEventListener("click", function () { sub.style.display = (sub.style.display === "none") ? "" : "none"; });
        list.appendChild(head); list.appendChild(sub);
      });
      card.appendChild(list);
      return card;
    }

    function bookingsCard(title, rows, empty) {
      var card = UI.card([CRMUI.sectionHead(title)], "cf-mt");
      if (!rows.length) { card.appendChild(el("div", { class: "cf-empty", text: empty })); return card; }
      var l = el("div", { class: "cf-list" });
      rows.forEach(function (b) {
        var k = (b.kind || "court").toLowerCase();
        var tap = !!(b.booking_id || b.enrolment_id) && cfg.onNavigate;
        // ONE trailing chip = the money state (pay_status) — the booking-status chip was redundant on a
        // money record and cramped the row (3 chips) on mobile. Compact date keeps the list tight.
        var payTone = /paid|covered/i.test(b.pay_status || "") ? " confirmed"
          : /owed|await|pending/i.test(b.pay_status || "") ? " held"
            : /written|refund|cancel/i.test(b.pay_status || "") ? " cf-chip-muted" : "";
        var row = el("div", { class: "cf-item" + (tap ? " cf-item-tap" : "") }, [
          el("span", { class: "cf-chip " + (["court", "lesson", "class"].indexOf(k) >= 0 ? k : "court"), text: k }),
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: fShort(b.starts_at) }),
            el("div", { class: "cf-item-s", text: [b.service, b.resource_name, b.coach_name].filter(Boolean).join(" · ") || "" }),
          ]),
          b.pay_status ? el("span", { class: "cf-chip" + payTone, text: b.pay_status }) : null,
          tap ? el("span", { class: "cf-muted", text: "›" }) : null,
        ].filter(Boolean));
        if (tap) row.addEventListener("click", function () { cfg.onNavigate({ kind: b.booking_id ? "event" : "class", id: b.booking_id || b.enrolment_id }); });
        l.appendChild(row);
      });
      card.appendChild(l);
      return card;
    }

    function refundsCard(pn, c) {
      var card = UI.card([CRMUI.sectionHead("Refund requests")], "cf-mt");
      var l = el("div", { class: "cf-list" });
      (pn.refunds || []).forEach(function (r) {
        // A PENDING request can be DECIDED right here (same action as Money → Approvals; single-sourced),
        // so the owner never has to leave the client record. Gated by the scope's can{} + wired actions.
        var pending = (r.status === "pending" || r.status === "requested");
        var right = [UI.statusChip(r.status)];
        if (pending) {
          var ap = actBtn("approve_refund_request", r, { label: "Approve", tone: "primary" }); if (ap) right.push(ap);
          var dc = actBtn("decline_refund_request", r, { label: "Decline", tone: "ghost" }); if (dc) right.push(dc);
        }
        l.appendChild(el("div", { class: "cf-item", style: "flex-wrap:wrap;gap:6px" }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: money(r.amount_minor, r.currency_code || c) }),
            el("div", { class: "cf-item-s", text: [r.reason, r.created_at ? fDate(r.created_at) : ""].filter(Boolean).join(" · ") }),
          ]),
          el("div", { class: "cf-row", style: "gap:6px;align-items:center;flex-wrap:wrap" }, right),
        ]));
      });
      card.appendChild(l);
      return card;
    }

    function dependentsCard(pn) {
      var card = UI.card([CRMUI.sectionHead("Dependents")], "cf-mt");
      var l = el("div", { class: "cf-list" });
      (pn.dependents || []).forEach(function (d) {
        var nm = [d.first_name, d.surname].filter(Boolean).join(" ") || "Dependent";
        l.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: nm }),
            el("div", { class: "cf-item-s", text: [d.relationship, d.is_minor ? "minor" : null].filter(Boolean).join(" · ") }),
          ]),
          d.can_self_book ? el("span", { class: "cf-chip confirmed", text: "can self-book" }) : null,
        ].filter(Boolean)));
      });
      card.appendChild(l);
      return card;
    }

    // ---- Mission 1.2: demographics, consent, CRM event stream (now linked via the bridge) ----
    function kvRow(k, v) {
      return el("div", { class: "cf-row", style: "justify-content:space-between;gap:12px;padding:5px 0;border-bottom:1px solid var(--border)" }, [
        el("span", { class: "cf-muted", style: "font-size:.82rem", text: k }),
        el("span", { style: "font-size:.9rem;text-align:right", text: v }),
      ]);
    }
    function detailsCard(pn) {
      var pr = pn.profile || {};
      var card = UI.card([CRMUI.sectionHead("Details")], "cf-mt");
      var rows = [];
      function add(k, v) { if (v) rows.push([k, v]); }
      add("Email", pn.email);
      add("Cell", pn.phone || pr.phone);
      add("Date of birth", pr.dob ? fDate(pr.dob) : "");
      add("Address", [pr.address_line1, pr.address_line2, pr.city, pr.postal_code, pr.country].filter(Boolean).join(", "));
      add("Emergency contact", [pr.emergency_contact_name, pr.emergency_contact_phone].filter(Boolean).join(" · "));
      add("Marketing", pr.marketing_opt_in ? "Opted in ✓" : "Not opted in");
      if (!rows.length) { card.appendChild(el("div", { class: "cf-empty", text: "No contact details on file." })); return card; }
      rows.forEach(function (r) { card.appendChild(kvRow(r[0], r[1])); });
      return card;
    }
    var CONSENT_LABELS = { marketing_email: "Marketing email", privacy_policy: "Privacy policy", terms_of_service: "Terms of service", minor_processing_parental: "Parental (minor)" };
    function consentCard(pn) {
      var card = UI.card([CRMUI.sectionHead("Consent")], "cf-mt");
      var l = el("div", { class: "cf-list" });
      (pn.consent || []).forEach(function (co) {
        var granted = co.status === "granted";
        l.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: CONSENT_LABELS[co.consent_type] || co.consent_type }),
            el("div", { class: "cf-item-s", text: granted ? (co.granted_at ? "since " + fDate(co.granted_at) : "") : (co.withdrawn_at ? "withdrawn " + fDate(co.withdrawn_at) : "") }),
          ]),
          el("span", { class: "cf-chip " + (granted ? "confirmed" : "held"), text: granted ? "granted" : (co.status || "—") }),
        ]));
      });
      card.appendChild(l);
      return card;
    }
    function prettyEvent(t) { return (t || "event").replace(/_/g, " ").replace(/^\w/, function (m) { return m.toUpperCase(); }); }
    function eventsCard(pn) {
      var card = UI.card([CRMUI.sectionHead("Recent activity")], "cf-mt");
      var l = el("div", { class: "cf-list" });
      (pn.events || []).forEach(function (e) {
        l.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: prettyEvent(e.event_type) }),
            el("div", { class: "cf-item-s", text: fDT(e.at) }),
          ]),
        ]));
      });
      card.appendChild(l);
      return card;
    }

    function load() {
      loading();
      var id = cfg.scope && cfg.scope.id;
      Promise.resolve(cfg.data.get(id, _month)).then(render, fail);   // data.get(id, month) — month-scoped 360
    }
    load();
    return { refresh: load, destroy: function () { UI.clear(host); _pn = null; } };
  }

  window.Widgets = window.Widgets || {};
  window.Widgets.ClientRecord = { mount: mount };
})();
