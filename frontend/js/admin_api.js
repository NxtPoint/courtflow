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
    // ---- home command-center --------------------------------------------
    // GET /api/admin/home -> {money:{currency,owed_to_club_minor,net_revenue_minor,rent_due_minor,
    //   active_members}, people:{new_signups_7d,coach_invites_pending,memberships_expiring_14d},
    //   approvals:{refund_requests_pending}}
    home: function () { return A().apiJSON("/api/admin/home"); },

    // ---- people (roster + unified person 360) ----------------------------
    // GET /api/admin/people -> {people:[{user_id,email,first_name,surname,phone,role,
    //   member_status,display_name,invite_status,has_membership}]}
    people: function () { return A().apiJSON("/api/admin/people"); },
    // A client's active lesson packs (optionally for a coach) — on-behalf auto-routes to their pack.
    clientPackages: function (userId, coachId) { return A().apiJSON("/api/admin/clients/" + enc(userId) + "/packages" + (coachId ? ("?coach_id=" + enc(coachId)) : "")); },
    // POST /api/admin/clients  body:{name,email,phone} -> {user_id,email,name,created} — add a
    // walk-up / off-system client to the system (they link to their login by email on first sign-in).
    createClient: function (body) { return A().apiJSON("/api/admin/clients", { method: "POST", body: body || {} }); },
    // POST /api/admin/members/:id/issue  body:{kind:'membership'|'pack', price_id?|bundle_plan_id?,
    //   start_date?, mark_paid?, pay_provider?} -> the purchase (owed order + activated; mark_paid settles).
    issuePackage: function (id, body) { return A().apiJSON("/api/admin/members/" + enc(id) + "/issue", { method: "POST", body: body || {} }); },
    // GET /api/admin/people/:user_id -> {person:{...profile,roles,is_coach,member_status,
    //   membership, statement:{items,total_owed_minor}, owed_minor, payments:[], upcoming:[],
    //   history:[], bookings_count, settlement?}}  — one record, drill-through to the event story.
    person: function (id) { return A().apiJSON("/api/admin/people/" + enc(id)); },
    // (grantMembership wrapper removed 2026-07-05 — the SPA uses issuePackage; the classic console
    //  hits POST /api/admin/members/<id>/membership directly.)
    // DELETE /api/admin/members/:user_id/membership -> {ok, voided_orders}
    revokeMembership: function (id) {
      return A().apiJSON("/api/admin/members/" + enc(id) + "/membership", { method: "DELETE" });
    },
    // POST /api/admin/orders/:order_id/void  body: {write_off?} -> {ok} — clear an UNPAID order.
    voidOrder: function (id, body) {
      return A().apiJSON("/api/admin/orders/" + enc(id) + "/void", { method: "POST", body: body || {} });
    },
    // POST /api/admin/orders/:order_id/discount  body: {discount_minor|new_amount_minor, reason}
    //   -> {order_id, old_total_minor, new_total_minor, discount_minor} — reprice an OPEN order
    //   (original preserved; coach_arrears kept in lockstep; a paid order rejects).
    discountOrder: function (id, body) {
      return A().apiJSON("/api/admin/orders/" + enc(id) + "/discount", { method: "POST", body: body || {} });
    },
    // POST /api/admin/clients/:id/wallets/:wallet_id/adjust  body: {delta_sessions|delta_minutes, reason}
    //   -> {wallet_id, minutes_remaining, minutes_total, tokens_remaining, status} — add/subtract pack balance.
    walletAdjust: function (clientId, walletId, body) {
      return A().apiJSON("/api/admin/clients/" + enc(clientId) + "/wallets/" + enc(walletId) + "/adjust", { method: "POST", body: body || {} });
    },
    // POST /api/admin/clients/:id/wallets/:wallet_id/expire  body: {reason} -> soft-expire a pack (audited).
    walletExpire: function (clientId, walletId, body) {
      return A().apiJSON("/api/admin/clients/" + enc(clientId) + "/wallets/" + enc(walletId) + "/expire", { method: "POST", body: body || {} });
    },

    // ---- admin event story (the ONE shared drill target) -----------------
    // GET /api/admin/bookings/:id -> {booking:{id,booking_type,status,starts_at,ends_at,
    //   duration_minutes,is_future,court_name,coach:{name,user_id},client:{name,email,phone,user_id},
    //   venue,players,order_id,charge,arrears,ics_url,can:{...}}} — god-view of any booking.
    bookingStory: function (id) { return A().apiJSON("/api/admin/bookings/" + enc(id)); },
    // POST /api/admin/bookings/:id/reassign-coach  body: {coach_user_id} -> {ok, booking}. 409 busy.
    reassignCoach: function (id, body) {
      return A().apiJSON("/api/admin/bookings/" + enc(id) + "/reassign-coach", { method: "POST", body: body || {} });
    },
    // POST /api/services  body:{service_kind:'lesson', coach_user_id, name, duration_minutes, amount_minor}
    // Owner creates a lesson FOR a chosen coach (the product is owned by that coach). -> {service}
    createService: function (body) { return A().apiJSON("/api/services", { method: "POST", body: body || {} }); },
    // POST /api/admin/coach-statement/arrears/:id/collected -> accrue commission (off-platform pay).
    arrearsCollected: function (id) {
      return A().apiJSON("/api/admin/coach-statement/arrears/" + enc(id) + "/collected", { method: "POST", body: {} });
    },
    // PATCH /api/admin/coach-statement/arrears/:id  body: {gross_minor?}|{status:'written_off',reason?}
    arrearsAdjust: function (id, body) {
      return A().apiJSON("/api/admin/coach-statement/arrears/" + enc(id), { method: "PATCH", body: body || {} });
    },
    // POST /api/billing/yoco/refund  body: {order_id, amount_minor?, cancel_booking?} — admin refund.
    yocoRefund: function (body) { return A().apiJSON("/api/billing/yoco/refund", { method: "POST", body: body || {} }); },

    // ---- onboarding ------------------------------------------------------
    // GET /api/admin/onboarding ->
    //   {completed, steps:{profile,hours,courts,services,coaches},
    //    club, location, branding, policy, counts:{courts,products,coaches}}
    onboarding: function () { return A().apiJSON("/api/admin/onboarding"); },

    // GET /api/admin/activity -> {activity:[{at,kind,title,detail,amount_minor,currency,direction}]}
    //   the club-wide transaction log (payments, refunds, orders, commission, arrears, memberships).
    activity: function (limit) {
      return A().apiJSON("/api/admin/activity" + (limit ? ("?limit=" + limit) : ""));
    },
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
    // GET /api/admin/bundle-plans -> {plans:[...]} — READ ONLY, for the offline "issue a pack" picker.
    // (create/patch/delete removed 2026-07-09 — packs are created/edited ONLY under a service via the
    //  services lane /api/services/<id>/packages; there is no standalone pack editor.)
    bundlePlans: function () { return A().apiJSON("/api/admin/bundle-plans"); },

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
    // PATCH /api/admin/coaches/:id  body:{is_bookable}  (Hide/Unhide a coach)
    patchCoach: function (id, body) { return A().apiJSON("/api/admin/coaches/" + enc(id), { method: "PATCH", body: body }); },
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
    // PATCH /api/admin/classes/:resource_id  body: {coach_user_id, name?, capacity?, description?,
    //   court_resource_ids?} — edit a class: (re)assign coach (lockstep across product + future
    //   sessions) + reassign the courts its upcoming sessions hold. -> {class, coach_conflicts?}
    updateClass: function (resourceId, body) {
      return A().apiJSON("/api/admin/classes/" + enc(resourceId), { method: "PATCH", body: body || {} });
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

    // ---- insights (Phase 2 P1 read-layer) --------------------------------
    // GET /api/insights/court-utilisation?days= -> {days, overall_pct, booked_hours,
    //   available_hours, cells:[{weekday,hour,booked_hours,available_hours,pct}]}
    courtUtilisation: function (days) { return A().apiJSON("/api/insights/court-utilisation" + (days ? ("?days=" + days) : "")); },
    // GET /api/insights/overview?month=YYYY-MM -> {month, currency, days:[iso], series:{visits,
    //   unique_visitors,bookings,bookings_court,bookings_lesson,bookings_class,member_bookings,
    //   revenue_gross_minor,revenue_net_minor,refunded_minor,new_clients,active_members,nps_responses},
    //   kpis:{...}, breakdowns:{sources,top_pages,by_device}}
    overview: function (month) { return A().apiJSON("/api/insights/overview" + (month ? ("?month=" + month) : "")); },
    // GET /api/insights/sales-by-day?month=YYYY-MM -> {month, currency, total_minor, count,
    //   days:[{date, total_minor, sales:[{payment_id,order_id,booking_id,client_name,service_type,
    //   description,amount_minor,at}]}]}
    salesByDay: function (month) { return A().apiJSON("/api/insights/sales-by-day" + (month ? ("?month=" + month) : "")); },
    // GET /api/insights/bookings-by-day?month=YYYY-MM -> {month, count, by_type:{court,lesson,class},
    //   days:[{date, count, bookings:[{booking_id,booking_type,status,client_name,coach_name,
    //   court_name,description,starts_at}]}]}
    bookingsByDay: function (month) { return A().apiJSON("/api/insights/bookings-by-day" + (month ? ("?month=" + month) : "")); },

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
  // COURTS & HOURS (unified) — each court is a summary block (click to edit), exactly like
  // services/memberships/coaches. The editor sets name + surface + the court's OWN weekly playing
  // hours (per day, open/closed + time range + slot). -> resources + per-resource /hours.
  // ---------------------------------------------------------------------------
  function courtsManage(host) {
    init();
    var DATA = { courts: [], hoursByCourt: {} };

    function reload() {
      UI.clear(host);
      host.appendChild(el("div", { class: "cf-card" }, [
        el("h2", { text: "Courts & hours" }),
        el("p", { class: "cf-muted", text: "Each court with its own surface and weekly playing hours. Click a court to edit; add or remove courts below." }),
      ]));
      var listBox = el("div"); host.appendChild(listBox);
      host.appendChild(addCard());
      listBox.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
      Promise.all([window.AdminAPI.resources(), window.AdminAPI.hours(), window.AdminAPI.products()]).then(function (res) {
        DATA.courts = (res[0].resources || []).filter(function (x) { return x.kind === "court" && x.is_active !== false; });
        DATA.hoursByCourt = {};
        (res[1].hours || []).forEach(function (h) { (DATA.hoursByCourt[h.resource_id] = DATA.hoursByCourt[h.resource_id] || []).push(h); });
        DATA.courtServices = (res[2].products || []).filter(function (p) { return p.kind === "court_booking" && p.active !== false; });
        UI.clear(listBox);
        if (!DATA.courts.length) { listBox.appendChild(el("div", { class: "cf-empty", text: "No courts yet. Add your first below." })); return; }
        var list = el("div", { class: "cf-list" });
        DATA.courts.forEach(function (c) { list.appendChild(courtRow(c)); });
        listBox.appendChild(list);
      }, function (e) { UI.clear(listBox); listBox.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    function svcName(pid) {
      var p = (DATA.courtServices || []).filter(function (x) { return String(x.id) === String(pid); })[0];
      return p ? p.name : null;
    }
    function hoursSummary(c) {
      var rows = DATA.hoursByCourt[c.id] || [];
      if (!rows.length) return "no hours set";
      var byDay = {}; rows.forEach(function (r) { byDay[r.weekday] = r; });
      var openDays = Object.keys(byDay).map(Number).sort(function (a, b) { return a - b; });
      var first = byDay[openDays[0]];
      var t = (first.start_time || "").slice(0, 5) + "–" + (first.end_time || "").slice(0, 5);
      return openDays.map(function (d) { return WEEKDAYS[d]; }).join(", ") + " · " + t;
    }

    function courtRow(c) {
      var row = el("div", { class: "cf-item cf-pickable" }, [
        el("span", { class: "cf-chip court", text: "court" }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: c.name || "Court" }),
          el("div", { class: "cf-item-s", text: [(c.surface || "hard"), svcName(c.product_id), hoursSummary(c)].filter(Boolean).join(" · ") }),
        ]),
        el("span", { class: "cf-spacer" }),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Delete", onclick: function (ev) { ev.stopPropagation(); delCourt(c); } }),
      ]);
      row.addEventListener("click", function () { openCourt(c); });
      return row;
    }

    function delCourt(c) {
      if (!window.confirm("Delete " + (c.name || "this court") + "?")) return;
      window.AdminAPI.deleteResource(c.id).then(function (r) {
        UI.toast((r && r.outcome === "archived") ? "This court has booking history, so it was archived (hidden) rather than deleted." : "Court deleted.", "info");
        reload();
      }, function (e) { UI.toast(UI.errMsg(e), "error"); });
    }

    function addCard() {
      var card = el("div", { class: "cf-card" }, [el("h3", { text: "Add a court" })]);
      var nm = input({ placeholder: "Court name (e.g. Court 1)", style: "max-width:220px" });
      var sf = select("hard", SURFACES);
      var b = el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Add court" });
      b.addEventListener("click", function () {
        var name = nm.value.trim(); if (!name) { UI.toast("Enter a court name.", "warn"); return; }
        b.disabled = true;
        window.AdminAPI.createResource({ kind: "court", name: name, surface: sf.value, capacity: 4 })
          .then(function () { UI.toast("Court added.", "info"); reload(); }, function (e) { b.disabled = false; UI.toast(UI.errMsg(e), "error"); });
      });
      card.appendChild(el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap;align-items:center" }, [nm, sf, b]));
      return card;
    }

    function openCourt(c) {
      var existing = {}; (DATA.hoursByCourt[c.id] || []).forEach(function (h) { existing[h.weekday] = h; });
      var hasAny = Object.keys(existing).length > 0;  // a court with NO hours yet → default a sensible open week
      var m = { name: c.name || "", surface: c.surface || "hard", product_id: c.product_id || "", rows: [] };
      WEEKDAYS.forEach(function (lbl, wd) {
        var h = existing[wd];
        m.rows.push({ wd: wd, label: lbl, open: hasAny ? !!h : (wd < 6), start: h ? (h.start_time || "").slice(0, 5) : "07:00",
                      end: h ? (h.end_time || "").slice(0, 5) : "21:00", slot: h ? (h.slot_minutes || 60) : 60 });
      });
      render();

      function render() {
        UI.clear(host);
        var saveB = el("button", { class: "cf-btn cf-btn-primary", text: "Save & close" });
        saveB.addEventListener("click", function () { save(saveB); });
        host.appendChild(el("div", { class: "cf-editbar" }, [
          el("button", { class: "cf-btn", text: "← Cancel", onclick: reload }),
          el("strong", { text: c.name || "Court" }), el("span", { class: "cf-spacer" }), saveB,
        ]));
        var nameI = input({ value: m.name, style: "max-width:260px;font-weight:700" }); nameI.addEventListener("input", function () { m.name = nameI.value; });
        var surfI = select(m.surface, SURFACES); surfI.addEventListener("change", function () { m.surface = surfI.value; });
        // Court service allocation — which court-hire tier (Hardcourt / Clay …) this court belongs to.
        // Its price + packs come from that service. Unassigned = the club's default court service.
        var svcOpts = [{ value: "", label: "— Default court service —" }].concat(
          (DATA.courtServices || []).map(function (p) { return { value: p.id, label: p.name }; }));
        var svcI = select(m.product_id || "", svcOpts); svcI.addEventListener("change", function () { m.product_id = svcI.value; });
        var details = [el("h3", { text: "Details" }), field("Court name", nameI), field("Surface", surfI)];
        if ((DATA.courtServices || []).length) details.push(field("Court service", svcI));
        host.appendChild(el("div", { class: "cf-card" }, details));

        var hc = el("div", { class: "cf-card" }, [el("h3", { text: "Playing hours" }),
          el("p", { class: "cf-muted cf-tiny", text: "The days and hours bookings can be made on this court. Untick a day to close it." })]);
        var grid = el("div", { class: "cf-list" });
        m.rows.forEach(function (r) {
          var tgl = input({ type: "checkbox" }); tgl.checked = r.open; tgl.style.width = "auto"; tgl.addEventListener("change", function () { r.open = tgl.checked; });
          var st = input({ type: "time", value: r.start, style: "max-width:120px" }); st.addEventListener("input", function () { r.start = st.value; });
          var en = input({ type: "time", value: r.end, style: "max-width:120px" }); en.addEventListener("input", function () { r.end = en.value; });
          var sl = select(r.slot, [{ value: 30, label: "30 min" }, { value: 60, label: "60 min" }, { value: 90, label: "90 min" }, { value: 120, label: "120 min" }]);
          sl.addEventListener("change", function () { r.slot = num(sl.value) || 60; });
          grid.appendChild(el("div", { class: "cf-item", style: "flex-wrap:wrap;gap:8px" }, [
            el("label", { class: "cf-row", style: "gap:6px;min-width:96px;font-weight:600;cursor:pointer" }, [tgl, el("span", { text: r.label })]),
            el("div", { class: "cf-row", style: "gap:6px;align-items:center" }, [st, el("span", { class: "cf-muted", text: "to" }), en, sl]),
          ]));
        });
        hc.appendChild(grid);
        host.appendChild(hc);
      }

      async function save(btn) {
        var name = (m.name || "").trim(); if (!name) { UI.toast("Name the court.", "warn"); return; }
        btn.disabled = true; btn.textContent = "Saving…";
        try {
          await window.AdminAPI.patchResource(c.id, { name: name, surface: m.surface, product_id: m.product_id || null });
          var week = m.rows.map(function (r) { return { weekday: r.wd, open: r.open, start_time: r.start || "07:00", end_time: r.end || "21:00", slot_minutes: r.slot || 60 }; });
          await window.AdminAPI.putHours({ scope: c.id, week: week });
          UI.toast("Saved.", "info"); reload();
        } catch (e) { btn.disabled = false; btn.textContent = "Save & close"; UI.toast(UI.errMsg(e), "error"); }
      }
    }

    reload();
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

  // (SESSION PACKS standalone editor REMOVED 2026-07-09 — a pack belongs to ONE specific service
  //  and is created/edited under it via Widgets.ServiceList -> the service editor's packagesCard.
  //  See docs/specs/FRONTEND-STANDARDISATION.md. AdminAPI.bundlePlans (GET) stays for issue-package.)

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

  // MEMBERSHIPS AS SERVICES — each membership (a TIER) is one service with term VARIANTS inside it
  // (Adult Anytime → 3 / 6 / 12 months). Summary card per membership → Edit opens the full editor
  // (terms + access hours). Same show-then-edit pattern as the Service Editor. -> /membership-plans.
  var memFilter = "active";
  function membershipServices(host) {
    init(); UI.clear(host);
    var card = el("div", { class: "cf-card" });
    card.appendChild(el("h2", { text: "Memberships" }));
    card.appendChild(el("p", { class: "cf-muted", text:
      "Each membership is a service with term options inside it (e.g. Adult Anytime → 3 / 6 / 12 months). " +
      "In the buy wizard members pick a membership, then a period." }));
    var listBox = el("div"); card.appendChild(listBox);
    var addBtn = el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", style: "margin-top:12px", text: "+ Add membership" });
    addBtn.addEventListener("click", function () { openTier(null); });
    card.appendChild(addBtn);
    host.appendChild(card);

    function term(m) { m = parseInt(m, 10) || 0; return m === 1 ? "1 month" : (m + " months"); }
    function perMonth(p) { var m = parseInt(p.term_months, 10) || 1; return Math.round((p.amount_minor || 0) / m); }
    function accessLabel(p) { return (!p.access_days && p.access_start_min == null && p.access_end_min == null) ? "Any time" : "Limited hours"; }
    function groupByTier(plans) {
      var map = {}, order = [];
      plans.forEach(function (p) { var k = p.tier || p.label || term(p.term_months); if (!map[k]) { map[k] = []; order.push(k); } map[k].push(p); });
      return order.map(function (k) { return { tier: k, plans: map[k].sort(function (a, b) { return (a.term_months || 0) - (b.term_months || 0); }) }; });
    }
    // A membership tier groups several term plans, each with a plan status (active|dormant|retired).
    // Surface the SAME lifecycle vocabulary as services/coaches: active→active, dormant→deactivated,
    // retired→terminated. A tier is active if ANY term is live, else deactivated if any dormant, else terminated.
    var _PLAN2LIFE = { active: "active", dormant: "deactivated", retired: "terminated" };
    var _LIFE2PLAN = { active: "active", deactivated: "dormant", terminated: "retired" };
    function tierLife(g) {
      var s = g.plans.map(function (p) { return _PLAN2LIFE[p.status || "active"] || "active"; });
      if (s.indexOf("active") >= 0) return "active";
      if (s.indexOf("deactivated") >= 0) return "deactivated";
      return "terminated";
    }

    function reload() {
      UI.clear(listBox); listBox.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
      window.AdminAPI.membershipPlans().then(function (r) {
        var groups = groupByTier(r.plans || []);
        UI.clear(listBox);
        listBox.appendChild(UI.lifecycleBar(memFilter, function (f) { memFilter = f; reload(); }));
        var shown = groups.filter(function (g) { return memFilter === "all" || tierLife(g) === memFilter; });
        if (!shown.length) { listBox.appendChild(el("div", { class: "cf-empty", text: "No " + (memFilter === "all" ? "" : memFilter + " ") + "memberships." })); return; }
        var list = el("div", { class: "cf-list" });
        shown.forEach(function (g) { list.appendChild(serviceRow(g)); });
        listBox.appendChild(list);
      }, function (e) { UI.clear(listBox); listBox.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    function serviceRow(g) {
      var life = tierLife(g);
      var minPm = Math.min.apply(null, g.plans.map(perMonth));
      var sub = g.plans.length + " term" + (g.plans.length > 1 ? "s" : "") + " · from " + UI.money(minPm) + "/mo · " + accessLabel(g.plans[0]);
      function setStatus(ns) { var ps = _LIFE2PLAN[ns] || "active"; Promise.all(g.plans.map(function (p) { return window.AdminAPI.patchMembershipPlan(p.price_id, { status: ps }).catch(function () {}); })).then(function () { UI.toast("Saved.", "info"); reload(); }); }
      var main = el("div", { class: "cf-item-main" }, [
        el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [el("span", { class: "cf-item-t", text: g.tier }), life !== "active" ? UI.statusChip(life) : null].filter(Boolean)),
        el("div", { class: "cf-item-s", text: sub }),
      ]);
      var actions = UI.lifeActions(life, setStatus, { terminateConfirm: "Terminate the " + g.tier + " membership? Kept for history, removed from sale." });
      actions.push(el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Delete", onclick: function (ev) { ev.stopPropagation(); delTier(g); } }));
      var row = el("div", { class: "cf-item cf-pickable" }, [
        el("span", { class: "cf-chip", text: "⭐" }), main, el("span", { class: "cf-spacer" }),
      ].concat(actions));
      row.addEventListener("click", function () { openTier(g); });
      if (life !== "active") row.style.opacity = "0.6";
      return row;
    }

    function delTier(g) {
      if (!window.confirm("Delete the " + g.tier + " membership and all its terms?")) return;
      Promise.all(g.plans.map(function (p) { return window.AdminAPI.deleteMembershipPlan(p.price_id).catch(function () {}); }))
        .then(function () { UI.toast("Deleted.", "info"); reload(); });
    }


    // The membership editor — a FULL-SCREEN view (not a popup): name + access hours + term variants,
    // with a single Save & close (changes batch in memory). Renders into `host`; Cancel/Save rebuild
    // the list via membershipServices(host).
    function openTier(g) {
      var m = {
        name: g ? g.tier : "",
        terms: (g ? g.plans : []).map(function (p) { return { price_id: p.price_id, term_months: p.term_months, amount_minor: p.amount_minor }; }),
        del: [],
        win: { days: (g && g.plans[0]) ? g.plans[0].access_days : null, start: (g && g.plans[0]) ? g.plans[0].access_start_min : null, end: (g && g.plans[0]) ? g.plans[0].access_end_min : null },
        modes: (g && g.plans[0] && g.plans[0].payment_modes) ? g.plans[0].payment_modes.slice() : null,  // null = inherit
        clubMethods: [],
      };
      // Need the club's enabled methods for the payment-options checkboxes; fetch then render.
      UI.clear(host); host.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
      window.TFAuth.apiJSON("/api/admin/membership-config").then(function (r) {
        m.clubMethods = r.club_payment_methods || [];
        renderEditor();
      }, function () { m.clubMethods = []; renderEditor(); });

      function renderEditor() {
        UI.clear(host);
        var saveB = el("button", { class: "cf-btn cf-btn-primary", text: "Save & close" });
        saveB.addEventListener("click", function () { save(saveB); });
        host.appendChild(el("div", { class: "cf-editbar" }, [
          el("button", { class: "cf-btn", text: "← Cancel", onclick: function () { membershipServices(host); } }),
          el("strong", { text: g ? "Edit membership" : "New membership" }),
          el("span", { class: "cf-spacer" }), saveB,
        ]));
        var nameI = input({ value: m.name, placeholder: "e.g. Adult Anytime", style: "max-width:360px;font-weight:700" });
        nameI.addEventListener("input", function () { m.name = nameI.value; });
        host.appendChild(el("div", { class: "cf-card" }, [el("h3", { text: "Details" }), field("Membership name", nameI)]));
        host.appendChild(accessCard());
        host.appendChild(paymentCard());
        host.appendChild(termsCard());
      }

      // Per-membership payment options. Inherits the membership default (then the club's global
      // methods) unless tailored here. Ticking a subset overrides for THIS membership only.
      function paymentCard() {
        var LABELS = { online: "Pay online (card)", at_court: "Pay at the club", monthly_account: "Monthly account" };
        var c = el("div", { class: "cf-card" }, [el("h3", { text: "Payment options" }),
          el("p", { class: "cf-muted cf-tiny", text: "How members pay for THIS membership. Leave all ticked to inherit the club default; untick to tailor. A single non-online option checks out immediately." })]);
        if (!m.clubMethods.length) { c.appendChild(el("div", { class: "cf-muted cf-tiny", text: "Enable payment methods on Club profile first." })); return c; }
        var checks = {};
        m.clubMethods.forEach(function (mode) {
          var lbl = el("label", { class: "cf-row", style: "gap:8px;align-items:center;cursor:pointer;margin-top:6px" });
          var cb = el("input", { type: "checkbox" }); cb.style.width = "auto";
          cb.checked = m.modes ? (m.modes.indexOf(mode) >= 0) : true;
          checks[mode] = cb;
          cb.addEventListener("change", function () {
            var sel = m.clubMethods.filter(function (x) { return checks[x].checked; });
            // all enabled selected → inherit (null); else the chosen subset.
            m.modes = (sel.length === m.clubMethods.length) ? null : sel;
          });
          lbl.appendChild(cb); lbl.appendChild(el("span", { text: LABELS[mode] || mode }));
          c.appendChild(lbl);
        });
        return c;
      }

      function accessCard() {
        var c = el("div", { class: "cf-card" }, [el("h3", { text: "Access hours" }), el("p", { class: "cf-muted cf-tiny", text: "When this membership makes courts free. All days + blank times = any time." })]);
        var sel = {}, cur = m.win.days;
        function syncDays() { var days = _DOW.filter(function (o) { return sel[o[0]]; }).map(function (o) { return parseInt(o[0], 10); }); m.win.days = (days.length === 0 || days.length === 7) ? null : days; }
        var chips = el("div", { class: "cf-row", style: "gap:4px;flex-wrap:wrap" });
        _DOW.forEach(function (o) { var on = !cur || cur.indexOf(parseInt(o[0], 10)) >= 0; sel[o[0]] = on; var b = el("button", { class: "cf-day" + (on ? " on" : ""), text: o[1], type: "button" }); b.addEventListener("click", function () { sel[o[0]] = !sel[o[0]]; b.className = "cf-day" + (sel[o[0]] ? " on" : ""); syncDays(); }); chips.appendChild(b); });
        var fromI = input({ type: "time", value: minToTime(m.win.start), style: "max-width:110px" }); fromI.addEventListener("input", function () { m.win.start = timeToMin(fromI.value); });
        var toI = input({ type: "time", value: minToTime(m.win.end), style: "max-width:110px" }); toI.addEventListener("input", function () { m.win.end = timeToMin(toI.value); });
        c.appendChild(chips);
        c.appendChild(el("div", { class: "cf-row", style: "gap:8px;align-items:center;margin-top:8px" }, [el("span", { class: "cf-muted", text: "from" }), fromI, el("span", { class: "cf-muted", text: "to" }), toI]));
        return c;
      }

      function termsCard() {
        var c = el("div", { class: "cf-card" }, [el("h3", { text: "Terms" })]);
        var list = el("div", { class: "cf-list" });
        m.terms.forEach(function (t) {
          var mI = input({ type: "number", min: 1, value: t.term_months || 1, style: "max-width:80px" }); mI.addEventListener("input", function () { t.term_months = parseInt(mI.value, 10) || null; });
          var pI = input({ value: (t.amount_minor / 100).toFixed(2), style: "max-width:120px" }); pI.addEventListener("input", function () { t.amount_minor = Math.round(parseFloat(pI.value || "0") * 100); });
          var rm = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Remove" });
          rm.addEventListener("click", function () { if (t.price_id) m.del.push(t.price_id); m.terms.splice(m.terms.indexOf(t), 1); renderEditor(); });
          list.appendChild(el("div", { class: "cf-item" }, [mI, el("span", { class: "cf-muted", text: "months → R" }), pI, el("span", { class: "cf-spacer" }), rm]));
        });
        if (!m.terms.length) list.appendChild(el("div", { class: "cf-empty", text: "No terms yet. Add one below." }));
        c.appendChild(list);
        c.appendChild(el("button", { class: "cf-btn cf-btn-sm", style: "margin-top:10px", text: "+ Add term", onclick: function () { m.terms.push({ term_months: 6, amount_minor: 0 }); renderEditor(); } }));
        return c;
      }

      async function save(btn) {
        var name = (m.name || "").trim();
        if (!name) { UI.toast("Name the membership.", "warn"); return; }
        if (!m.terms.length) { UI.toast("Add at least one term.", "warn"); return; }
        btn.disabled = true; btn.textContent = "Saving…";
        try {
          for (var i = 0; i < m.terms.length; i++) {
            var t = m.terms[i]; if (!t.term_months) continue;
            if (t.price_id) await window.AdminAPI.patchMembershipPlan(t.price_id, { tier: name, term_months: t.term_months, amount_minor: t.amount_minor || 0, set_window: true, access_days: m.win.days, access_start_min: m.win.start, access_end_min: m.win.end, set_modes: true, payment_modes: m.modes });
            else await window.AdminAPI.createMembershipPlan({ tier: name, term_months: t.term_months, amount_minor: t.amount_minor || 0, access_days: m.win.days, access_start_min: m.win.start, access_end_min: m.win.end, payment_modes: m.modes });
          }
          for (var d = 0; d < m.del.length; d++) await window.AdminAPI.deleteMembershipPlan(m.del[d]);
          UI.toast("Saved.", "info"); membershipServices(host);
        } catch (e) { btn.disabled = false; btn.textContent = "Save & close"; UI.toast(UI.errMsg(e) || "Couldn't save.", "error"); }
      }
    }

    reload();
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

  // COACHES (merged Coaches + Coach pay) — each coach is a summary row (click to edit), with
  // Hide/Delete. Edit opens a full-screen editor: details · rent · default commission. Per-service
  // commission lives on the service (the Service Editor). One place per coach.
  function coachManage(host) {
    init();
    var DATA = { agg: {}, coaches: [], filter: "active" };
    function aggFor(uid) { return (DATA.agg.coaches || []).filter(function (c) { return String(c.coach_user_id) === String(uid); })[0] || {}; }
    function coachName(c) { return c.display_name || ((c.first_name || "") + " " + (c.surname || "")).trim() || c.email || "Coach"; }
    function isPending(c) { return !!(c.invite_status && c.invite_status !== "accepted"); }

    function coachLife(c) {
      if ((c.member_status || "") === "lapsed") return "terminated";
      if (c.is_bookable === false) return "deactivated";
      return "active";
    }
    function renderList() {
      UI.clear(host);
      host.appendChild(el("div", { class: "cf-card" }, [el("h2", { text: "Coaches" }), el("p", { class: "cf-muted", text: "Your coaches, their rent and commission — one place. Click a coach to edit." })]));
      host.appendChild(clubDefaultCard());
      var listBox = el("div"); host.appendChild(listBox);
      host.appendChild(inviteCard());
      listBox.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
      Promise.all([window.AdminAPI.coaches(), window.AdminAPI.coachAgreements()]).then(function (res) {
        DATA.coaches = res[0].coaches || []; DATA.agg = res[1] || {};
        UI.clear(listBox);
        listBox.appendChild(UI.lifecycleBar(DATA.filter, function (f) { DATA.filter = f; renderList(); }));
        var shown = DATA.coaches.filter(function (c) { return DATA.filter === "all" || coachLife(c) === DATA.filter; });
        if (!shown.length) { listBox.appendChild(el("div", { class: "cf-empty", text: DATA.coaches.length ? ("No " + DATA.filter + " coaches.") : "No coaches yet. Invite one below." })); return; }
        var list = el("div", { class: "cf-list" }); shown.forEach(function (c) { list.appendChild(coachRow(c)); }); listBox.appendChild(list);
      }, function (e) { UI.clear(listBox); listBox.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    function clubDefaultCard() {
      var card = el("div", { class: "cf-card" });
      card.appendChild(el("h3", { text: "Club default commission" }));
      card.appendChild(el("p", { class: "cf-muted cf-tiny", text: "The % the club keeps on lessons by default. Override per coach (open a coach) or per service (the service editor)." }));
      var pctI = input({ type: "number", step: "0.5", min: 0, max: 100, value: (DATA.agg.club_default_pct != null ? DATA.agg.club_default_pct : 0), style: "max-width:110px" });
      var save = el("button", { class: "cf-btn cf-btn-sm", text: "Save default" });
      save.addEventListener("click", function () {
        var pct = parseFloat(pctI.value); if (isNaN(pct) || pct < 0 || pct > 100) { UI.toast("Enter 0–100.", "warn"); return; }
        window.AdminAPI.setCommissionRule({ commission_pct: pct }).then(function () { UI.toast("Saved.", "info"); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
      });
      card.appendChild(el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [pctI, el("span", { class: "cf-muted", text: "% the club keeps" }), save]));
      return card;
    }

    function coachRow(c) {
      var uid = c.user_id || c.id, ag = aggFor(uid), pending = isPending(c), life = coachLife(c);
      var subbits = [c.email || ""];
      if (ag.rent_minor) subbits.push("rent " + UI.money(ag.rent_minor));
      if (ag.coach_pct != null) subbits.push(ag.coach_pct + "% commission");
      if (pending) subbits.push("invite pending");
      function setStatus(ns) { window.AdminAPI.patchCoach(uid, { status: ns }).then(renderList, function (e) { UI.toast(UI.errMsg(e), "error"); }); }
      var actions = [];
      if (pending) actions.push(el("button", { class: "cf-btn cf-btn-sm", text: "Resend invite", onclick: function (ev) { ev.stopPropagation(); window.AdminAPI.resendCoachInvite(uid).then(function (r) { if (r && r.invite_link) { try { navigator.clipboard.writeText(r.invite_link); } catch (e) {} } UI.toast("Invite re-issued.", "info"); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); } }));
      actions = actions.concat(UI.lifeActions(life, setStatus, { terminateConfirm: "Terminate " + coachName(c) + "? They keep their history but can't be booked." }));
      actions.push(el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Delete", onclick: function (ev) { ev.stopPropagation(); if (window.confirm("Remove " + coachName(c) + " from the club?")) window.AdminAPI.removeCoach(uid).then(function (r) { UI.toast((r && r.outcome === "archived") ? "This coach has history, so they were archived (kept for reporting) rather than deleted." : "Coach deleted.", "info"); renderList(); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); } }));
      var row = el("div", { class: "cf-item cf-pickable" }, [
        el("span", { class: "cf-chip coach", text: "coach" }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [el("span", { class: "cf-item-t", text: coachName(c) }), life !== "active" ? UI.statusChip(life) : null].filter(Boolean)),
          el("div", { class: "cf-item-s", text: subbits.filter(Boolean).join(" · ") }),
        ]),
        el("span", { class: "cf-spacer" }),
      ].concat(actions));
      row.addEventListener("click", function () { openCoach(c, ag); });
      if (life !== "active") row.style.opacity = "0.6";
      return row;
    }

    function inviteCard() {
      var card = el("div", { class: "cf-card" }, [el("h3", { text: "Invite a coach" })]);
      var first = input({ placeholder: "First name", style: "max-width:140px" }), surname = input({ placeholder: "Surname", style: "max-width:140px" }), email = input({ placeholder: "Email", type: "email", style: "max-width:200px" });
      var invite = el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Invite" });
      invite.addEventListener("click", function () {
        var em = email.value.trim(); if (!em) { UI.toast("Email is required.", "warn"); return; }
        var display = (first.value.trim() + " " + surname.value.trim()).trim();
        window.AdminAPI.inviteCoach({ email: em, first_name: first.value.trim(), surname: surname.value.trim(), display_name: display || em }).then(function (r) { UI.toast("Invite sent.", "info"); if (r && r.invite_link) { try { navigator.clipboard.writeText(r.invite_link); } catch (e) {} } renderList(); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
      });
      card.appendChild(el("div", { class: "cf-row", style: "gap:6px;align-items:center;flex-wrap:wrap" }, [first, surname, email, invite]));
      return card;
    }

    function openCoach(c, ag) {
      var uid = c.user_id || c.id, pending = isPending(c);
      var m = { rent_minor: ag.rent_minor || 0, rent_day: ag.rent_day || 1, coach_pct: (ag.coach_pct != null ? ag.coach_pct : "") };
      render();
      function render() {
        UI.clear(host);
        var saveB = el("button", { class: "cf-btn cf-btn-primary", text: "Save & close" });
        saveB.addEventListener("click", function () { save(saveB); });
        host.appendChild(el("div", { class: "cf-editbar" }, [el("button", { class: "cf-btn", text: "← Cancel", onclick: renderList }), el("strong", { text: coachName(c) }), el("span", { class: "cf-spacer" }), saveB]));
        var det = el("div", { class: "cf-card" }, [el("h3", { text: "Details" }), el("div", { class: "cf-muted", text: (c.email || "") + (pending ? " · invite pending" : "") })]);
        if (pending) det.appendChild(el("button", { class: "cf-btn cf-btn-sm", style: "margin-top:10px", text: "Resend invite", onclick: function () { window.AdminAPI.resendCoachInvite(uid).then(function (r) { if (r && r.invite_link) { try { navigator.clipboard.writeText(r.invite_link); } catch (e) {} } UI.toast("Invite re-issued.", "info"); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); } }));
        host.appendChild(det);
        var rentI = input({ value: fromMinor(m.rent_minor), placeholder: "0.00", style: "max-width:120px" }); rentI.addEventListener("input", function () { m.rent_minor = toMinor(rentI.value) || 0; });
        var dayI = input({ type: "number", min: 1, max: 28, value: m.rent_day, style: "max-width:80px" }); dayI.addEventListener("input", function () { m.rent_day = num(dayI.value) || 1; });
        host.appendChild(el("div", { class: "cf-card" }, [el("h3", { text: "Monthly rent" }), el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [rentI, el("span", { class: "cf-muted", text: "on day" }), dayI])]));
        var pctI = input({ type: "number", min: 0, max: 100, value: m.coach_pct, placeholder: String(DATA.agg.club_default_pct || 0), style: "max-width:100px" }); pctI.addEventListener("input", function () { m.coach_pct = pctI.value; });
        host.appendChild(el("div", { class: "cf-card" }, [el("h3", { text: "Default commission" }), el("p", { class: "cf-muted cf-tiny", text: "The % the club keeps on all this coach's lessons. Blank = the club default (" + (DATA.agg.club_default_pct || 0) + "%). Per-service overrides live in the service editor." }), el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [pctI, el("span", { class: "cf-muted", text: "% to the club" })])]));
      }
      async function save(btn) {
        btn.disabled = true; btn.textContent = "Saving…";
        try {
          await window.AdminAPI.putCoachAgreement(uid, { rent_minor: m.rent_minor, rent_day: m.rent_day });
          if (m.coach_pct !== "" && m.coach_pct != null) { var v = parseFloat(m.coach_pct); if (!isNaN(v)) await window.AdminAPI.setCommissionRule({ coach_user_id: uid, commission_pct: Math.max(0, Math.min(100, v)) }); }
          UI.toast("Saved.", "info"); renderList();
        } catch (e) { btn.disabled = false; btn.textContent = "Save & close"; UI.toast(UI.errMsg(e) || "Couldn't save.", "error"); }
      }
    }

    renderList();
  }

  window.AdminUI = {
    clubProfile: clubProfile, hours: hours, courts: courts, courtsManage: courtsManage,
    coachManage: coachManage,
    services: services, coaches: coaches, membershipPlans: membershipPlans,
    membershipServices: membershipServices,
    coachAgreements: coachAgreements,
    courtRates: courtRates, pricingHome: pricingHome,
  };
})();

