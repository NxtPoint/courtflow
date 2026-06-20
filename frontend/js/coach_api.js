// coach_api.js — thin typed wrappers over the live coach onboarding/profile APIs.
//
// Mirrors admin_api.js: every wrapper maps 1:1 to a route in the coach lane. The
// user_id and club_id are NEVER sent in the body — the server derives both from the
// Clerk JWT principal. All calls go through TFAuth.apiJSON (Bearer header; throws
// {status, body} on non-2xx).
//
// Exposes window.CoachAPI. Used by coach_onboarding.js + coach.js. Does NOT touch
// api.js / admin_api.js.
(function () {
  function A() {
    if (!window.TFAuth) throw new Error("auth_client.js must load before coach_api.js");
    return window.TFAuth;
  }
  function enc(id) { return encodeURIComponent(id); }
  function _qs(params) {
    var p = new URLSearchParams();
    Object.keys(params || {}).forEach(function (k) {
      var v = params[k];
      if (v !== undefined && v !== null && v !== "") p.set(k, v);
    });
    var s = p.toString();
    return s ? ("?" + s) : "";
  }

  var CoachAPI = {
    // ---- onboarding ------------------------------------------------------
    // GET /api/coach/onboarding ->
    //   {completed, steps:{profile,hours,services},
    //    profile:{display_name,headline,bio,photo_url,specialties[],phone,
    //             first_name,surname,email},
    //    hours:{week:[{weekday,open,start_time,end_time,slot_minutes}]},
    //    services:[{price_id,product_id,name,amount_minor,unit,duration_minutes}]}
    onboarding: function () { return A().apiJSON("/api/coach/onboarding"); },
    // POST /api/coach/onboarding/complete -> {ok:true}
    completeOnboarding: function () {
      return A().apiJSON("/api/coach/onboarding/complete", { method: "POST", body: {} });
    },

    // ---- profile ---------------------------------------------------------
    // GET /api/coach/profile -> {profile:{...}}
    profile: function () { return A().apiJSON("/api/coach/profile"); },
    // PATCH /api/coach/profile  body:
    //   {display_name,headline,bio,photo_url,specialties[],phone,first_name,surname}
    patchProfile: function (body) {
      return A().apiJSON("/api/coach/profile", { method: "PATCH", body: body });
    },

    // ---- working hours ---------------------------------------------------
    // PUT /api/coach/hours  body:
    //   {week:[{weekday,open,start_time"HH:MM",end_time"HH:MM",slot_minutes}]}
    putHours: function (body) {
      return A().apiJSON("/api/coach/hours", { method: "PUT", body: body });
    },

    // ---- services & rates ------------------------------------------------
    // GET /api/coach/services -> {services:[{price_id,product_id,name,amount_minor,
    //   unit,duration_minutes,audience}]}
    services: function () { return A().apiJSON("/api/coach/services"); },
    // POST /api/coach/services  body: {name,duration_minutes,amount_minor,audience}
    createService: function (body) {
      return A().apiJSON("/api/coach/services", { method: "POST", body: body });
    },
    // PATCH /api/coach/services/:price_id  body: {name,duration_minutes,amount_minor,audience}
    patchService: function (priceId, body) {
      return A().apiJSON("/api/coach/services/" + enc(priceId), { method: "PATCH", body: body });
    },
    // DELETE /api/coach/services/:price_id
    deleteService: function (priceId) {
      return A().apiJSON("/api/coach/services/" + enc(priceId), { method: "DELETE" });
    },

    // ---- profile photo ---------------------------------------------------
    // POST /api/coach/photo-presign  body: {filename,content_type}
    //   -> {url,public_url}  (S3 PUT target + the public URL to store), or
    //   -> {configured:false} when object storage isn't wired up (caller falls
    //      back to a plain "photo URL" text field).
    photoPresign: function (body) {
      return A().apiJSON("/api/coach/photo-presign", { method: "POST", body: body });
    },

    // ---- classes (a coach manages only their OWN) -----------------------
    // GET /api/coach/classes -> {classes:[{resource_id,name,coach_user_id,coach_name,
    //   capacity,price_amount_minor,duration_minutes,upcoming_sessions}]}
    classes: function () { return A().apiJSON("/api/coach/classes"); },
    // POST /api/coach/classes  body: {name,capacity,price_amount_minor,duration_minutes,
    //   description?}  (no coach_user_id — the server uses the caller) -> {resource_id,...}
    createClass: function (body) {
      return A().apiJSON("/api/coach/classes", { method: "POST", body: body });
    },
    // POST /api/coach/classes/:resource_id/schedule
    //   recurring: {weekdays:[0-6],start_time,duration_minutes?,date_from,date_until,capacity?}
    //   one-off:   {dates:[...],start_time,duration_minutes?,capacity?}  -> {created, skipped}
    scheduleClass: function (resourceId, body) {
      return A().apiJSON("/api/coach/classes/" + enc(resourceId) + "/schedule",
        { method: "POST", body: body });
    },
    // GET /api/coach/classes/:resource_id/sessions?date_from=&date_to=
    //   -> {sessions:[{session_id,starts_at,ends_at,capacity,enrolled,waitlisted,spots_left,status}]}
    classSessions: function (resourceId, opts) {
      return A().apiJSON("/api/coach/classes/" + enc(resourceId) + "/sessions" + _qs(opts));
    },
    // POST /api/coach/classes/sessions/:session_id/cancel
    cancelClassSession: function (sessionId, body) {
      return A().apiJSON("/api/coach/classes/sessions/" + enc(sessionId) + "/cancel",
        { method: "POST", body: body || {} });
    },

    // ---- class rosters / attendance (shared diary lane) -----------------
    // GET /api/diary/classes/:session_id/roster
    //   -> {enrolled:[{user_id,name,email,status}], waitlisted:[...]}
    classRoster: function (sessionId) {
      return A().apiJSON("/api/diary/classes/" + enc(sessionId) + "/roster");
    },
    // POST /api/diary/classes/:session_id/attendance  body: {user_id, attended}
    classAttendance: function (sessionId, body) {
      return A().apiJSON("/api/diary/classes/" + enc(sessionId) + "/attendance",
        { method: "POST", body: body });
    },
  };

  window.CoachAPI = CoachAPI;
})();

// CoachUI — shared section components reused by BOTH the coach onboarding wizard
// (coach_onboarding.js) and the coach console "My profile" editor (coach.js). Each
// builder renders one editable section into a host element and wires its own Save ->
// CoachAPI call. Pure presentation + the API calls above; depends on window.UI +
// window.CoachAPI. Mirrors the AdminUI section-component pattern in admin_api.js.
(function () {
  var UI, el;
  function init() { if (!UI) { UI = window.UI; el = UI.el; } }

  var WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  // ---- small form helpers (same shapes as AdminUI) ---------------------------
  function field(label, control) {
    return el("div", { class: "cf-field" }, [el("label", { text: label }), control]);
  }
  function input(opts) { return el("input", Object.assign({ class: "cf-input" }, opts || {})); }
  function textarea(opts) { return el("textarea", Object.assign({ class: "cf-input" }, opts || {})); }
  function select(value, options) {
    var s = el("select", { class: "cf-select" });
    (options || []).forEach(function (o) {
      var val = (typeof o === "object") ? o.value : o;
      var lbl = (typeof o === "object") ? o.label : o;
      var opt = el("option", { value: val, text: lbl });
      if (String(val) === String(value)) opt.selected = true;
      s.appendChild(opt);
    });
    return s;
  }
  function num(v) { var n = parseInt(v, 10); return isNaN(n) ? null : n; }
  // major currency string -> amount_minor (cents). "85.50" -> 8550.
  function toMinor(v) {
    if (v === "" || v == null) return null;
    var f = parseFloat(v); if (isNaN(f)) return null;
    return Math.round(f * 100);
  }
  function fromMinor(m) { return (m == null) ? "" : (m / 100).toFixed(2); }
  function actionRow(children) { return el("div", { class: "cf-row", style: "margin-top:14px" }, children); }

  // ---------------------------------------------------------------------------
  // SPECIALTIES — a chip/tag editor. Renders removable cf-chips + an "add" input.
  // Returns {el, value()} where value() yields the current string[] of tags.
  // ---------------------------------------------------------------------------
  function specialtyEditor(initial) {
    var tags = (initial || []).slice();
    var chips = el("div", { class: "cf-row", style: "flex-wrap:wrap;gap:6px" });
    var addI = input({ placeholder: "Add a specialty (e.g. Junior development) + Enter", style: "max-width:280px" });
    function draw() {
      UI.clear(chips);
      tags.forEach(function (t, idx) {
        var x = el("button", { class: "cf-chip", style: "cursor:pointer;border:0",
          text: t + "  ✕", title: "Remove",
          onclick: function () { tags.splice(idx, 1); draw(); } });
        chips.appendChild(x);
      });
      if (!tags.length) chips.appendChild(el("span", { class: "cf-muted", text: "No specialties yet." }));
    }
    function add() {
      var v = addI.value.trim();
      if (!v) return;
      if (tags.indexOf(v) < 0) tags.push(v);
      addI.value = ""; draw();
    }
    addI.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); add(); }
    });
    var addBtn = el("button", { class: "cf-btn cf-btn-sm", text: "Add", onclick: add });
    draw();
    var wrap = el("div", {}, [chips, el("div", { class: "cf-row", style: "margin-top:6px" }, [addI, addBtn])]);
    return { el: wrap, value: function () { return tags.slice(); } };
  }

  // ---------------------------------------------------------------------------
  // PROFILE — photo, display name, headline, bio, specialties, phone.
  //   -> PATCH /coach/profile (+ POST /coach/photo-presign for the photo upload).
  // data: the onboarding/profile `profile` object. opts.onSaved fires on success.
  // ---------------------------------------------------------------------------
  function profile(host, data, opts) {
    init(); opts = opts || {};
    var p = data || {};

    var photoUrl = p.photo_url || "";
    var previewWrap = el("div", { class: "cf-row", style: "align-items:center;gap:12px;flex-wrap:wrap" });
    var fileI = input({ type: "file", accept: "image/*", style: "max-width:260px" });
    // URL fallback field — shown when presign reports {configured:false}, or as a
    // manual override. Hidden by default; revealed lazily.
    var urlI = input({ value: photoUrl, placeholder: "https://… photo URL", type: "url", style: "max-width:320px" });
    var urlField = field("…or paste a photo URL", urlI);
    urlField.style.display = photoUrl ? "" : "none";

    function drawPreview() {
      UI.clear(previewWrap);
      if (photoUrl) {
        previewWrap.appendChild(el("img", { src: photoUrl, alt: "Profile photo",
          style: "width:64px;height:64px;border-radius:50%;object-fit:cover;border:1px solid var(--border)" }));
      } else {
        previewWrap.appendChild(el("span", { class: "cf-chip coach", text: "No photo yet" }));
      }
    }
    drawPreview();

    // Photo file -> presign -> PUT to storage -> store public_url. If the backend
    // reports {configured:false}, reveal the URL field and ask for a link instead.
    fileI.addEventListener("change", async function () {
      var file = fileI.files && fileI.files[0];
      if (!file) return;
      try {
        var pre = await window.CoachAPI.photoPresign({ filename: file.name, content_type: file.type || "application/octet-stream" });
        if (!pre || pre.configured === false || !pre.url) {
          urlField.style.display = "";
          UI.toast("Photo uploads aren't configured — paste a photo URL instead.", "warn");
          fileI.value = "";
          return;
        }
        var put = await fetch(pre.url, { method: "PUT", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file });
        if (!put.ok) throw new Error("Upload failed (" + put.status + ").");
        photoUrl = pre.public_url || pre.url.split("?")[0];
        urlI.value = photoUrl;
        drawPreview();
        UI.toast("Photo uploaded.", "info");
      } catch (e) {
        urlField.style.display = "";
        UI.toast(UI.errMsg(e), "error");
      }
    });
    // Manual URL edit keeps the preview in sync.
    urlI.addEventListener("change", function () { photoUrl = urlI.value.trim(); drawPreview(); });

    var f = {
      first: input({ value: p.first_name || "", placeholder: "First name", style: "max-width:200px" }),
      surname: input({ value: p.surname || "", placeholder: "Surname", style: "max-width:200px" }),
      display: input({ value: p.display_name || "", placeholder: "How your name appears to members" }),
      headline: input({ value: p.headline || "", placeholder: "e.g. LTA Level 3 coach · 10+ years" }),
      bio: textarea({ placeholder: "Tell members about your coaching style and experience…", rows: 5 }),
      phone: input({ value: p.phone || "", placeholder: "Cell phone", type: "tel", style: "max-width:220px" }),
    };
    f.bio.value = p.bio || "";
    var spec = specialtyEditor(p.specialties || []);

    var card = el("div", { class: "cf-card" }, [
      el("h2", { text: "Your coaching profile" }),
      field("Profile photo", el("div", {}, [previewWrap, el("div", { class: "cf-row", style: "margin-top:8px" }, [fileI])])),
      urlField,
      el("div", { class: "cf-grid cf-grid-2" }, [field("First name", f.first), field("Surname", f.surname)]),
      field("Display name", f.display),
      field("Headline", f.headline),
      field("Bio", f.bio),
      field("Specialties", spec.el),
      field("Cell phone", f.phone),
    ]);
    var btn = el("button", { class: "cf-btn cf-btn-primary", text: opts.saveLabel || "Save" });
    card.appendChild(actionRow((opts.before || []).concat([btn])));

    btn.addEventListener("click", async function () {
      var display = f.display.value.trim()
        || (f.first.value.trim() + " " + f.surname.value.trim()).trim();
      if (!display) { UI.toast("A display name is required.", "warn"); return; }
      btn.disabled = true; btn.textContent = "Saving…";
      try {
        await window.CoachAPI.patchProfile({
          display_name: display,
          headline: f.headline.value.trim(),
          bio: f.bio.value.trim(),
          photo_url: (photoUrl || urlI.value.trim()) || null,
          specialties: spec.value(),
          phone: f.phone.value.trim(),
          first_name: f.first.value.trim(),
          surname: f.surname.value.trim(),
        });
        UI.toast("Profile saved.", "info");
        if (typeof opts.onSaved === "function") opts.onSaved();
      } catch (e) {
        UI.toast(UI.errMsg(e), "error");
      } finally { btn.disabled = false; btn.textContent = opts.saveLabel || "Save"; }
    });
    UI.clear(host); host.appendChild(card);
  }

  // ---------------------------------------------------------------------------
  // WORKING HOURS — Mon–Sun grid (same UX as the owner wizard). -> PUT /coach/hours.
  // data: {week:[{weekday,open,start_time,end_time,slot_minutes}]} (any source).
  // ---------------------------------------------------------------------------
  function hours(host, data, opts) {
    init(); opts = opts || {};
    var existing = {};
    ((data && data.week) || []).forEach(function (w) { existing[w.weekday] = w; });
    var rows = [];
    var card = el("div", { class: "cf-card" }, [
      el("h2", { text: "Working hours" }),
      el("p", { class: "cf-muted", text: "When you're available to coach each day." }),
    ]);
    var grid = el("div", { class: "cf-list" });
    WEEKDAYS.forEach(function (lbl, wd) {
      var w = existing[wd] || { open: wd < 6, start_time: "08:00", end_time: "18:00", slot_minutes: 60 };
      var openTgl = input({ type: "checkbox" }); openTgl.checked = !!w.open;
      var start = input({ type: "time", value: w.start_time || "08:00", style: "max-width:130px" });
      var end = input({ type: "time", value: w.end_time || "18:00", style: "max-width:130px" });
      var slot = select(w.slot_minutes || 60, [
        { value: 30, label: "30 min" }, { value: 45, label: "45 min" },
        { value: 60, label: "60 min" }, { value: 90, label: "90 min" }, { value: 120, label: "120 min" },
      ]);
      rows.push({ wd: wd, openTgl: openTgl, start: start, end: end, slot: slot });
      var labelCell = el("label", { class: "cf-row", style: "gap:6px;min-width:96px;font-weight:600" },
        [openTgl, el("span", { text: lbl })]);
      grid.appendChild(el("div", { class: "cf-item", style: "flex-wrap:wrap" }, [
        labelCell,
        el("div", { class: "cf-row", style: "gap:6px" }, [
          start, el("span", { class: "cf-muted", text: "to" }), end, slot,
        ]),
      ]));
    });
    card.appendChild(grid);
    var btn = el("button", { class: "cf-btn cf-btn-primary", text: opts.saveLabel || "Save hours" });
    card.appendChild(actionRow((opts.before || []).concat([btn])));
    btn.addEventListener("click", async function () {
      var week = rows.map(function (r) {
        return {
          weekday: r.wd, open: r.openTgl.checked,
          start_time: r.start.value || "08:00", end_time: r.end.value || "18:00",
          slot_minutes: num(r.slot.value) || 60,
        };
      });
      btn.disabled = true; btn.textContent = "Saving…";
      try {
        await window.CoachAPI.putHours({ week: week });
        UI.toast("Working hours saved.", "info");
        if (typeof opts.onSaved === "function") opts.onSaved();
      } catch (e) { UI.toast(UI.errMsg(e), "error"); }
      finally { btn.disabled = false; btn.textContent = opts.saveLabel || "Save hours"; }
    });
    UI.clear(host); host.appendChild(card);
  }

  // ---------------------------------------------------------------------------
  // SERVICES & RATES — repeatable rows of {lesson name, duration, price}.
  //   -> GET /coach/services, POST/PATCH/DELETE /coach/services[/:price_id].
  // ---------------------------------------------------------------------------
  var DURATIONS = [
    { value: 30, label: "30 min" }, { value: 45, label: "45 min" },
    { value: 60, label: "60 min" }, { value: 90, label: "90 min" }, { value: 120, label: "120 min" },
  ];

  function services(host, opts) {
    init(); opts = opts || {};
    UI.clear(host);
    var card = el("div", { class: "cf-card" });
    card.appendChild(el("h2", { text: "Services & rates" }));
    card.appendChild(el("p", { class: "cf-muted", text: "Add the lessons you offer with their length and price." }));
    var listBox = el("div", { class: "cf-list", id: "co-services" });
    card.appendChild(listBox);
    host.appendChild(card);

    function reload() {
      UI.clear(listBox);
      listBox.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
      window.CoachAPI.services().then(function (r) { renderList(r.services || []); })
        .catch(function (e) { UI.clear(listBox); listBox.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    function renderList(list) {
      UI.clear(listBox);
      if (!list.length) { listBox.appendChild(el("div", { class: "cf-empty", text: "No services yet. Add one below." })); return; }
      list.forEach(function (s) {
        var nameI = input({ value: s.name || "", placeholder: "Lesson name", style: "max-width:220px" });
        var durI = select(s.duration_minutes || 60, DURATIONS);
        var amtI = input({ value: fromMinor(s.amount_minor), placeholder: "0.00", style: "max-width:110px" });
        var save = el("button", { class: "cf-btn cf-btn-sm", text: "Save" });
        var del = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Delete" });
        save.addEventListener("click", async function () {
          var nm = nameI.value.trim();
          if (!nm) { UI.toast("Enter a lesson name.", "warn"); return; }
          save.disabled = true;
          try {
            await window.CoachAPI.patchService(s.price_id, {
              name: nm, duration_minutes: num(durI.value) || 60, amount_minor: toMinor(amtI.value),
            });
            UI.toast("Service updated.", "info");
          } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { save.disabled = false; }
        });
        del.addEventListener("click", async function () {
          if (!window.confirm("Delete " + (s.name || "this service") + "?")) return;
          try { await window.CoachAPI.deleteService(s.price_id); UI.toast("Service deleted.", "info"); reload(); }
          catch (e) { UI.toast(UI.errMsg(e), "error"); }
        });
        listBox.appendChild(el("div", { class: "cf-item", style: "flex-wrap:wrap;gap:6px" }, [
          el("span", { class: "cf-chip lesson", text: "lesson" }),
          nameI, durI, amtI, el("span", { class: "cf-spacer" }), save, del,
        ]));
      });
    }

    // add-service form
    var addName = input({ placeholder: "Lesson name (e.g. Private 1:1)", style: "max-width:240px" });
    var addDur = select(60, DURATIONS);
    var addAmt = input({ placeholder: "0.00", style: "max-width:110px" });
    var addBtn = el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Add service" });
    addBtn.addEventListener("click", async function () {
      var nm = addName.value.trim();
      if (!nm) { UI.toast("Enter a lesson name.", "warn"); return; }
      addBtn.disabled = true;
      try {
        await window.CoachAPI.createService({
          name: nm, duration_minutes: num(addDur.value) || 60,
          amount_minor: toMinor(addAmt.value), audience: "member",
        });
        addName.value = ""; addAmt.value = ""; UI.toast("Service added.", "info"); reload();
      } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { addBtn.disabled = false; }
    });
    card.appendChild(el("h3", { text: "Add a service", style: "margin-top:14px" }));
    card.appendChild(el("div", { class: "cf-row", style: "gap:6px;flex-wrap:wrap" }, [addName, addDur, addAmt, addBtn]));
    if (opts.before && opts.before.length) card.appendChild(actionRow(opts.before));

    reload();
    return { reload: reload };
  }

  window.CoachUI = {
    profile: profile, hours: hours, services: services,
  };
})();
