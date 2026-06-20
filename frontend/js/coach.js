// coach.js — coach console (docs/08 §2): my week (lessons + classes I run), class
// rosters + mark attendance, my availability/time-off editor. No pricing/finance.
// Calls GET /api/diary/bookings?as_coach=1, GET /api/diary/classes, GET /api/diary/resources,
// POST /api/diary/bookings/:id/status, POST /api/diary/time-off.
(function () {
  var UI, el, principal;

  async function loadWeek() {
    var box = document.getElementById("coach-week");
    UI.clear(box); box.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
    try {
      var from = UI.dateKey(new Date());
      var to = UI.dateKey(UI.addDays(new Date(), 7));
      var [bk, cls] = await Promise.all([
        window.API.bookings({ date_from: from, date_to: to, as_coach: "1" }),
        window.API.classes({ date_from: from, date_to: to }),
      ]);
      renderWeek(bk.bookings || [], (cls.classes || []).filter(function (c) {
        return String(c.coach_user_id) === String(principal.user_id);
      }));
    } catch (e) { box.innerHTML = ""; box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }

  function renderWeek(lessons, classes) {
    var box = document.getElementById("coach-week"); UI.clear(box);
    if (!lessons.length && !classes.length) { box.appendChild(el("div", { class: "cf-empty", text: "Nothing scheduled this week." })); return; }

    if (lessons.length) {
      box.appendChild(el("h3", { text: "Lessons" }));
      var ll = el("div", { class: "cf-list" });
      lessons.forEach(function (b) {
        var actions = [];
        if (["held", "confirmed"].indexOf(b.status) >= 0) {
          actions.push(el("button", { class: "cf-btn cf-btn-sm", text: "Completed", onclick: function () { setStatus(b.id, "completed"); } }));
          actions.push(el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "No-show", onclick: function () { setStatus(b.id, "no_show"); } }));
        }
        ll.appendChild(el("div", { class: "cf-item" }, [
          el("span", { class: "cf-chip lesson", text: "lesson" }),
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: b.resource_name || "Lesson" }),
            el("div", { class: "cf-item-s", text: UI.fmtRange(b.starts_at, b.ends_at) }),
          ]),
          el("span", { class: "cf-chip " + b.status, text: b.status }),
        ].concat(actions)));
      });
      box.appendChild(ll);
    }

    if (classes.length) {
      box.appendChild(el("h3", { text: "Classes I run", style: "margin-top:16px" }));
      var cl = el("div", { class: "cf-list" });
      classes.forEach(function (c) {
        cl.appendChild(el("div", { class: "cf-item" }, [
          el("span", { class: "cf-chip class", text: "class" }),
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: c.class_name || "Class" }),
            el("div", { class: "cf-item-s", text: UI.fmtRange(c.starts_at, c.ends_at) +
              " · " + (c.enrolled || 0) + " enrolled" + (c.waitlisted ? " · " + c.waitlisted + " waitlisted" : "") }),
          ]),
          el("button", { class: "cf-btn cf-btn-sm", text: "Roster", onclick: function () { openRoster(c); } }),
        ]));
      });
      box.appendChild(cl);
    }
  }

  async function setStatus(id, status) {
    try { await window.API.setBookingStatus(id, { status: status }); UI.toast("Updated.", "info"); loadWeek(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  // Roster modal: the class session id maps to its bookings via the booking list
  // filtered by resource. Mark attendance uses POST /status {status:"attended", party_id}.
  async function openRoster(c) {
    var bg = el("div", { class: "cf-modal-bg" });
    var body = el("div", { id: "roster-body", class: "cf-loading", text: "Loading roster…" });
    var modal = el("div", { class: "cf-modal" }, [
      el("h2", { text: (c.class_name || "Class") + " roster" }),
      el("p", { class: "cf-muted", text: UI.fmtRange(c.starts_at, c.ends_at) }),
      body,
      el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px" }, [
        el("button", { class: "cf-btn", text: "Close", onclick: function () { document.body.removeChild(bg); } }),
      ]),
    ]);
    bg.appendChild(modal); document.body.appendChild(bg);
    try {
      // Class enrolments surface as bookings on the class resource for attendance marking.
      var r = await window.API.bookings({ date_from: UI.dateKey(new Date(c.starts_at)),
        date_to: UI.dateKey(UI.addDays(new Date(c.starts_at), 1)), resource_id: c.resource_id, as_coach: "1" });
      renderRoster(body, r.bookings || []);
    } catch (e) { body.textContent = UI.errMsg(e); }
  }

  function renderRoster(body, bookings) {
    UI.clear(body);
    if (!bookings.length) { body.appendChild(el("div", { class: "cf-empty", text: "No enrolments to mark, or the roster is exposed via parties on each booking." })); return; }
    var list = el("div", { class: "cf-list" });
    bookings.forEach(function (b) {
      list.appendChild(el("div", { class: "cf-item" }, [
        el("div", { class: "cf-item-main" }, [ el("div", { class: "cf-item-t", text: b.resource_name || "Player" }) ]),
        el("button", { class: "cf-btn cf-btn-sm", text: "Attended", onclick: function () {
          window.API.setBookingStatus(b.id, { status: "attended", attended: true })
            .then(function () { UI.toast("Marked.", "info"); })
            .catch(function (e) { UI.toast(UI.errMsg(e), "error"); });
        } }),
      ]));
    });
    body.appendChild(list);
  }

  // ---- availability / time-off ---------------------------------------------
  async function loadResources() {
    try {
      var r = await window.API.resources();
      var mine = (r.resources || []).filter(function (x) {
        return x.kind === "coach" && String(x.coach_user_id) === String(principal.user_id);
      });
      var sel = document.getElementById("to-resource");
      UI.clear(sel);
      if (!mine.length) { sel.appendChild(el("option", { value: "", text: "No coach resource for you" })); return; }
      mine.forEach(function (res) { sel.appendChild(el("option", { value: res.id, text: res.name })); });
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  async function submitTimeOff() {
    var resId = document.getElementById("to-resource").value;
    var start = document.getElementById("to-start").value;
    var end = document.getElementById("to-end").value;
    var reason = document.getElementById("to-reason").value;
    if (!resId || !start || !end) { UI.toast("Pick a resource and a time range.", "warn"); return; }
    try {
      await window.API.timeOff({
        resource_id: resId,
        starts_at: new Date(start).toISOString(),
        ends_at: new Date(end).toISOString(),
        reason: reason || "time off",
      });
      UI.toast("Time off blocked.", "info");
      document.getElementById("to-start").value = "";
      document.getElementById("to-end").value = "";
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  window.CoachConsole = {
    start: function (p) {
      UI = window.UI; el = UI.el; principal = p;
      loadWeek(); loadResources();
      document.getElementById("to-submit").addEventListener("click", submitTimeOff);
    },
  };
})();
