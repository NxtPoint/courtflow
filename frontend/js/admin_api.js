// admin_api.js — thin typed wrappers over the live admin/onboarding APIs.
//
// Mirrors api.js: every wrapper maps 1:1 to a route in the admin lane. club_id is
// NEVER sent in the body — the server derives it from the Clerk JWT principal.
// All calls go through TFAuth.apiJSON (Bearer header; throws {status, body} on non-2xx).
//
// Exposes window.AdminAPI. Used by onboarding.js + settings.js. Does NOT touch api.js.
(function () {
  function A() {
    if (!window.TFAuth) throw new Error("auth_client.js must load before admin_api.js");
    return window.TFAuth;
  }
  function qs(params) {
    var p = new URLSearchParams();
    Object.keys(params || {}).forEach(function (k) {
      var v = params[k];
      if (v !== undefined && v !== null && v !== "") p.set(k, v);
    });
    var s = p.toString();
    return s ? ("?" + s) : "";
  }
  function enc(id) { return encodeURIComponent(id); }

  var AdminAPI = {
    // ---- onboarding ------------------------------------------------------
    // GET /api/admin/onboarding ->
    //   {completed, steps:{profile,hours,courts,services,coaches},
    //    club, location, branding, policy, counts:{courts,products,coaches}}
    onboarding: function () { return A().apiJSON("/api/admin/onboarding"); },
    // POST /api/admin/onboarding/complete -> {ok:true}
    completeOnboarding: function () {
      return A().apiJSON("/api/admin/onboarding/complete", { method: "POST", body: {} });
    },

    // ---- club profile ----------------------------------------------------
    // GET /api/admin/club -> {club:{...}}
    club: function () { return A().apiJSON("/api/admin/club"); },
    // PATCH /api/admin/club  body: {name,legal_name,currency_code,timezone,locale}
    patchClub: function (body) {
      return A().apiJSON("/api/admin/club", { method: "PATCH", body: body });
    },

    // ---- location (NAP) --------------------------------------------------
    // PUT /api/admin/location
    //   body: {name,address_line,city,postal_code,country,phone,email,lat,lng}
    putLocation: function (body) {
      return A().apiJSON("/api/admin/location", { method: "PUT", body: body });
    },

    // ---- branding --------------------------------------------------------
    // PATCH /api/admin/branding
    //   body: {primary_color,accent_color,logo_url,favicon_url,og_image_url}
    patchBranding: function (body) {
      return A().apiJSON("/api/admin/branding", { method: "PATCH", body: body });
    },

    // ---- policy ----------------------------------------------------------
    // PATCH /api/admin/policy  body: {booking_window_days,min_booking_minutes,
    //   cancellation_cutoff_hours,guest_requires_member,allow_pay_at_court,
    //   allow_monthly_account,allow_online_payment}
    patchPolicy: function (body) {
      return A().apiJSON("/api/admin/policy", { method: "PATCH", body: body });
    },

    // ---- resources (courts) ---------------------------------------------
    // GET /api/admin/resources -> {resources:[{id,kind,name,surface,capacity,...}]}
    resources: function () { return A().apiJSON("/api/admin/resources"); },
    // POST /api/admin/resources  body: {kind:'court',name,surface,capacity}
    createResource: function (body) {
      return A().apiJSON("/api/admin/resources", { method: "POST", body: body });
    },
    // PATCH /api/admin/resources/:id  body: {name,surface,capacity,is_active,...}
    patchResource: function (id, body) {
      return A().apiJSON("/api/admin/resources/" + enc(id), { method: "PATCH", body: body });
    },
    // DELETE /api/admin/resources/:id
    deleteResource: function (id) {
      return A().apiJSON("/api/admin/resources/" + enc(id), { method: "DELETE" });
    },

    // ---- opening hours ---------------------------------------------------
    // GET /api/admin/hours?resource_id= -> {week:[{weekday,open,start_time,end_time,slot_minutes}]}
    hours: function (opts) { return A().apiJSON("/api/admin/hours" + qs(opts)); },
    // PUT /api/admin/hours  body: {scope:'all_courts',week:[{weekday(0-6),open,
    //   start_time"HH:MM",end_time"HH:MM",slot_minutes}]}
    putHours: function (body) {
      return A().apiJSON("/api/admin/hours", { method: "PUT", body: body });
    },

    // ---- products & prices ----------------------------------------------
    // GET /api/admin/products -> {products:[{id,kind,name,description,prices:[...]}]}
    products: function () { return A().apiJSON("/api/admin/products"); },
    // POST /api/admin/products  body: {kind,name,description,
    //   prices:[{audience,amount_minor,unit,duration_minutes}]}
    createProduct: function (body) {
      return A().apiJSON("/api/admin/products", { method: "POST", body: body });
    },
    // POST /api/admin/prices  body: {product_id,audience,amount_minor,unit,duration_minutes}
    createPrice: function (body) {
      return A().apiJSON("/api/admin/prices", { method: "POST", body: body });
    },
    // PATCH /api/admin/prices/:id  body: {amount_minor,unit,duration_minutes,is_active}
    patchPrice: function (id, body) {
      return A().apiJSON("/api/admin/prices/" + enc(id), { method: "PATCH", body: body });
    },

    // ---- membership term plans (label + amount + duration) ---------------
    // GET /api/admin/membership-plans -> {plans:[{price_id,label,amount_minor,term_months,active}]}
    membershipPlans: function () { return A().apiJSON("/api/admin/membership-plans"); },
    // POST /api/admin/membership-plans  body: {label,amount_minor,term_months} -> {plan}
    createMembershipPlan: function (body) {
      return A().apiJSON("/api/admin/membership-plans", { method: "POST", body: body });
    },
    // PATCH /api/admin/membership-plans/:price_id  body: {label?,amount_minor?,term_months?,active?}
    patchMembershipPlan: function (id, body) {
      return A().apiJSON("/api/admin/membership-plans/" + enc(id), { method: "PATCH", body: body });
    },
    // DELETE /api/admin/membership-plans/:price_id  (deactivate)
    deleteMembershipPlan: function (id) {
      return A().apiJSON("/api/admin/membership-plans/" + enc(id), { method: "DELETE" });
    },

    // ---- session-pack (token bundle) plans (docs/specs/02) ---------------
    // GET /api/admin/bundle-plans -> {plans:[{id,service_kind,coach_user_id,label,sessions_count,
    //                                         duration_minutes,price_minor,validity_days,active}]}
    bundlePlans: function () { return A().apiJSON("/api/admin/bundle-plans"); },
    // POST body: {service_kind,sessions_count,price_minor,label?,duration_minutes?,coach_user_id?,validity_days?}
    createBundlePlan: function (body) {
      return A().apiJSON("/api/admin/bundle-plans", { method: "POST", body: body });
    },
    // PATCH /api/admin/bundle-plans/:id  body: any of the above + clear_coach/clear_duration/clear_validity/active
    patchBundlePlan: function (id, body) {
      return A().apiJSON("/api/admin/bundle-plans/" + enc(id), { method: "PATCH", body: body });
    },
    // DELETE /api/admin/bundle-plans/:id  (deactivate)
    deleteBundlePlan: function (id) {
      return A().apiJSON("/api/admin/bundle-plans/" + enc(id), { method: "DELETE" });
    },

    // ---- coaches ---------------------------------------------------------
    // GET /api/admin/coaches -> {coaches:[{id,email,display_name,status,...}]}
    coaches: function () { return A().apiJSON("/api/admin/coaches"); },
    // POST /api/admin/coaches/invite  body: {email,phone,first_name,surname,display_name}
    //   -> {coach, invite_link}
    inviteCoach: function (body) {
      return A().apiJSON("/api/admin/coaches/invite", { method: "POST", body: body });
    },
    // POST /api/admin/coaches/:id/resend-invite -> {invite_link}
    resendCoachInvite: function (id) { return A().apiJSON("/api/admin/coaches/" + enc(id) + "/resend-invite", { method: "POST" }); },
    // DELETE /api/admin/coaches/:id  (remove a coach from the club)
    removeCoach: function (id) { return A().apiJSON("/api/admin/coaches/" + enc(id), { method: "DELETE" }); },
    // PATCH /api/admin/products/:id  body: {name?,description?,active?}
    patchProduct: function (id, body) { return A().apiJSON("/api/admin/products/" + enc(id), { method: "PATCH", body: body }); },
    // DELETE /api/admin/prices/:id  (delete/deactivate a price)
    deletePrice: function (id) { return A().apiJSON("/api/admin/prices/" + enc(id), { method: "DELETE" }); },

    // ---- classes (management) -------------------------------------------
    // GET /api/admin/classes -> {classes:[{resource_id,name,coach_user_id,coach_name,
    //   capacity,price_amount_minor,duration_minutes,upcoming_sessions}]}
    classes: function () { return A().apiJSON("/api/admin/classes"); },
    // POST /api/admin/classes  body: {name,coach_user_id?,capacity,price_amount_minor,
    //   duration_minutes,description?} -> {resource_id,...}
    createClass: function (body) {
      return A().apiJSON("/api/admin/classes", { method: "POST", body: body });
    },
    // POST /api/admin/classes/:resource_id/schedule
    //   recurring: {weekdays:[0-6],start_time,duration_minutes?,date_from,date_until,capacity?}
    //   one-off:   {dates:[...],start_time,duration_minutes?,capacity?}
    //   -> {created, skipped}
    scheduleClass: function (resourceId, body) {
      return A().apiJSON("/api/admin/classes/" + enc(resourceId) + "/schedule",
        { method: "POST", body: body });
    },
    // GET /api/admin/classes/:resource_id/sessions?date_from=&date_to=
    //   -> {sessions:[{session_id,starts_at,ends_at,capacity,enrolled,waitlisted,spots_left,status}]}
    classSessions: function (resourceId, opts) {
      return A().apiJSON("/api/admin/classes/" + enc(resourceId) + "/sessions" + qs(opts));
    },
    // POST /api/admin/classes/sessions/:session_id/cancel
    cancelClassSession: function (sessionId, body) {
      return A().apiJSON("/api/admin/classes/sessions/" + enc(sessionId) + "/cancel",
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

    // ---- commission engine: coach agreements + rules (owner config) ------
    // GET /api/admin/coach-agreements ->
    //   {club_default_pct, currency, coaches:[{coach_user_id,name,rent_minor,rent_day,
    //     coach_pct, lesson_types:[{product_id,name,club_pct,coach_pct,effective_pct}]}], rules}
    coachAgreements: function () { return A().apiJSON("/api/admin/coach-agreements"); },
    // PUT /api/admin/coach-agreements/:coach_user_id  body:{rent_minor?,rent_day?,status?,notes?}
    putCoachAgreement: function (id, body) {
      return A().apiJSON("/api/admin/coach-agreements/" + enc(id), { method: "PUT", body: body });
    },
    // GET /api/admin/commission-rules -> {rules:[...]}
    commissionRules: function () { return A().apiJSON("/api/admin/commission-rules"); },
    // POST /api/admin/commission-rules  body:{product_id?,coach_user_id?,commission_pct} -> {rule}
    //   scope derived from which of product_id/coach_user_id are sent.
    setCommissionRule: function (body) {
      return A().apiJSON("/api/admin/commission-rules", { method: "POST", body: body });
    },
    // DELETE /api/admin/commission-rules/:rule_id
    deleteCommissionRule: function (id) {
      return A().apiJSON("/api/admin/commission-rules/" + enc(id), { method: "DELETE" });
    },
    // GET /api/admin/commission-rules/preview?coach_user_id=&product_id= -> {effective_pct}
    commissionPreview: function (opts) {
      return A().apiJSON("/api/admin/commission-rules/preview" + qs(opts));
    },

    // ---- owner cockpit / financials (commission engine reporting) --------
    // Under /api/admin/financials/* (the CRM lane owns /api/admin/cockpit/* — no clash).
    cockpitSummary: function (opts) { return A().apiJSON("/api/admin/financials/summary" + qs(opts)); },
    cockpitRevenue: function (opts) { return A().apiJSON("/api/admin/financials/revenue" + qs(opts)); },
    cockpitCoachEarnings: function (opts) {
      return A().apiJSON("/api/admin/financials/coach-earnings" + qs(opts));
    },
    cockpitMemberships: function () { return A().apiJSON("/api/admin/financials/memberships"); },

    // ---- online payments + client refund requests (Billing tab) ----------
    // GET /api/admin/payments -> {payments:[{order_id,payer_email,amount_minor,currency_code,
    //                                        created_at,refunded}]}
    payments: function () { return A().apiJSON("/api/admin/payments"); },
    // GET /api/admin/refund-requests?status= -> {requests:[{id,order_id,user_id,amount_minor,
    //   reason,status,decided_by,decided_at,note,created_at,order_amount_minor,currency_code,
    //   order_status,requester_email,requester_name}]}
    refundRequests: function (opts) { return A().apiJSON("/api/admin/refund-requests" + qs(opts)); },
    // POST /api/admin/refund-requests/:id/approve  body:{amount_minor?,cancel_booking?,note?}
    //   -> {refund_request, cancelled}. 409 if already decided; 502/503 if the gateway refund failed.
    approveRefundRequest: function (id, body) {
      return A().apiJSON("/api/admin/refund-requests/" + enc(id) + "/approve",
        { method: "POST", body: body || {} });
    },
    // POST /api/admin/refund-requests/:id/decline  body:{note?} -> {refund_request}
    declineRefundRequest: function (id, body) {
      return A().apiJSON("/api/admin/refund-requests/" + enc(id) + "/decline",
        { method: "POST", body: body || {} });
    },
  };

  window.AdminAPI = AdminAPI;
})();

// AdminUI — shared section components reused by BOTH the onboarding wizard
// (onboarding.js) and the Settings tabs (settings.js). Each builder renders one
// editable section into a host element and wires its own Save → AdminAPI call.
// Pure presentation + the API calls above; depends on window.UI + window.AdminAPI.
(function () {
  var UI, el;
  function init() { if (!UI) { UI = window.UI; el = UI.el; } }

  var WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  var SURFACES = ["hard", "clay", "grass", "artificial"];
  var AUDIENCES = ["member", "visitor", "guest"];
  var UNITS = ["per_hour", "per_booking"];
  var PRODUCT_KINDS = ["court_hire", "lesson", "class", "membership"];

  // ---- small form helpers ----------------------------------------------------
  function field(label, control) {
    return el("div", { class: "cf-field" }, [el("label", { text: label }), control]);
  }
  function input(opts) { return el("input", Object.assign({ class: "cf-input" }, opts || {})); }
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
  // CLUB PROFILE — name, address, NAP. -> PUT /location + PATCH /club.
  // data: {club:{...}, location:{...}}. onSaved(optional) fires after success.
  // ---------------------------------------------------------------------------
  function clubProfile(host, data, opts) {
    init(); opts = opts || {};
    var club = (data && data.club) || {};
    var loc = (data && data.location) || {};
    var f = {
      name: input({ value: club.name || "", placeholder: "Club name" }),
      city: input({ value: loc.city || "", placeholder: "City" }),
      address: input({ value: loc.address_line || "", placeholder: "Street address" }),
      postal: input({ value: loc.postal_code || "", placeholder: "Postal code" }),
      country: input({ value: loc.country || "South Africa", placeholder: "Country" }),
      phone: input({ value: loc.phone || "", placeholder: "Club phone / cell", type: "tel" }),
      email: input({ value: loc.email || "", placeholder: "Club email", type: "email" }),
    };
    var card = el("div", { class: "cf-card" }, [
      el("h2", { text: "Club profile" }),
      field("Club name", f.name),
      field("Street address", f.address),
      el("div", { class: "cf-grid cf-grid-2" }, [field("City", f.city), field("Postal code", f.postal)]),
      el("div", { class: "cf-grid cf-grid-2" }, [field("Country", f.country), field("Club phone", f.phone)]),
      field("Club email", f.email),
    ]);
    var btn = el("button", { class: "cf-btn cf-btn-primary", text: opts.saveLabel || "Save" });
    card.appendChild(actionRow((opts.before || []).concat([btn])));
    btn.addEventListener("click", async function () {
      var name = f.name.value.trim();
      if (!name) { UI.toast("Club name is required.", "warn"); return; }
      btn.disabled = true; btn.textContent = "Saving…";
      try {
        await window.AdminAPI.putLocation({
          name: name, address_line: f.address.value.trim(), city: f.city.value.trim(),
          postal_code: f.postal.value.trim(), country: f.country.value.trim(),
          phone: f.phone.value.trim(), email: f.email.value.trim(),
        });
        await window.AdminAPI.patchClub({ name: name });
        UI.toast("Club profile saved.", "info");
        if (typeof opts.onSaved === "function") opts.onSaved();
      } catch (e) {
        UI.toast(UI.errMsg(e), "error");
      } finally { btn.disabled = false; btn.textContent = opts.saveLabel || "Save"; }
    });
    UI.clear(host); host.appendChild(card);
  }

  // ---------------------------------------------------------------------------
  // OPENING HOURS — Mon–Sun grid, applied to all courts. -> PUT /hours.
  // data: {week:[{weekday,open,start_time,end_time,slot_minutes}]} (any source).
  // ---------------------------------------------------------------------------
  function hours(host, data, opts) {
    init(); opts = opts || {};
    var existing = {};
    ((data && data.week) || []).forEach(function (w) { existing[w.weekday] = w; });
    var rows = [];
    var card = el("div", { class: "cf-card" }, [
      el("h2", { text: "Opening hours" }),
      el("p", { class: "cf-muted", text: "Set the week; these apply to all courts." }),
    ]);
    var grid = el("div", { class: "cf-list" });
    WEEKDAYS.forEach(function (lbl, wd) {
      var w = existing[wd] || { open: wd < 6, start_time: "07:00", end_time: "21:00", slot_minutes: 60 };
      var openTgl = input({ type: "checkbox" }); openTgl.checked = !!w.open;
      var start = input({ type: "time", value: w.start_time || "07:00", style: "max-width:130px" });
      var end = input({ type: "time", value: w.end_time || "21:00", style: "max-width:130px" });
      var slot = select(w.slot_minutes || 60, [
        { value: 30, label: "30 min" }, { value: 60, label: "60 min" },
        { value: 90, label: "90 min" }, { value: 120, label: "120 min" },
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
          start_time: r.start.value || "07:00", end_time: r.end.value || "21:00",
          slot_minutes: num(r.slot.value) || 60,
        };
      });
      btn.disabled = true; btn.textContent = "Saving…";
      try {
        await window.AdminAPI.putHours({ scope: "all_courts", week: week });
        UI.toast("Opening hours saved.", "info");
        if (typeof opts.onSaved === "function") opts.onSaved();
      } catch (e) { UI.toast(UI.errMsg(e), "error"); }
      finally { btn.disabled = false; btn.textContent = opts.saveLabel || "Save hours"; }
    });
    UI.clear(host); host.appendChild(card);
  }

  // ---------------------------------------------------------------------------
  // COURTS — list existing + add/rename/delete. -> POST/PATCH/DELETE /resources.
  // ---------------------------------------------------------------------------
  function courts(host, opts) {
    init(); opts = opts || {};
    UI.clear(host);
    var card = el("div", { class: "cf-card" });
    card.appendChild(el("h2", { text: "Courts" }));
    var listBox = el("div", { class: "cf-list", id: "ad-courts" });
    card.appendChild(listBox);
    host.appendChild(card);

    function reload() {
      UI.clear(listBox);
      listBox.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
      window.AdminAPI.resources().then(function (r) {
        var courts = (r.resources || []).filter(function (x) { return x.kind === "court"; });
        renderList(courts);
      }).catch(function (e) { UI.clear(listBox); listBox.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    function renderList(courts) {
      UI.clear(listBox);
      if (!courts.length) listBox.appendChild(el("div", { class: "cf-empty", text: "No courts yet. Add your first below." }));
      courts.forEach(function (c) {
        var nameI = input({ value: c.name || "", style: "max-width:180px" });
        var surfI = select(c.surface || "hard", SURFACES);
        var save = el("button", { class: "cf-btn cf-btn-sm", text: "Save" });
        var del = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Delete" });
        save.addEventListener("click", async function () {
          save.disabled = true;
          try {
            await window.AdminAPI.patchResource(c.id, { name: nameI.value.trim(), surface: surfI.value });
            UI.toast("Court updated.", "info");
          } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { save.disabled = false; }
        });
        del.addEventListener("click", async function () {
          if (!window.confirm("Delete " + (c.name || "this court") + "?")) return;
          try { await window.AdminAPI.deleteResource(c.id); UI.toast("Court deleted.", "info"); reload(); }
          catch (e) { UI.toast(UI.errMsg(e), "error"); }
        });
        listBox.appendChild(el("div", { class: "cf-item", style: "flex-wrap:wrap" }, [
          nameI, surfI, el("span", { class: "cf-spacer" }), save, del,
        ]));
      });
    }

    // add-court form
    var addName = input({ placeholder: "Court name (e.g. Court 1)", style: "max-width:200px" });
    var addSurf = select("hard", SURFACES);
    var addBtn = el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Add court" });
    addBtn.addEventListener("click", async function () {
      var nm = addName.value.trim();
      if (!nm) { UI.toast("Enter a court name.", "warn"); return; }
      addBtn.disabled = true;
      try {
        await window.AdminAPI.createResource({ kind: "court", name: nm, surface: addSurf.value, capacity: 4 });
        addName.value = ""; UI.toast("Court added.", "info"); reload();
      } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { addBtn.disabled = false; }
    });
    card.appendChild(el("h3", { text: "Add a court", style: "margin-top:14px" }));
    card.appendChild(el("div", { class: "cf-row" }, [addName, addSurf, addBtn]));
    if (opts.before && opts.before.length) card.appendChild(actionRow(opts.before));

    reload();
    return { reload: reload };
  }

  // ---------------------------------------------------------------------------
  // SERVICES & RATES — products + per-audience prices. -> POST /products, /prices.
  // ---------------------------------------------------------------------------
  function services(host, opts) {
    init(); opts = opts || {};
    UI.clear(host);
    var card = el("div", { class: "cf-card" });
    card.appendChild(el("h2", { text: "Services & rates" }));
    card.appendChild(el("p", { class: "cf-muted", text: "Set court-hire rates per audience and add lessons, classes or memberships." }));
    var listBox = el("div", { class: "cf-list", id: "ad-products" });
    card.appendChild(listBox);
    host.appendChild(card);

    function reload() {
      UI.clear(listBox);
      listBox.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
      window.AdminAPI.products().then(function (r) { renderList(r.products || []); })
        .catch(function (e) { UI.clear(listBox); listBox.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    function priceRow(price) {
      var amt = input({ value: fromMinor(price.amount_minor), placeholder: "0.00", style: "max-width:110px" });
      var aud = select(price.audience || "member", AUDIENCES);
      var unit = select(price.unit || "per_hour", UNITS);
      var save = el("button", { class: "cf-btn cf-btn-sm", text: "Save" });
      save.addEventListener("click", async function () {
        save.disabled = true;
        try {
          await window.AdminAPI.patchPrice(price.id, { amount_minor: toMinor(amt.value), unit: unit.value });
          UI.toast("Price updated.", "info");
        } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { save.disabled = false; }
      });
      return el("div", { class: "cf-row", style: "gap:6px" }, [aud, amt, unit, save]);
    }

    function renderList(products) {
      UI.clear(listBox);
      if (!products.length) listBox.appendChild(el("div", { class: "cf-empty", text: "No services yet. Add one below." }));
      products.forEach(function (p) {
        var box = el("div", { class: "cf-item", style: "flex-direction:column;align-items:stretch;gap:8px" });
        box.appendChild(el("div", { class: "cf-row" }, [
          el("span", { class: "cf-chip", text: p.kind || "service" }),
          el("div", { class: "cf-item-t", text: p.name || "Service" }),
        ]));
        (p.prices || []).forEach(function (pr) { box.appendChild(priceRow(pr)); });
        // add-price to an existing product
        var newAud = select("member", AUDIENCES);
        var newAmt = input({ placeholder: "0.00", style: "max-width:110px" });
        var newUnit = select("per_hour", UNITS);
        var addPrice = el("button", { class: "cf-btn cf-btn-sm", text: "Add price" });
        addPrice.addEventListener("click", async function () {
          addPrice.disabled = true;
          try {
            await window.AdminAPI.createPrice({ product_id: p.id, audience: newAud.value,
              amount_minor: toMinor(newAmt.value), unit: newUnit.value });
            UI.toast("Price added.", "info"); reload();
          } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { addPrice.disabled = false; }
        });
        box.appendChild(el("div", { class: "cf-row", style: "gap:6px;border-top:1px dashed var(--border);padding-top:8px" },
          [newAud, newAmt, newUnit, addPrice]));
        listBox.appendChild(box);
      });
    }

    // add-product form
    var pKind = select("court_hire", PRODUCT_KINDS.map(function (k) { return { value: k, label: k.replace("_", " ") }; }));
    var pName = input({ placeholder: "Name (e.g. Court hire, Private lesson)", style: "max-width:240px" });
    var pAud = select("member", AUDIENCES);
    var pAmt = input({ placeholder: "0.00", style: "max-width:110px" });
    var pUnit = select("per_hour", UNITS);
    var addBtn = el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Add service" });
    addBtn.addEventListener("click", async function () {
      var nm = pName.value.trim();
      if (!nm) { UI.toast("Enter a service name.", "warn"); return; }
      var amount = toMinor(pAmt.value);
      addBtn.disabled = true;
      try {
        var prices = (amount != null) ? [{ audience: pAud.value, amount_minor: amount, unit: pUnit.value }] : [];
        await window.AdminAPI.createProduct({ kind: pKind.value, name: nm, description: "", prices: prices });
        pName.value = ""; pAmt.value = ""; UI.toast("Service added.", "info"); reload();
      } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { addBtn.disabled = false; }
    });
    card.appendChild(el("h3", { text: "Add a service", style: "margin-top:14px" }));
    card.appendChild(el("div", { class: "cf-row" }, [pKind, pName]));
    card.appendChild(el("div", { class: "cf-row", style: "margin-top:6px" }, [pAud, pAmt, pUnit, addBtn]));
    if (opts.before && opts.before.length) card.appendChild(actionRow(opts.before));

    reload();
    return { reload: reload };
  }

  // ---------------------------------------------------------------------------
  // COACHES — list + repeatable invite rows. -> POST /coaches/invite (each).
  // ---------------------------------------------------------------------------
  function coaches(host, opts) {
    init(); opts = opts || {};
    UI.clear(host);
    var card = el("div", { class: "cf-card" });
    card.appendChild(el("h2", { text: "Coaches" }));
    var listBox = el("div", { class: "cf-list", id: "ad-coaches" });
    card.appendChild(listBox);
    host.appendChild(card);

    function reload() {
      UI.clear(listBox);
      listBox.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
      window.AdminAPI.coaches().then(function (r) {
        var list = r.coaches || [];
        UI.clear(listBox);
        if (!list.length) { listBox.appendChild(el("div", { class: "cf-empty", text: "No coaches yet. Invite one below." })); return; }
        list.forEach(function (c) {
          var cid = c.user_id || c.id;
          var pending = (c.status || "").toLowerCase() !== "active";
          var actions = [];
          if (pending && cid) {
            actions.push(el("button", { class: "cf-btn cf-btn-sm", text: "Resend invite", onclick: async function () {
              try { var r = await window.AdminAPI.resendCoachInvite(cid);
                UI.toast(r && r.invite_link ? "Invite re-issued — link copied below." : "Invite re-sent.", "info");
                if (r && r.invite_link) { try { await navigator.clipboard.writeText(r.invite_link); } catch (e) {} }
                reload();
              } catch (e) { UI.toast(UI.errMsg(e), "error"); }
            } }));
          }
          if (cid) {
            actions.push(el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Remove", onclick: async function () {
              if (!window.confirm("Remove " + (c.display_name || c.email || "this coach") + " from the club?")) return;
              try { await window.AdminAPI.removeCoach(cid); UI.toast("Coach removed.", "info"); reload(); }
              catch (e) { UI.toast(UI.errMsg(e), "error"); }
            } }));
          }
          listBox.appendChild(el("div", { class: "cf-item" }, [
            el("span", { class: "cf-chip coach", text: "coach" }),
            el("div", { class: "cf-item-main" }, [
              el("div", { class: "cf-item-t", text: c.display_name || c.email || "Coach" }),
              el("div", { class: "cf-item-s", text: (c.email || "") + (c.status ? " · " + c.status : "") }),
            ]),
            el("span", { class: "cf-spacer" }),
          ].concat(actions)));
        });
      }).catch(function (e) { UI.clear(listBox); listBox.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    // repeatable invite rows
    var rowsHost = el("div", { class: "cf-list" });
    function addRow() {
      var first = input({ placeholder: "First name", style: "max-width:140px" });
      var surname = input({ placeholder: "Surname", style: "max-width:140px" });
      var email = input({ placeholder: "Email", type: "email", style: "max-width:200px" });
      var phone = input({ placeholder: "Phone", type: "tel", style: "max-width:150px" });
      var invite = el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Invite" });
      var row = el("div", { class: "cf-item", style: "flex-wrap:wrap;gap:6px" }, [first, surname, email, phone, invite]);
      invite.addEventListener("click", async function () {
        var em = email.value.trim();
        if (!em) { UI.toast("Email is required to invite a coach.", "warn"); return; }
        invite.disabled = true; invite.textContent = "Inviting…";
        var display = (first.value.trim() + " " + surname.value.trim()).trim();
        try {
          var res = await window.AdminAPI.inviteCoach({
            email: em, phone: phone.value.trim(),
            first_name: first.value.trim(), surname: surname.value.trim(),
            display_name: display || em,
          });
          UI.toast("Invite sent to " + em + ".", "info");
          UI.clear(row);
          row.appendChild(el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: "✓ Invited " + (display || em) }),
            (res && res.invite_link)
              ? el("a", { class: "cf-item-s", href: res.invite_link, target: "_blank", text: "Copy invite link" })
              : el("div", { class: "cf-item-s", text: em }),
          ]));
          reload();
        } catch (e) { invite.disabled = false; invite.textContent = "Invite"; UI.toast(UI.errMsg(e), "error"); }
      });
      rowsHost.appendChild(row);
    }
    addRow();
    var addAnother = el("button", { class: "cf-btn cf-btn-sm", text: "+ Add another", onclick: addRow });
    card.appendChild(el("h3", { text: "Invite coaches", style: "margin-top:14px" }));
    card.appendChild(rowsHost);
    card.appendChild(el("div", { class: "cf-row", style: "margin-top:8px" }, [addAnother]));
    if (opts.before && opts.before.length) card.appendChild(actionRow(opts.before));

    reload();
    return { reload: reload };
  }

  // minutes-from-midnight <-> "HH:MM" for the membership access-window editor.
  function minToTime(m) {
    if (m == null || m === "") return "";
    var h = Math.floor(m / 60), mm = m % 60;
    return ("0" + h).slice(-2) + ":" + ("0" + mm).slice(-2);
  }
  function timeToMin(s) {
    if (!s) return null;
    var p = String(s).split(":");
    return (parseInt(p[0], 10) || 0) * 60 + (parseInt(p[1], 10) || 0);
  }
  var _DOW = [["1", "Mon"], ["2", "Tue"], ["3", "Wed"], ["4", "Thu"], ["5", "Fri"], ["6", "Sat"], ["7", "Sun"]];

  // The access-window editor for one membership plan (days + from/to). Reveals on a toggle. Saving
  // PATCHes {set_window:true, access_days, access_start_min, access_end_min}; "all days + no times"
  // = unconstrained (covers any time). Returns a collapsible element.
  function windowEditor(plan) {
    var sel = {};
    var cur = plan.access_days; // array of ISO ints, or null = all days
    var chips = el("div", { class: "cf-row", style: "gap:4px;flex-wrap:wrap" });
    _DOW.forEach(function (o) {
      var on = !cur || cur.indexOf(parseInt(o[0], 10)) >= 0;
      sel[o[0]] = on;
      var b = el("button", { class: "cf-chip" + (on ? " class" : ""), text: o[1], type: "button" });
      b.addEventListener("click", function () { sel[o[0]] = !sel[o[0]]; b.className = "cf-chip" + (sel[o[0]] ? " class" : ""); });
      chips.appendChild(b);
    });
    var fromI = input({ type: "time", value: minToTime(plan.access_start_min), style: "max-width:110px" });
    var toI = input({ type: "time", value: minToTime(plan.access_end_min), style: "max-width:110px" });
    var save = el("button", { class: "cf-btn cf-btn-sm", text: "Save hours" });
    save.addEventListener("click", async function () {
      var days = _DOW.filter(function (o) { return sel[o[0]]; }).map(function (o) { return parseInt(o[0], 10); });
      save.disabled = true;
      try {
        await window.AdminAPI.patchMembershipPlan(plan.price_id, {
          set_window: true,
          access_days: (days.length === 0 || days.length === 7) ? null : days,
          access_start_min: timeToMin(fromI.value),
          access_end_min: timeToMin(toI.value),
        });
        UI.toast("Access hours saved.", "info");
      } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { save.disabled = false; }
    });
    return el("div", { class: "cf-subtle", style: "padding:8px 0 4px;display:flex;gap:8px;align-items:center;flex-wrap:wrap" }, [
      el("span", { class: "cf-muted cf-tiny", text: "Free on:" }), chips,
      el("span", { class: "cf-muted cf-tiny", text: "from" }), fromI,
      el("span", { class: "cf-muted cf-tiny", text: "to" }), toI, save,
    ]);
  }

  // Lifecycle control shared by plan/pack/price rows: active | dormant (configured but hidden
  // from customers) | retired. onChange(newStatus) PATCHes {status}. Returns the <select>.
  function statusSelect(current, onChange) {
    var sel = el("select", { class: "cf-select", style: "max-width:155px;font-size:.82rem" });
    [["active", "● Active"], ["dormant", "◐ Dormant — hidden"], ["retired", "✕ Retired"]]
      .forEach(function (o) {
        var opt = el("option", { value: o[0], text: o[1] });
        if ((current || "active") === o[0]) opt.selected = "selected";
        sel.appendChild(opt);
      });
    sel.addEventListener("change", function () { onChange(sel.value); });
    return sel;
  }

  // ---------------------------------------------------------------------------
  // MEMBERSHIP PLANS — configurable term plans (label + price + duration). Each plan
  // is one billing.price (term_months) on the membership product. -> /membership-plans.
  // ---------------------------------------------------------------------------
  function membershipPlans(host, opts) {
    init(); opts = opts || {};
    UI.clear(host);
    var card = el("div", { class: "cf-card" });
    card.appendChild(el("h2", { text: "Membership plans" }));
    card.appendChild(el("p", { class: "cf-muted", text:
      "Set the term plans members can buy. A plan is a price for a duration (e.g. 3 months for R600). " +
      "Members pick a plan, pay online, and get unlimited-courts membership for that term." }));
    var listBox = el("div", { class: "cf-list", id: "ad-membership-plans" });
    card.appendChild(listBox);
    host.appendChild(card);

    function planTerm(months) {
      var m = parseInt(months, 10) || 0;
      return m === 1 ? "1 month" : (m + " months");
    }

    function reload() {
      UI.clear(listBox);
      listBox.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
      window.AdminAPI.membershipPlans().then(function (r) { renderList(r.plans || []); })
        .catch(function (e) { UI.clear(listBox); listBox.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    function planRow(plan) {
      var tierI = input({ value: plan.tier || "", placeholder: "Tier (e.g. Standard)", style: "max-width:130px" });
      var labelI = input({ value: plan.label || "", placeholder: planTerm(plan.term_months), style: "max-width:140px" });
      var amtI = input({ value: fromMinor(plan.amount_minor), placeholder: "0.00", style: "max-width:110px" });
      var monthsI = input({ type: "number", value: plan.term_months || 1, min: 1, style: "max-width:80px" });
      var save = el("button", { class: "cf-btn cf-btn-sm", text: "Save" });
      var status = statusSelect(plan.status, async function (s) {
        try { await window.AdminAPI.patchMembershipPlan(plan.price_id, { status: s }); UI.toast("Plan " + s + ".", "info"); reload(); }
        catch (e) { UI.toast(UI.errMsg(e), "error"); }
      });
      save.addEventListener("click", async function () {
        var months = num(monthsI.value);
        if (!months || months < 1) { UI.toast("Duration must be at least 1 month.", "warn"); return; }
        save.disabled = true;
        try {
          await window.AdminAPI.patchMembershipPlan(plan.price_id, {
            label: labelI.value.trim(), tier: tierI.value.trim(), amount_minor: toMinor(amtI.value), term_months: months,
          });
          UI.toast("Plan updated.", "info"); reload();
        } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { save.disabled = false; }
      });
      var row = el("div", { class: "cf-item", style: "flex-wrap:wrap;gap:6px" }, [
        tierI, labelI, amtI,
        el("div", { class: "cf-row", style: "gap:4px;align-items:center" }, [monthsI, el("span", { class: "cf-muted", text: "months" })]),
        el("span", { class: "cf-spacer" }), status, save,
      ]);
      // Access window (Phase 5): a "⏱ Access hours" toggle reveals the day+time editor. A summary
      // shows when the tier is time-boxed.
      var win = windowEditor(plan); win.style.display = "none";
      var hasWin = !!(plan.access_days || plan.access_start_min != null || plan.access_end_min != null);
      var winToggle = el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", type: "button",
        text: hasWin ? "⏱ Access hours · limited" : "⏱ Access hours · any time" });
      winToggle.addEventListener("click", function () { win.style.display = win.style.display === "none" ? "flex" : "none"; });
      row.appendChild(winToggle);
      var wrap = el("div", {}, [row, win]);
      if ((plan.status || "active") !== "active") wrap.style.opacity = "0.6";
      return wrap;
    }

    function renderList(plans) {
      UI.clear(listBox);
      if (!plans.length) listBox.appendChild(el("div", { class: "cf-empty", text: "No membership plans yet. Add one below." }));
      plans.forEach(function (pl) { listBox.appendChild(planRow(pl)); });
    }

    // add-plan form
    var addTier = input({ placeholder: "Tier (e.g. Student)", style: "max-width:130px" });
    var addLabel = input({ placeholder: "Label (optional)", style: "max-width:140px" });
    var addAmt = input({ placeholder: "0.00", style: "max-width:110px" });
    var addMonths = input({ type: "number", value: 1, min: 1, placeholder: "Months", style: "max-width:80px" });
    var addBtn = el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Add plan" });
    addBtn.addEventListener("click", async function () {
      var amount = toMinor(addAmt.value);
      var months = num(addMonths.value);
      if (amount == null || amount < 0) { UI.toast("Enter a price.", "warn"); return; }
      if (!months || months < 1) { UI.toast("Enter a duration in months (min 1).", "warn"); return; }
      addBtn.disabled = true;
      try {
        await window.AdminAPI.createMembershipPlan({ label: addLabel.value.trim(), tier: addTier.value.trim(), amount_minor: amount, term_months: months });
        addTier.value = ""; addLabel.value = ""; addAmt.value = ""; addMonths.value = 1;
        UI.toast("Plan added.", "info"); reload();
      } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { addBtn.disabled = false; }
    });
    card.appendChild(el("h3", { text: "Add a plan", style: "margin-top:14px" }));
    card.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin:-6px 0 8px",
      text: "Tier groups plans in the buy wizard (e.g. a 'Student' tier with 6- and 12-month terms). Leave blank for a standalone plan." }));
    card.appendChild(el("div", { class: "cf-row", style: "gap:6px;align-items:center;flex-wrap:wrap" }, [
      addTier, addLabel, addAmt,
      el("div", { class: "cf-row", style: "gap:4px;align-items:center" }, [addMonths, el("span", { class: "cf-muted", text: "months" })]),
      addBtn,
    ]));
    if (opts.before && opts.before.length) card.appendChild(actionRow(opts.before));

    reload();
    return { reload: reload };
  }

  // ---------------------------------------------------------------------------
  // COACH AGREEMENTS — the commission/rental config (Phase C owner lane).
  // Headline owner ask: a clean PER-SERVICE commission editor. Hierarchy (most specific wins):
  //   coach + service  ›  service (all coaches)  ›  coach (all services)  ›  club default.
  // Per coach we show rent + a coach-level %, then a skimmable per-service table with BOTH the
  // club-wide rate (global, {product_id}) and this coach's override ({coach_user_id, product_id}),
  // each Set/Clear-able, with the live resolved effective_pct alongside. Rules can be cleared via
  // DELETE /commission-rules/<rule_id> (we resolve the rule_id from data.rules by scope+keys).
  // ---------------------------------------------------------------------------
  function coachAgreements(host, opts) {
    init(); opts = opts || {};
    UI.clear(host);
    var card = el("div", { class: "cf-card" });
    card.appendChild(el("h2", { text: "Coach pay" }));
    card.appendChild(el("p", { class: "cf-muted", text:
      "How you monetise each coach: a flat monthly rent and/or a commission % on their lessons " +
      "and classes. Rent and commission add together (not either/or). Commission is taken on " +
      "collected, ex-VAT revenue. The most specific rate wins: coach + service, then the service " +
      "(all coaches), then the coach (all services), then the club default." }));
    var body = el("div", { id: "ad-coach-agreements" });
    card.appendChild(body);
    host.appendChild(card);

    var DATA = null;  // last loaded payload (for rule_id lookup on Clear).

    // Find the ACTIVE rule_id matching a scope's exact keys, or null. Lets us Clear a rule.
    function ruleIdFor(scope, productId, coachId) {
      var rules = (DATA && DATA.rules) || [];
      for (var i = 0; i < rules.length; i++) {
        var r = rules[i];
        if (!r.active) continue;
        if (r.scope !== scope) continue;
        if (String(r.product_id || "") !== String(productId || "")) continue;
        if (String(r.coach_user_id || "") !== String(coachId || "")) continue;
        return r.id;
      }
      return null;
    }

    // A small inline %-editor cell: number input + Set + (Clear when a rule exists).
    // saveArgs() -> body for setCommissionRule; scope/keys identify the rule to Clear.
    function pctCell(currentPct, scope, productId, coachId, savedMsg) {
      var wrap = el("div", { class: "cf-row", style: "gap:5px;align-items:center" });
      var inp = input({ type: "number", step: "0.5", min: 0, max: 100,
        value: (currentPct != null ? currentPct : ""), placeholder: "—", style: "max-width:78px" });
      var set = el("button", { class: "cf-btn cf-btn-sm", text: "Set" });
      set.addEventListener("click", async function () {
        var pct = parseFloat(inp.value);
        if (isNaN(pct) || pct < 0 || pct > 100) { UI.toast("Enter 0–100.", "warn"); return; }
        set.disabled = true;
        var b = { commission_pct: pct };
        if (productId) b.product_id = productId;
        if (coachId) b.coach_user_id = coachId;
        try { await window.AdminAPI.setCommissionRule(b); UI.toast(savedMsg || "Saved.", "info"); reload(); }
        catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { set.disabled = false; }
      });
      wrap.appendChild(inp); wrap.appendChild(set);
      var rid = ruleIdFor(scope, productId, coachId);
      if (rid) {
        var clr = el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", title: "Clear this rule", text: "Clear" });
        clr.addEventListener("click", async function () {
          clr.disabled = true;
          try { await window.AdminAPI.deleteCommissionRule(rid); UI.toast("Rule cleared.", "info"); reload(); }
          catch (e) { UI.toast(UI.errMsg(e), "error"); clr.disabled = false; }
        });
        wrap.appendChild(clr);
      }
      return wrap;
    }

    function clubDefaultRow(data) {
      var box = el("div", { class: "cf-card", style: "background:var(--cf-surface-2,#f7f8fa)" });
      box.appendChild(el("h3", { text: "Club default commission" }));
      box.appendChild(el("p", { class: "cf-muted", text:
        "The % of every lesson the club keeps by default. Coaches keep the rest. Override per coach or per service below." }));
      var pctI = input({ type: "number", step: "0.5", min: 0, max: 100,
        value: (data.club_default_pct != null ? data.club_default_pct : 0), style: "max-width:110px" });
      var save = el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Save default" });
      save.addEventListener("click", async function () {
        var pct = parseFloat(pctI.value);
        if (isNaN(pct) || pct < 0 || pct > 100) { UI.toast("Enter 0–100.", "warn"); return; }
        save.disabled = true;
        try { await window.AdminAPI.setCommissionRule({ commission_pct: pct });
          UI.toast("Club default saved.", "info"); reload(); }
        catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { save.disabled = false; }
      });
      box.appendChild(el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [
        pctI, el("span", { class: "cf-muted", text: "% the club keeps" }), el("span", { class: "cf-spacer" }), save]));
      return box;
    }

    // Guess a lesson/class chip for a service by name (the payload has no kind field). Cosmetic only.
    function kindChip(name) {
      var isClass = /class|clinic|group|squad|camp/i.test(name || "");
      return el("span", { class: "cf-chip " + (isClass ? "class" : "lesson"), text: isClass ? "class" : "lesson" });
    }

    function coachCard(coach, currency) {
      var c = el("div", { class: "cf-card" });
      c.appendChild(el("h3", { text: coach.name }));

      // rent + rent day
      var rentI = input({ value: fromMinor(coach.rent_minor), placeholder: "0.00", style: "max-width:120px" });
      var dayI = input({ type: "number", min: 1, max: 28, value: coach.rent_day || 1, style: "max-width:80px" });
      var rentSave = el("button", { class: "cf-btn cf-btn-sm", text: "Save rent" });
      rentSave.addEventListener("click", async function () {
        rentSave.disabled = true;
        try {
          await window.AdminAPI.putCoachAgreement(coach.coach_user_id, {
            rent_minor: toMinor(rentI.value) || 0, rent_day: num(dayI.value) || 1 });
          UI.toast("Rent saved.", "info");
        } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { rentSave.disabled = false; }
      });
      c.appendChild(field("Monthly rent (" + currency + ")",
        el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [
          rentI, el("span", { class: "cf-muted", text: "on day" }), dayI,
          el("span", { class: "cf-spacer" }), rentSave])));

      // coach-level commission % (the DEFAULT for all this coach's services) — Set/Clear.
      c.appendChild(field("Default commission % — all this coach's services",
        pctCell(coach.coach_pct, "coach", null, coach.coach_user_id, "Coach commission saved.")));

      // Per-SERVICE overrides now live in the Service Editor (Settings → Services → Manage), so a
      // service is edited in ONE place. This screen keeps only rent + the global/per-coach default.
      c.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin-top:10px",
        text: "Need a different rate for one specific service? Set it on the service itself — Settings → Services → Manage → Commission." }));
      return c;
    }

    function render(data) {
      DATA = data || {};
      UI.clear(body);
      body.appendChild(clubDefaultRow(DATA));
      var coaches = DATA.coaches || [];
      if (!coaches.length) {
        body.appendChild(el("div", { class: "cf-empty", text: "No coaches yet — invite a coach in the Coaches tab." }));
        return;
      }
      coaches.forEach(function (co) { body.appendChild(coachCard(co, DATA.currency || "ZAR")); });
    }

    function reload() {
      UI.clear(body); body.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
      window.AdminAPI.coachAgreements().then(render)
        .catch(function (e) { UI.clear(body); body.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    reload();
    return { reload: reload };
  }

  // ---------------------------------------------------------------------------
  // SESSION PACKS (token bundles) — configurable prepaid packs (docs/specs/02). Generic across
  // court/lesson/class: any service, duration, price, #sessions, validity, and (lesson) coach.
  // Each plan is a billing.bundle_plan row. -> /bundle-plans.
  // ---------------------------------------------------------------------------
  function bundlePlans(host, opts) {
    init(); opts = opts || {};
    UI.clear(host);
    var coaches = [];  // [{id,display_name|email}] for lesson-pack coach scoping
    var card = el("div", { class: "cf-card" });
    card.appendChild(el("h2", { text: "Session packs" }));
    card.appendChild(el("p", { class: "cf-muted", text:
      "Sell prepaid packs of sessions (tokens). A member buys N sessions upfront; booking a matching " +
      "service draws one; cancelling credits it back. Works for courts, lessons and classes — set the " +
      "service, how many sessions, the duration they cover (optional), the price, and an optional expiry. " +
      "Lesson packs can be tied to one coach." }));
    var listBox = el("div", { class: "cf-list", id: "ad-bundle-plans" });
    card.appendChild(listBox);
    host.appendChild(card);

    var KINDS = [
      { value: "court", label: "Court sessions" },
      { value: "lesson", label: "Lessons" },
      { value: "class", label: "Classes" },
    ];
    function coachOptions() {
      return [{ value: "", label: "Any coach" }].concat(coaches.map(function (c) {
        return { value: c.id, label: c.display_name || c.email || "Coach" };
      }));
    }
    function kindLabel(k) { var m = { court: "Court", lesson: "Lesson", class: "Class" }; return m[k] || k; }

    function planRow(plan) {
      var labelI = input({ value: plan.label || "", placeholder: plan.sessions_count + " " + kindLabel(plan.service_kind), style: "max-width:150px" });
      var nI = input({ type: "number", min: 1, value: plan.sessions_count || 1, style: "max-width:70px" });
      var durI = input({ type: "number", min: 0, value: plan.duration_minutes || "", placeholder: "any", style: "max-width:80px" });
      var amtI = input({ value: fromMinor(plan.price_minor), placeholder: "0.00", style: "max-width:100px" });
      var valI = input({ type: "number", min: 0, value: plan.validity_days || "", placeholder: "never", style: "max-width:80px" });
      var coachSel = plan.service_kind === "lesson"
        ? select(plan.coach_user_id || "", coachOptions()) : null;
      if (coachSel) coachSel.style.maxWidth = "150px";
      var save = el("button", { class: "cf-btn cf-btn-sm", text: "Save" });
      var status = statusSelect(plan.status, async function (s) {
        try { await window.AdminAPI.patchBundlePlan(plan.id, { status: s }); UI.toast("Pack " + s + ".", "info"); reload(); }
        catch (e) { UI.toast(UI.errMsg(e), "error"); }
      });
      save.addEventListener("click", async function () {
        var n = num(nI.value);
        if (!n || n < 1) { UI.toast("Sessions must be at least 1.", "warn"); return; }
        var body = {
          label: labelI.value.trim(), sessions_count: n, price_minor: toMinor(amtI.value),
          duration_minutes: num(durI.value) || null, validity_days: num(valI.value) || null,
          clear_duration: !durI.value, clear_validity: !valI.value,
        };
        if (coachSel) { body.coach_user_id = coachSel.value || null; body.clear_coach = !coachSel.value; }
        save.disabled = true;
        try { await window.AdminAPI.patchBundlePlan(plan.id, body); UI.toast("Pack updated.", "info"); reload(); }
        catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { save.disabled = false; }
      });
      var kids = [
        el("span", { class: "cf-chip", text: kindLabel(plan.service_kind) }),
        labelI,
        el("div", { class: "cf-row", style: "gap:3px;align-items:center" }, [nI, el("span", { class: "cf-muted", text: "× " })]),
        el("div", { class: "cf-row", style: "gap:3px;align-items:center" }, [durI, el("span", { class: "cf-muted", text: "min" })]),
        amtI,
        el("div", { class: "cf-row", style: "gap:3px;align-items:center" }, [valI, el("span", { class: "cf-muted", text: "days" })]),
      ];
      if (coachSel) kids.push(coachSel);
      kids.push(el("span", { class: "cf-spacer" }), status, save);
      var row = el("div", { class: "cf-item", style: "flex-wrap:wrap;gap:6px" }, kids);
      if ((plan.status || "active") !== "active") row.style.opacity = "0.6";
      return row;
    }

    function renderList(plans) {
      UI.clear(listBox);
      if (!plans.length) listBox.appendChild(el("div", { class: "cf-empty", text: "No session packs yet. Add one below." }));
      plans.forEach(function (pl) { listBox.appendChild(planRow(pl)); });
    }

    function reload() {
      UI.clear(listBox);
      listBox.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
      window.AdminAPI.bundlePlans().then(function (r) { renderList(r.plans || []); })
        .catch(function (e) { UI.clear(listBox); listBox.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    // add-plan form — service kind drives whether a coach picker shows.
    var addKind = select("court", KINDS);
    var addLabel = input({ placeholder: "Label (optional)", style: "max-width:160px" });
    var addN = input({ type: "number", min: 1, value: 10, placeholder: "Sessions", style: "max-width:80px" });
    var addDur = input({ type: "number", min: 0, placeholder: "min (any)", style: "max-width:90px" });
    var addAmt = input({ placeholder: "Price 0.00", style: "max-width:110px" });
    var addVal = input({ type: "number", min: 0, placeholder: "days (never)", style: "max-width:100px" });
    var addCoachWrap = el("span");
    function refreshCoachPicker() {
      UI.clear(addCoachWrap);
      if (addKind.value === "lesson") {
        var sel = select("", coachOptions()); sel.style.maxWidth = "150px"; sel.id = "ad-bundle-add-coach";
        addCoachWrap.appendChild(sel);
      }
    }
    addKind.addEventListener("change", refreshCoachPicker);
    var addBtn = el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Add pack" });
    addBtn.addEventListener("click", async function () {
      var amount = toMinor(addAmt.value);
      var n = num(addN.value);
      if (amount == null || amount < 0) { UI.toast("Enter a price.", "warn"); return; }
      if (!n || n < 1) { UI.toast("Enter how many sessions (min 1).", "warn"); return; }
      var body = {
        service_kind: addKind.value, sessions_count: n, price_minor: amount,
        label: addLabel.value.trim(), duration_minutes: num(addDur.value) || null,
        validity_days: num(addVal.value) || null,
      };
      var coachSel = document.getElementById("ad-bundle-add-coach");
      if (coachSel && coachSel.value) body.coach_user_id = coachSel.value;
      addBtn.disabled = true;
      try {
        await window.AdminAPI.createBundlePlan(body);
        addLabel.value = ""; addAmt.value = ""; addN.value = 10; addDur.value = ""; addVal.value = "";
        UI.toast("Pack added.", "info"); reload();
      } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { addBtn.disabled = false; }
    });
    card.appendChild(el("h3", { text: "Add a pack", style: "margin-top:14px" }));
    card.appendChild(el("div", { class: "cf-row", style: "gap:6px;align-items:center;flex-wrap:wrap" }, [
      addKind, addLabel,
      el("div", { class: "cf-row", style: "gap:3px;align-items:center" }, [addN, el("span", { class: "cf-muted", text: "sessions" })]),
      el("div", { class: "cf-row", style: "gap:3px;align-items:center" }, [addDur, el("span", { class: "cf-muted", text: "min" })]),
      addAmt,
      el("div", { class: "cf-row", style: "gap:3px;align-items:center" }, [addVal, el("span", { class: "cf-muted", text: "valid days" })]),
      addCoachWrap, addBtn,
    ]));

    // Load coaches (for lesson-pack scoping) then the plans.
    window.AdminAPI.coaches().then(function (r) { coaches = r.coaches || []; })
      .catch(function () {}).then(function () { refreshCoachPicker(); reload(); });
    return { reload: reload };
  }

  // ---------------------------------------------------------------------------
  // COURT RATES — clean per-DURATION editor for court hire (the core PAYG config).
  // No audience/unit jargon: every rate is audience='any', unit='per_booking', with the
  // duration the customer actually picks. Reuses the 3-state status control. -> /products + /prices.
  // ---------------------------------------------------------------------------
  function courtRates(host, opts) {
    init(); opts = opts || {};
    UI.clear(host);
    var card = el("div", { class: "cf-card" });
    card.appendChild(el("h2", { text: "Court rates" }));
    card.appendChild(el("p", { class: "cf-muted", text: "What a court costs per length of booking. These are the prices members see when they book." }));
    var listBox = el("div", { class: "cf-list" });
    card.appendChild(listBox);
    host.appendChild(card);

    var productId = null;

    function rateRow(pr) {
      var durI = input({ type: "number", min: 0, value: pr.duration_minutes || "", placeholder: "60", style: "max-width:80px" });
      var amtI = input({ value: fromMinor(pr.amount_minor), placeholder: "0.00", style: "max-width:110px" });
      var save = el("button", { class: "cf-btn cf-btn-sm", text: "Save" });
      save.addEventListener("click", async function () {
        var dur = num(durI.value);
        if (!dur || dur < 1) { UI.toast("Enter the booking length in minutes.", "warn"); return; }
        save.disabled = true;
        try {
          await window.AdminAPI.patchPrice(pr.id, { duration_minutes: dur, amount_minor: toMinor(amtI.value) });
          UI.toast("Rate saved.", "info"); reload();
        } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { save.disabled = false; }
      });
      var status = statusSelect(pr.status, async function (s) {
        try { await window.AdminAPI.patchPrice(pr.id, { status: s }); UI.toast("Rate " + s + ".", "info"); reload(); }
        catch (e) { UI.toast(UI.errMsg(e), "error"); }
      });
      var row = el("div", { class: "cf-item", style: "gap:6px;align-items:center;flex-wrap:wrap" }, [
        el("div", { class: "cf-row", style: "gap:4px;align-items:center" }, [durI, el("span", { class: "cf-muted", text: "min" })]),
        el("span", { class: "cf-muted", text: "→" }), amtI,
        el("span", { class: "cf-spacer" }), status, save,
      ]);
      if ((pr.status || "active") !== "active") row.style.opacity = "0.6";
      return row;
    }

    function renderList(products) {
      UI.clear(listBox);
      var court = (products || []).filter(function (p) { return p.kind === "court_booking" || p.kind === "court_hire"; })[0];
      if (!court) {
        listBox.appendChild(el("div", { class: "cf-empty", text: "No court service yet — add one in onboarding or Settings → Services." }));
        return;
      }
      productId = court.id;
      // Court per-duration rates only (skip any stray no-duration/legacy rows).
      var rates = (court.prices || []).filter(function (pr) { return pr.duration_minutes != null; })
        .sort(function (a, b) { return (a.duration_minutes || 0) - (b.duration_minutes || 0); });
      if (!rates.length) listBox.appendChild(el("div", { class: "cf-empty", text: "No rates yet. Add your first below." }));
      rates.forEach(function (pr) { listBox.appendChild(rateRow(pr)); });

      // add-rate
      var nDur = input({ type: "number", min: 0, placeholder: "60", style: "max-width:80px" });
      var nAmt = input({ placeholder: "0.00", style: "max-width:110px" });
      var add = el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Add rate" });
      add.addEventListener("click", async function () {
        var dur = num(nDur.value);
        if (!dur || dur < 1) { UI.toast("Enter the booking length in minutes.", "warn"); return; }
        add.disabled = true;
        try {
          await window.AdminAPI.createPrice({ product_id: productId, audience: "any",
            unit: "per_booking", duration_minutes: dur, amount_minor: toMinor(nAmt.value) });
          UI.toast("Rate added.", "info"); reload();
        } catch (e) { UI.toast(UI.errMsg(e), "error"); } finally { add.disabled = false; }
      });
      listBox.appendChild(el("div", { class: "cf-item", style: "gap:6px;align-items:center;border-top:1px dashed var(--border);flex-wrap:wrap" }, [
        el("div", { class: "cf-row", style: "gap:4px;align-items:center" }, [nDur, el("span", { class: "cf-muted", text: "min" })]),
        el("span", { class: "cf-muted", text: "→" }), nAmt, el("span", { class: "cf-spacer" }), add,
      ]));
    }

    function reload() {
      UI.clear(listBox); listBox.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
      window.AdminAPI.products().then(function (r) { renderList(r.products || []); })
        .catch(function (e) { UI.clear(listBox); listBox.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }
    reload();
    return { reload: reload };
  }

  // PRICING HOME — one place for everything purchasable: court rates · session packs · memberships.
  // (Lesson rates + lesson packs are coach-owned and live in the coach console.)
  function pricingHome(host) {
    init(); UI.clear(host);
    host.appendChild(el("p", { class: "cf-muted", style: "margin:0 0 12px",
      text: "Everything members can buy — court rates, prepaid packs and memberships — in one place. Hide something from customers with Dormant; bring it back any time." }));
    var rates = el("div"); host.appendChild(rates); courtRates(rates, {});
    var packs = el("div", { style: "margin-top:18px" }); host.appendChild(packs); bundlePlans(packs, {});
    var mem = el("div", { style: "margin-top:18px" }); host.appendChild(mem); membershipPlans(mem, {});
  }

  window.AdminUI = {
    clubProfile: clubProfile, hours: hours, courts: courts,
    services: services, coaches: coaches, membershipPlans: membershipPlans,
    coachAgreements: coachAgreements, bundlePlans: bundlePlans,
    courtRates: courtRates, pricingHome: pricingHome,
  };
})();

