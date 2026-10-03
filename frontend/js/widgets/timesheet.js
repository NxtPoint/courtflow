// widgets/timesheet.js — Widgets.Timesheet: THE COURT AUDIT (booked versus seen on court).
//
// ONE widget, two apps: the admin console (Money → Court audit) and the coach app, where it shows
// only for someone an admin has put on the auditor list. Role differences are what the SERVER
// allows, never forked render code — the reviewer captures, only an admin grants access or charges
// the penalty, and the buttons for those simply are not drawn for anyone else.
//
//   Capture   pick a coach + a day -> each BOOKED session gets "Seen" / "Not seen";
//             a court in use with no booking is recorded underneath.
//   Recon     the month, coach by coach: booked · seen · not seen · not reviewed · unbooked,
//             and every unbooked entry with (admin only) the penalty button.
//
// cfg: { back: {label, hash}?, searchMembers?: fn(q) -> Promise<{results}> }   (admin passes search)
// Talks to /api/admin/timesheet/* through window.TFAuth. Depends on window.UI + window.CRMUI.
(function () {
  function mount(host, cfg) {
    cfg = cfg || {};
    var UI = window.UI, el = UI.el;
    var J = function (path, opts) { return window.TFAuth.apiJSON("/api/admin/timesheet" + path, opts); };
    function money(m) { return UI.money(m || 0, "ZAR"); }
    function hours(min) { return (Math.round((min || 0) / 6) / 10) + " h"; }
    function isoDay(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
    function monthOf(day) { return String(day).slice(0, 7); }
    function monthLabel(ym) { try { var p = ym.split("-"); return new Date(p[0], parseInt(p[1], 10) - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" }); } catch (e) { return ym; } }
    function shiftMonth(ym, n) { var p = ym.split("-"); var d = new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1 + n, 1); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0"); }

    var yesterday = new Date(); yesterday.setDate(yesterday.getDate() - 1);
    var st = { tab: "capture", coach: "", date: isoDay(yesterday), month: monthOf(isoDay(yesterday)), access: null };

    function show(node) { UI.clear(host); host.appendChild(node); }
    function shell() {
      var wrap = el("div", {});
      if (cfg.back) wrap.appendChild(UI.backBar(cfg.back.label || "Back", cfg.back.hash));
      wrap.appendChild(el("h1", { style: "margin:0 0 4px", text: "Court audit" }));
      wrap.appendChild(el("p", { class: "cf-muted", style: "margin:0 0 12px;font-size:.88rem",
        text: "What was booked, against what was seen on court. Mark each booked session, and record any court a coach used without a booking." }));
      var tabs = el("div", { class: "cf-row", style: "gap:8px;margin-bottom:12px" });
      [["capture", "Capture a day"], ["recon", "Month recon"]].forEach(function (t) {
        tabs.appendChild(el("button", { type: "button", text: t[1],
          class: "cf-btn cf-btn-sm" + (st.tab === t[0] ? " cf-btn-primary" : ""),
          onclick: function () { st.tab = t[0]; render(); } }));
      });
      wrap.appendChild(tabs);
      return wrap;
    }
    function render() { return st.tab === "recon" ? renderRecon() : renderCapture(); }

    // ── CAPTURE ─────────────────────────────────────────────────────────────────────────────
    function renderCapture() {
      var wrap = shell();
      var body = el("div", { class: "cf-loading", style: "min-height:120px", text: "Loading…" });
      wrap.appendChild(body); show(wrap);
      var q = st.coach ? ("?coach=" + encodeURIComponent(st.coach) + "&date=" + encodeURIComponent(st.date)) : "";
      J("/day" + q).then(function (d) {
        UI.clear(body); body.className = "";
        var coachSel = el("select", { class: "cf-input", onchange: function (ev) { st.coach = ev.target.value; renderCapture(); } });
        coachSel.appendChild(el("option", { value: "", text: "Choose a coach…" }));
        (d.coaches || []).forEach(function (c) {
          coachSel.appendChild(el("option", { value: c.coach_user_id, text: c.name, selected: c.coach_user_id === st.coach ? "selected" : null }));
        });
        var dateInp = el("input", { class: "cf-input", type: "date", value: st.date, max: isoDay(new Date()),
          onchange: function (ev) { if (ev.target.value) { st.date = ev.target.value; st.month = monthOf(st.date); renderCapture(); } } });
        body.appendChild(el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap;margin-bottom:12px" }, [
          el("div", { style: "flex:1;min-width:180px" }, [coachSel]), el("div", { style: "min-width:160px" }, [dateInp]) ]));
        if (!st.coach) { body.appendChild(UI.card([el("div", { class: "cf-empty", text: "Choose a coach and a day to review." })])); return; }

        // Booked sessions — one tap each.
        var bc = UI.card([el("h3", { style: "margin:0 0 6px", text: "Booked that day" })]);
        var list = el("div", { class: "cf-list" });
        if (!(d.sessions || []).length) list.appendChild(el("div", { class: "cf-empty", text: "Nothing was booked for this coach on this day." }));
        (d.sessions || []).forEach(function (x) {
          function mark(v) {
            J("/verdict", { method: "POST", body: { kind: x.kind, ref_id: x.ref_id, verdict: (x.verdict === v ? null : v) } })
              .then(renderCapture, function (e) { UI.toast(UI.errMsg(e), "error"); });
          }
          function btn(v, label, tone) {
            return el("button", { type: "button", text: label,
              class: "cf-btn cf-btn-sm" + (x.verdict === v ? (" cf-btn-" + tone) : " cf-btn-ghost"),
              onclick: function () { mark(v); } });
          }
          list.appendChild(el("div", { class: "cf-item" }, [
            el("span", { class: "cf-chip " + x.kind, text: x.kind }),
            el("div", { class: "cf-item-main" }, [
              el("div", { class: "cf-item-t", text: UI.fmtTime(x.starts_at) + "–" + UI.fmtTime(x.ends_at) + " · " + (x.label || "") }),
              el("div", { class: "cf-item-s", text: [x.court || "No court recorded", x.minutes + " min",
                x.verdict ? (x.verdict === "verified" ? "seen on court" : "NOT seen on court") : "not reviewed yet"].join(" · ") }),
            ]),
            el("div", { class: "cf-row", style: "gap:6px" }, [btn("verified", "Seen", "primary"), btn("not_seen", "Not seen", "danger")]),
          ]));
        });
        bc.appendChild(list); body.appendChild(bc);

        // Court used with no booking.
        var uc = UI.card([el("h3", { style: "margin:0 0 4px", text: "Court used with NO booking" })], "cf-mt");
        uc.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin:0 0 8px",
          text: "Record it here. It shows on the month recon, where an admin can charge the penalty." }));
        var ul = el("div", { class: "cf-list" });
        (d.unbooked || []).forEach(function (u) {
          ul.appendChild(el("div", { class: "cf-item" }, [
            el("span", { class: "cf-chip held", text: "unbooked" }),
            el("div", { class: "cf-item-main" }, [
              el("div", { class: "cf-item-t", text: UI.fmtTime(u.starts_at) + "–" + UI.fmtTime(u.ends_at) + " · " + (u.court || "court not given") }),
              el("div", { class: "cf-item-s", text: [u.minutes + " min", u.note, u.penalty_minor ? "penalty " + money(u.penalty_minor) + " charged" : ""].filter(Boolean).join(" · ") }),
            ]),
            u.penalty_minor ? null : el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", type: "button", text: "Remove",
              onclick: function () { J("/unbooked/" + encodeURIComponent(u.id), { method: "DELETE" }).then(renderCapture, function (e) { UI.toast(UI.errMsg(e), "error"); }); } }),
          ].filter(Boolean)));
        });
        if ((d.unbooked || []).length) uc.appendChild(ul);
        var t = el("input", { class: "cf-input", type: "time", step: "300" });
        var dur = el("select", { class: "cf-input" }, [30, 45, 60, 90, 120].map(function (m) { return el("option", { value: String(m), text: m + " min", selected: m === 60 ? "selected" : null }); }));
        var court = el("select", { class: "cf-input" }, [el("option", { value: "", text: "Which court?" })].concat((d.courts || []).map(function (c) { return el("option", { value: c.id, text: c.name }); })));
        var note = el("input", { class: "cf-input", placeholder: "Note (optional) — e.g. camera 2, with a junior" });
        var add = el("button", { class: "cf-btn cf-btn-primary", type: "button", text: "Record unbooked court" });
        add.addEventListener("click", function () {
          if (!t.value) { UI.toast("Enter the time the court was in use from.", "warn"); return; }
          add.disabled = true;
          J("/unbooked", { method: "POST", body: { coach_user_id: st.coach, date: st.date, start_time: t.value,
            duration_minutes: parseInt(dur.value, 10), court_resource_id: court.value || null, note: note.value.trim() || null } })
            .then(function () { UI.toast("Recorded.", "info"); renderCapture(); },
                  function (e) { add.disabled = false; UI.toast(UI.errMsg(e), "error"); });
        });
        uc.appendChild(el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap;margin-top:8px" }, [
          el("div", { style: "min-width:120px" }, [t]), el("div", { style: "min-width:110px" }, [dur]), el("div", { style: "flex:1;min-width:150px" }, [court]) ]));
        uc.appendChild(el("div", { style: "margin-top:8px" }, [note]));
        uc.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:10px" }, [add]));
        body.appendChild(uc);
      }, function (e) { UI.clear(body); body.className = "cf-empty"; body.textContent = UI.errMsg(e); });
    }

    // ── RECON ───────────────────────────────────────────────────────────────────────────────
    function renderRecon() {
      var wrap = shell();
      var body = el("div", { class: "cf-loading", style: "min-height:120px", text: "Loading…" });
      wrap.appendChild(body); show(wrap);
      Promise.all([J("/recon?month=" + encodeURIComponent(st.month)), st.access ? Promise.resolve(st.access) : J("/access")]).then(function (res) {
        var r = res[0], acc = st.access = res[1];
        UI.clear(body); body.className = "";
        body.appendChild(el("div", { class: "cf-row", style: "gap:6px;align-items:center;margin-bottom:10px" }, [
          el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "‹", onclick: function () { st.month = shiftMonth(st.month, -1); renderRecon(); } }),
          el("span", { style: "font-weight:600;min-width:130px;text-align:center", text: monthLabel(r.month) }),
          el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "›", onclick: function () { st.month = shiftMonth(st.month, 1); renderRecon(); } }),
        ]));

        var cc = UI.card([el("h3", { style: "margin:0 0 6px", text: "By coach" })]);
        var cl = el("div", { class: "cf-list" });
        if (!(r.coaches || []).length) cl.appendChild(el("div", { class: "cf-empty", text: "Nothing booked or recorded this month." }));
        (r.coaches || []).forEach(function (c) {
          var flag = c.unbooked ? (c.unbooked + " unbooked · " + hours(c.unbooked_minutes)) : "no unbooked courts";
          cl.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { st.coach = c.coach_user_id; st.tab = "capture"; render(); } }, [
            el("div", { class: "cf-item-main" }, [
              el("div", { class: "cf-item-t", text: c.name }),
              el("div", { class: "cf-item-s", text: c.booked + " booked (" + hours(c.booked_minutes) + ") · " + c.verified + " seen · "
                + c.not_seen + " not seen · " + c.unreviewed + " not reviewed" }),
            ]),
            el("div", { style: "text-align:right;min-width:150px" }, [
              el("div", { style: "font-weight:700" + (c.unbooked ? ";color:var(--danger)" : ""), text: flag }),
              (c.penalties_minor || c.penalties_pending) ? el("div", { class: "cf-muted", style: "font-size:.76rem",
                text: [c.penalties_minor ? money(c.penalties_minor) + " charged" : "", c.penalties_pending ? c.penalties_pending + " not charged" : ""].filter(Boolean).join(" · ") }) : null,
            ].filter(Boolean)),
          ]));
        });
        cc.appendChild(cl); body.appendChild(cc);

        var uc = UI.card([el("h3", { style: "margin:0 0 6px", text: "Courts used without a booking" })], "cf-mt");
        var ul = el("div", { class: "cf-list" });
        if (!(r.unbooked || []).length) ul.appendChild(el("div", { class: "cf-empty", text: "None recorded this month." }));
        (r.unbooked || []).forEach(function (u) {
          var right;
          if (u.penalty_minor) right = el("span", { class: "cf-chip confirmed", text: money(u.penalty_minor) + " charged" });
          else if (acc.is_admin) {
            right = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", type: "button", text: "Charge " + money(r.default_penalty_minor),
              onclick: function () {
                if (!window.confirm("Charge " + u.coach_name + " " + money(r.default_penalty_minor) + " for the unbooked court on "
                    + UI.fmtDate(u.starts_at) + " at " + UI.fmtTime(u.starts_at) + "?\n\nIt comes off what the club owes them for that month.")) return;
                J("/unbooked/" + encodeURIComponent(u.id) + "/penalty", { method: "POST", body: {} })
                  .then(function () { UI.toast("Penalty charged.", "info"); renderRecon(); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
              } });
          } else right = el("span", { class: "cf-chip held", text: "not charged" });
          ul.appendChild(el("div", { class: "cf-item" }, [
            el("div", { class: "cf-item-main" }, [
              el("div", { class: "cf-item-t", text: u.coach_name + " · " + UI.fmtDate(u.starts_at) + " · " + UI.fmtTime(u.starts_at) + "–" + UI.fmtTime(u.ends_at) }),
              el("div", { class: "cf-item-s", text: [u.court || "court not given", u.minutes + " min", u.note, u.captured_by ? "recorded by " + u.captured_by : ""].filter(Boolean).join(" · ") }),
            ]), right ]));
        });
        uc.appendChild(ul); body.appendChild(uc);

        if (acc.is_admin) body.appendChild(accessCard(acc));
      }, function (e) { UI.clear(body); body.className = "cf-empty"; body.textContent = UI.errMsg(e); });
    }

    // Who may capture (admin only). Admins always can; this is the list of everyone else.
    function accessCard(acc) {
      var c = UI.card([el("h3", { style: "margin:0 0 4px", text: "Who can capture" })], "cf-mt");
      c.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin:0 0 8px",
        text: "Admins always can. Anyone listed here can capture and see the recon — they cannot charge a penalty or see anything else in the admin console." }));
      var l = el("div", { class: "cf-list" });
      function setAccess(userId, allowed) {
        return J("/access", { method: "POST", body: { user_id: userId, allowed: allowed } })
          .then(function () { st.access = null; renderRecon(); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
      }
      (acc.auditors || []).forEach(function (a) {
        l.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: a.name })]),
          el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", type: "button", text: "Remove", onclick: function () { setAccess(a.user_id, false); } }) ]));
      });
      if (!(acc.auditors || []).length) l.appendChild(el("div", { class: "cf-empty", text: "Nobody yet — only admins can capture." }));
      c.appendChild(l);
      if (typeof cfg.searchMembers === "function") {
        var q = el("input", { class: "cf-input", placeholder: "Add someone — search their name…", style: "margin-top:8px" });
        var res = el("div", { class: "cf-list" });
        var tmr;
        q.addEventListener("input", function () {
          clearTimeout(tmr);
          var term = q.value.trim(); if (term.length < 2) { UI.clear(res); return; }
          tmr = setTimeout(function () {
            cfg.searchMembers(term).then(function (r) {
              UI.clear(res);
              var seen = {};
              ((r && r.results) || []).filter(function (m) { return m.kind === "member" && !seen[m.user_id] && (seen[m.user_id] = 1); }).forEach(function (m) {
                res.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { setAccess(m.user_id, true); } }, [
                  el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: m.name }), el("div", { class: "cf-item-s", text: m.email || "" })]),
                  el("span", { class: "cf-muted", text: "Add ›" }) ]));
              });
            }, function () {});
          }, 250);
        });
        c.appendChild(q); c.appendChild(res);
      }
      return c;
    }

    render();
    return { refresh: render, destroy: function () { UI.clear(host); } };
  }

  window.Widgets.Timesheet = { mount: mount };
})();
