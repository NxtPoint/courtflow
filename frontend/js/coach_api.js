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
    // GET /api/coach/members/search?q= -> {members:[{user_id,name,email,phone}]} — 'book a client' lookup.
    searchMembers: function (q) { return A().apiJSON("/api/coach/members/search?q=" + encodeURIComponent(q || "")); },
    // POST /api/coach/clients {first_name, surname, email, phone?} — create a new client (walk-up).
    createClient: function (body) { return A().apiJSON("/api/coach/clients", { method: "POST", body: body || {} }); },
    // A client's active lesson packs THIS coach can draw (for on-behalf auto-routing to their pack).
    clientPackages: function (userId) { return A().apiJSON("/api/coach/members/" + enc(userId) + "/packages"); },
    // Every client holding an active pack with this coach (the 'clients with packages' view).
    packages: function () { return A().apiJSON("/api/coach/packages"); },
    // POST /api/coach/onboarding/complete -> {ok:true}
    completeOnboarding: function () {
      return A().apiJSON("/api/coach/onboarding/complete", { method: "POST", body: {} });
    },

    // ---- profile ---------------------------------------------------------
    // GET /api/coach/profile -> {profile:{...}}
    profile: function () { return A().apiJSON("/api/coach/profile"); },
    // PATCH /api/coach/profile  body:
    //   {display_name,headline,bio,photo_url,specialties[],languages[],qualifications[],
    //    years_experience,is_bookable,public_visibility,review_bookings,phone,first_name,surname}
    //   (rank is admin-only — ignored if sent.)
    patchProfile: function (body) {
      return A().apiJSON("/api/coach/profile", { method: "PATCH", body: body });
    },

    // ---- working hours ---------------------------------------------------
    // PUT /api/coach/hours  body:
    //   {week:[{weekday,open,start_time"HH:MM",end_time"HH:MM",slot_minutes}]}
    putHours: function (body) {
      return A().apiJSON("/api/coach/hours", { method: "PUT", body: body });
    },

    // ---- services & rates (PER-DURATION per_booking — see diary/pricing.py) ----
    // GET /api/coach/services -> {services:[{price_id,product_id,name,amount_minor,
    //   unit:'per_booking',duration_minutes,audience}]}
    services: function () { return A().apiJSON("/api/coach/services"); },
    // POST /api/coach/services  body: {name,duration_minutes,amount_minor} -> {service}
    //   (server defaults unit='per_booking', audience='any' so the rate prices + books.)
    createService: function (body) {
      return A().apiJSON("/api/coach/services", { method: "POST", body: body });
    },
    // POST /api/coach/services/:product_id/rate  body: {duration_minutes,amount_minor}
    //   adds another per-duration rate to an existing lesson product -> {service}
    addServiceRate: function (productId, body) {
      return A().apiJSON("/api/coach/services/" + enc(productId) + "/rate",
        { method: "POST", body: body });
    },
    // PATCH /api/coach/services/:price_id  body: {name,duration_minutes,amount_minor}
    patchService: function (priceId, body) {
      return A().apiJSON("/api/coach/services/" + enc(priceId), { method: "PATCH", body: body });
    },
    // DELETE /api/coach/services/:price_id
    deleteService: function (priceId) {
      return A().apiJSON("/api/coach/services/" + enc(priceId), { method: "DELETE" });
    },


    // ---- time-off (view + remove; POST stays in the diary lane) ----------
    // GET /api/coach/time-off[?all=1] -> {time_off:[{id,resource_id,resource_name,
    //   starts_at,ends_at,reason}], count}  (upcoming-only by default)
    timeOff: function (opts) { return A().apiJSON("/api/coach/time-off" + _qs(opts)); },
    // DELETE /api/coach/time-off/:id -> {ok:true}
    deleteTimeOff: function (id) {
      return A().apiJSON("/api/coach/time-off/" + enc(id), { method: "DELETE" });
    },

    // ---- my clients (read-only derivation; THIS coach only) --------------
    // GET /api/coach/clients[?search=&limit=] -> {clients:[{user_id,first_name,surname,
    //   email,phone,first_seen,last_seen,lessons_count,classes_count,no_show_count,
    //   upcoming_count,lifetime_spend_minor}], count}
    clients: function (opts) { return A().apiJSON("/api/coach/clients" + _qs(opts)); },
    // GET /api/coach/clients/:id/360[?month=YYYY-MM] -> {person:{...}} — the ONE client-360 composer
    //   payload (scope='coach': coaching + packages filtered to THIS coach; can:{discount,collect};
    //   month scopes coaching + adds service_breakdown). Feeds Widgets.ClientRecord (golden rule).
    client360: function (userId, month) {
      return A().apiJSON("/api/coach/clients/" + enc(userId) + "/360" + (month ? ("?month=" + enc(month)) : ""));
    },
    // GET /api/coach/money[?month=YYYY-MM] — the coach Money tab as an OUTCOME of bookings:
    //   {month,currency,commission_pct, totals:{billed_minor,discount_minor,written_off_minor,
    //    invoiced_minor,paid_minor,outstanding_minor,refunded_minor,commission_minor,net_minor,
    //    balance_minor}, clients:[{client_user_id,client_name,count,...same fold fields}]}
    //   Everything folds from THIS month's sessions, so it reconciles: billed−disc−wo=invoiced=paid+out.
    money: function (month) { return A().apiJSON("/api/coach/money" + (month ? ("?month=" + enc(month)) : "")); },
    // Earnings by service (the coach's own slice — same shape/widget as admin).
    earningsByService: function (month) { return A().apiJSON("/api/coach/financials/earnings-by-service" + (month ? ("?month=" + enc(month)) : "")); },
    // The coach's OWN earnings P&L (their Money landing) — sales/net/received/owed + keep-vs-commission.
    revenueMe: function (month) { return A().apiJSON("/api/coach/financials/revenue-me" + (month ? ("?month=" + enc(month)) : "")); },
    // The coach revenue drill's CLIENT level (coach-scoped — no by-coach level; they ARE the coach).
    earningsClients: function (opts) {
      opts = opts || {};
      var q = [];
      if (opts.category) q.push("category=" + enc(opts.category));
      if (opts.month) q.push("month=" + enc(opts.month));
      return A().apiJSON("/api/coach/financials/revenue-clients" + (q.length ? ("?" + q.join("&")) : ""));
    },
    earningsTransactions: function (opts) {
      opts = opts || {};
      var q = [];
      if (opts.category) q.push("category=" + enc(opts.category));
      if (opts.user_id) q.push("user_id=" + enc(opts.user_id));
      if (opts.month) q.push("month=" + enc(opts.month));
      return A().apiJSON("/api/coach/financials/transactions" + (q.length ? ("?" + q.join("&")) : ""));
    },
    // GET /api/coach/bookings/:id -> {booking:{id,booking_type,status,starts_at,ends_at,
    //   duration_minutes,is_future,court_name,client:{name,email,phone,user_id},venue:{club_name,address},
    //   players:[{name,kind,attended}],charge:{amount_minor,currency,status,settlement_mode,order_id,...},
    //   ics_url,can:{accept,propose,decline,reschedule,cancel,mark_completed,mark_no_show,add_to_calendar}}}
    //   the coach EVENT STORY (drill-through on any lesson/class they run).
    bookingStory: function (id) { return A().apiJSON("/api/coach/bookings/" + enc(id)); },
    // GET /api/coach/classes/:enrolment_id -> {booking:{...}} — the class sibling of bookingStory
    //   (same shape: charge fold + transactions log + can), so Widgets.TransactionDetail renders it.
    classStory: function (enrolmentId) { return A().apiJSON("/api/coach/classes/" + enc(enrolmentId)); },
    // GET /api/coach/orders/:order_id/record -> {booking:{...}} — a standalone order the coach earned
    //   (a pack they sold). Same record shape; coach-scoped, read-only (fold + log + receipt).
    orderRecord: function (orderId) { return A().apiJSON("/api/coach/orders/" + enc(orderId) + "/record"); },
    // GET /api/coach/clients/:id/invoice?month= -> {invoice:{month,currency,club_name,coach_name,
    //   client_name,client_email,lines:[{at,description,gross_minor,status,note?}],totals:{...}}}
    clientInvoice: function (userId, month) {
      return A().apiJSON("/api/coach/clients/" + enc(userId) + "/invoice" + (month ? ("?month=" + enc(month)) : ""));
    },
    // POST /api/coach/clients/:id/issue-invoice?month= -> {invoice, owed_minor, notified}
    issueInvoice: function (userId, month) {
      return A().apiJSON("/api/coach/clients/" + enc(userId) + "/issue-invoice" + (month ? ("?month=" + enc(month)) : ""),
        { method: "POST", body: {} });
    },

    // ---- business cockpit (read-only; THIS coach's own numbers only) -----
    // GET /api/coach/cockpit[?month=YYYY-MM] ->
    //   {period:'YYYY-MM',
    //    kpis:{lessons_count,hours,classes_count,gross_minor,net_minor,commission_minor,
    //          arrears_owed_minor,fill_rate_pct(0-100|null),clients_active,clients_new,no_shows},
    //    trend:[{month,net_minor,lessons}] (last ~6 months, oldest->newest),
    //    top_clients:[{user_id,name,sessions,spend_minor}],
    //    upcoming:[{when,client,type}]}
    // Earnings are NET of commission (party_type='coach' splits); with no agreement the
    // server returns net=gross & commission=0. fill_rate_pct is null when the coach has no
    // working hours set. Money in *_minor cents.
    cockpit: function (month) {
      return A().apiJSON("/api/coach/cockpit" + (month ? ("?month=" + enc(month)) : ""));
    },
    // GET /api/coach/commission -> {club_default_pct, coach_default_pct, effective_pct, currency,
    //   services:[{product_id,name,effective_pct}]} — READ-ONLY (owner sets it in admin).
    commission: function () { return A().apiJSON("/api/coach/commission"); },

    // GET /api/coach/activity -> {activity:[{at,kind,title,detail,amount_minor,currency,direction}]}
    //   the coach's transaction log (lessons earned, refund clawbacks, per-client arrears).
    activity: function (limit) {
      return A().apiJSON("/api/coach/activity" + (limit ? ("?limit=" + enc(limit)) : ""));
    },

    // ---- disputes (refund requests on THIS coach's coaching services) ----------
    // GET /api/coach/refund-requests?status= -> {requests:[{id,routed_to,coach_name,requester_name,
    //   item_description,amount_minor,order_amount_minor,currency_code,reason,status,...}]}
    refundRequests: function (status) {
      return A().apiJSON("/api/coach/refund-requests" + (status ? ("?status=" + enc(status)) : ""));
    },
    // POST /api/coach/refund-requests/:id/approve  body {note?} -> {refund_request}
    approveRefund: function (id, body) {
      return A().apiJSON("/api/coach/refund-requests/" + enc(id) + "/approve",
        { method: "POST", body: body || {} });
    },
    // POST /api/coach/refund-requests/:id/decline  body {note?} -> {refund_request}
    declineRefund: function (id, body) {
      return A().apiJSON("/api/coach/refund-requests/" + enc(id) + "/decline",
        { method: "POST", body: body || {} });
    },

    // ---- month-end statement (commission settlement; a coach sees their OWN) ----
    // GET /api/admin/coach-statement?month=YYYY-MM ->
    //   {month, currency, clients:[{client_name,lessons,paid_minor,owed_minor,net_minor}],
    //    arrears_items:[{id,client_name,client_user_id,gross_minor,currency,starts_at}],
    //    totals:{paid_minor,owed_minor,net_minor,rent_minor,balance_minor}}
    statement: function (month) {
      return A().apiJSON("/api/admin/coach-statement" + (month ? ("?month=" + enc(month)) : ""));
    },
    // POST /api/admin/coach-statement/arrears/:id/collected — mark an owed lesson collected
    // (off-platform EFT received) → accrues its commission. -> {ok}
    arrearsCollected: function (id) {
      return A().apiJSON("/api/admin/coach-statement/arrears/" + enc(id) + "/collected",
        { method: "POST", body: {} });
    },
    // PATCH /api/admin/coach-statement/arrears/:id  body: {gross_minor?} (discount) |
    //   {status:'written_off'} (waive — no commission). -> {ok}
    arrearsAdjust: function (id, body) {
      return A().apiJSON("/api/admin/coach-statement/arrears/" + enc(id),
        { method: "PATCH", body: body || {} });
    },

    // ---- pending lessons (approval queue; THIS coach as the runner) ----------
    // Thin alias over the diary list — lessons awaiting the coach (status='requested')
    // or awaiting the client (status='proposed'). Returns {bookings:[...],count}.
    // (No client name in the row — see api.js bookings(); we render a best-effort title.)
    pendingLessons: function (status) {
      return window.API.bookings({ as_coach: "1", status: status || "requested" });
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
    // PATCH /api/coach/classes/:resource_id  body: {name?,capacity?,description?,court_resource_ids?}
    //   — edit the coach's OWN class (coach forced to self) + reassign its upcoming sessions' courts.
    updateClass: function (resourceId, body) {
      return A().apiJSON("/api/coach/classes/" + enc(resourceId), { method: "PATCH", body: body || {} });
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
  function field(label, control, hint) {
    return el("div", { class: "cf-field" }, [
      el("label", { text: label }), control,
      hint ? el("div", { class: "cf-muted", style: "margin-top:4px;font-size:.8rem", text: hint }) : null,
    ].filter(Boolean));
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
  // TAG EDITOR — a generic chip editor (removable cf-chips + an "add" input). Reused
  // for specialties, languages, qualifications. Returns {el, value()} -> string[].
  // ---------------------------------------------------------------------------
  function tagEditor(initial, opts) {
    opts = opts || {};
    var tags = (initial || []).slice();
    var chips = el("div", { class: "cf-row", style: "flex-wrap:wrap;gap:6px" });
    var addI = input({ placeholder: opts.placeholder || "Add + Enter", style: "max-width:280px" });
    function draw() {
      UI.clear(chips);
      tags.forEach(function (t, idx) {
        var x = el("button", { class: "cf-chip", style: "cursor:pointer;border:0",
          text: t + "  ✕", title: "Remove",
          onclick: function () { tags.splice(idx, 1); draw(); } });
        chips.appendChild(x);
      });
      if (!tags.length) chips.appendChild(el("span", { class: "cf-muted", text: opts.empty || "None yet." }));
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
  // toggle row: a labelled checkbox returning {el, checked()}.
  function toggle(label, checked, hint) {
    var box = input({ type: "checkbox" }); box.checked = !!checked;
    var row = el("label", { class: "cf-row", style: "gap:8px;align-items:center;cursor:pointer" },
      [box, el("span", { text: label, style: "font-weight:600" })]);
    var wrap = el("div", {}, [row]);
    if (hint) wrap.appendChild(el("div", { class: "cf-muted", style: "margin-top:2px", text: hint }));
    return { el: wrap, checked: function () { return box.checked; } };
  }

  // ---------------------------------------------------------------------------
  // PROFILE — photo, name, headline, bio, specialties, languages, qualifications,
  // years of experience, bookable/visibility toggles, phone.
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
      years: input({ value: (p.years_experience == null ? "" : p.years_experience),
        placeholder: "e.g. 10", type: "number", min: "0", max: "80", style: "max-width:120px" }),
      phone: input({ value: p.phone || "", placeholder: "Cell phone", type: "tel", style: "max-width:220px" }),
    };
    f.bio.value = p.bio || "";
    var spec = tagEditor(p.specialties || [], {
      placeholder: "Add a specialty (e.g. Junior development) + Enter", empty: "No specialties yet." });
    var langs = tagEditor(p.languages || [], {
      placeholder: "Add a language (e.g. English) + Enter", empty: "No languages yet." });
    var quals = tagEditor(p.qualifications || [], {
      placeholder: "Add a qualification (e.g. LTA Level 3) + Enter", empty: "No qualifications yet." });
    // is_bookable defaults true; public_visibility defaults true (NOT NULL DEFAULT true).
    var bookable = toggle("Accepting new bookings",
      (p.is_bookable == null ? true : p.is_bookable),
      "Members can book lessons with you. Turn off when you're full.");
    var visible = toggle("Show on the public coach directory",
      (p.public_visibility == null ? true : p.public_visibility),
      "Appears on the club's public/marketing site. Independent of bookable.");
    // review_bookings defaults FALSE — lessons confirm immediately unless the coach opts in.
    var review = toggle("Review bookings before they confirm",
      !!p.review_bookings,
      "New lesson requests wait for you to accept (or propose a new time) before they're confirmed.");
    // PREFERRED COURT. Clients pick the coach, never the court — the club allocates it — which used to
    // scatter a coach's lessons across the site. This is a PREFERENCE, not a lock: the server holds
    // this court whenever it's free at the requested time and falls back to any free court otherwise,
    // so setting it can never make a lesson unbookable.
    var prefCourt = el("select", { class: "cf-input" }, [
      el("option", { value: "", text: "No preference — any free court" })]);
    window.API.resources().then(function (r) {
      ((r && r.resources) || []).filter(function (x) { return x.kind === "court" && x.is_active !== false; })
        .forEach(function (c) { prefCourt.appendChild(el("option", { value: c.id, text: c.name })); });
      if (p.preferred_court_resource_id) prefCourt.value = String(p.preferred_court_resource_id);
    }, function () { /* courts unavailable → "no preference" still saves fine */ });

    var card = el("div", { class: "cf-card" }, [
      el("h2", { text: "Your coaching profile" }),
      field("Profile photo", el("div", {}, [previewWrap, el("div", { class: "cf-row", style: "margin-top:8px" }, [fileI])])),
      urlField,
      el("div", { class: "cf-grid cf-grid-2" }, [field("First name", f.first), field("Surname", f.surname)]),
      field("Display name", f.display),
      field("Headline", f.headline),
      field("Bio", f.bio),
      field("Specialties", spec.el),
      field("Languages", langs.el),
      field("Qualifications", quals.el),
      field("Years of experience", f.years),
      field("Cell phone", f.phone),
      field("Preferred court", prefCourt,
            "Your lessons are held on this court whenever it's free — otherwise the next free one."),
      el("div", { class: "cf-grid cf-grid-2", style: "margin-top:4px" }, [bookable.el, visible.el]),
      el("div", { style: "margin-top:10px" }, [review.el]),
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
          languages: langs.value(),
          qualifications: quals.value(),
          years_experience: num(f.years.value),
          is_bookable: bookable.checked(),
          public_visibility: visible.checked(),
          review_bookings: review.checked(),
          // Always sent (even empty) so a coach can clear the preference — the repo treats a
          // present-but-empty value as "no preference" rather than "leave unchanged".
          preferred_court_resource_id: prefCourt.value || null,
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
  // SERVICES & RATES — repeatable rows of {lesson name, duration, price}. Each rate
  // is a PER-DURATION price (unit='per_booking') the booking flow resolves directly
  // (diary/pricing.py). No audience is sent — the server defaults to 'any' so the
  // rate prices for every booker.
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
    card.appendChild(el("p", { class: "cf-muted", text: "Add the lessons you offer. Each lesson can have several lengths (e.g. 30 min = R250, 60 min = R400) — use “Add another duration” to add more. Each price is for the whole lesson." }));
    var listBox = el("div", { id: "co-services" });
    card.appendChild(listBox);
    host.appendChild(card);

    function reload() {
      UI.clear(listBox);
      listBox.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
      window.CoachAPI.services().then(function (r) { renderList(r.services || []); })
        .catch(function (e) { UI.clear(listBox); listBox.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    // One editable rate row (a single billing.price). save -> patch; del -> delete.
    function rateRow(s) {
      var durI = select(s.duration_minutes || 60, DURATIONS);
      var amtI = input({ value: fromMinor(s.amount_minor), placeholder: "0.00", style: "max-width:110px" });
      var save = el("button", { class: "cf-btn cf-btn-sm", text: "Save" });
      var del = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Remove" });
      save.addEventListener("click", async function () {
        save.disabled = true;
        try {
          await window.CoachAPI.patchService(s.price_id, {
            name: s.name, duration_minutes: num(durI.value) || 60, amount_minor: toMinor(amtI.value),
          });
          UI.toast("Rate updated.", "info");
        } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { save.disabled = false; }
      });
      del.addEventListener("click", async function () {
        if (!window.confirm("Remove this rate?")) return;
        try { await window.CoachAPI.deleteService(s.price_id); UI.toast("Rate removed.", "info"); reload(); }
        catch (e) { UI.toast(UI.errMsg(e), "error"); }
      });
      return el("div", { class: "cf-item", style: "flex-wrap:wrap;gap:6px" }, [
        durI, el("span", { class: "cf-muted", text: "R" }), amtI,
        el("span", { class: "cf-spacer" }), save, del,
      ]);
    }

    // Group flattened rates by product_id so each lesson PRODUCT shows all its durations
    // with one "Add another duration" affordance (POST /services/:product_id/rate).
    function renderList(list) {
      UI.clear(listBox);
      if (!list.length) { listBox.appendChild(el("div", { class: "cf-empty", text: "No services yet. Add one below." })); return; }
      var groups = {}; var order = [];
      list.forEach(function (s) {
        var pid = s.product_id || s.price_id;
        if (!groups[pid]) { groups[pid] = { name: s.name, rates: [] }; order.push(pid); }
        groups[pid].rates.push(s);
      });
      order.forEach(function (pid) {
        var g = groups[pid];
        var box = el("div", { class: "cf-card", style: "margin-bottom:10px" });
        box.appendChild(el("div", { class: "cf-row", style: "align-items:center;gap:8px" }, [
          el("span", { class: "cf-chip lesson", text: "lesson" }),
          el("strong", { text: g.name || "Lesson" }),
          el("span", { class: "cf-muted", text: g.rates.length + (g.rates.length === 1 ? " duration" : " durations") }),
        ]));
        var rates = el("div", { class: "cf-list", style: "margin-top:8px" });
        g.rates.forEach(function (s) { rates.appendChild(rateRow(s)); });
        box.appendChild(rates);
        // "Add another duration" — inline mini-form bound to this product.
        var aDur = select(90, DURATIONS);
        var aAmt = input({ placeholder: "0.00", style: "max-width:110px" });
        var aBtn = el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "+ Add another duration" });
        aBtn.addEventListener("click", async function () {
          aBtn.disabled = true;
          try {
            await window.CoachAPI.addServiceRate(pid, {
              duration_minutes: num(aDur.value) || 60, amount_minor: toMinor(aAmt.value),
            });
            UI.toast("Duration added.", "info"); reload();
          } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { aBtn.disabled = false; }
        });
        box.appendChild(el("div", { class: "cf-row", style: "gap:6px;align-items:center;margin-top:10px;flex-wrap:wrap" },
          [aDur, el("span", { class: "cf-muted", text: "R" }), aAmt, aBtn]));
        listBox.appendChild(box);
      });
    }

    // add-service form (creates a NEW lesson product + its first rate)
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
          amount_minor: toMinor(addAmt.value),
        });
        addName.value = ""; addAmt.value = ""; UI.toast("Service added.", "info"); reload();
      } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { addBtn.disabled = false; }
    });
    card.appendChild(el("h3", { text: "Add a new lesson", style: "margin-top:14px" }));
    card.appendChild(el("div", { class: "cf-row", style: "gap:6px;flex-wrap:wrap;align-items:center" },
      [addName, addDur, el("span", { class: "cf-muted", text: "R" }), addAmt, addBtn]));
    if (opts.before && opts.before.length) card.appendChild(actionRow(opts.before));

    reload();
    return { reload: reload };
  }

  // LESSON PACKS — a coach's own prepaid bundles (unit model: a pack covers any lesson length;
  // longer lessons use proportionally more). Scoped to /api/coach/bundle-plans (lesson + this coach).
  // (CoachUI.packs REMOVED 2026-07-09 — a coach's packs live under each of their
  //  lessons/classes in the service editor, not a standalone packs surface.)

  window.CoachUI = {
    profile: profile, hours: hours, services: services,
  };
})();
