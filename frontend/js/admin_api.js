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
    // PATCH /api/admin/clients/:id — edit a client's contact/details (whitelisted profile fields).
    updateClient: function (id, body) { return A().apiJSON("/api/admin/clients/" + enc(id), { method: "PATCH", body: body || {} }); },
    // POST /api/admin/members/:id/issue  body:{kind:'membership'|'pack', price_id?|bundle_plan_id?,
    //   start_date?, mark_paid?, pay_provider?} -> the purchase (owed order + activated; mark_paid settles).
    issuePackage: function (id, body) { return A().apiJSON("/api/admin/members/" + enc(id) + "/issue", { method: "POST", body: body || {} }); },
    // GET /api/services -> {services:[{id,name,service_kind,variations:[{price_id,duration_minutes,amount_minor}]}]}
    //   the configured services (+ their per-duration prices) — the invoice line picker draws from these.
    servicesList: function () { return A().apiJSON("/api/services"); },
    // POST /api/admin/clients/:id/invoice  body:{lines:[{price_id?|amount_minor,description?,qty?}], discount_minor?,
    //   reason?} -> {order_id, amount_minor, currency}. An ad-hoc OWED invoice (settleable online); emails the client.
    createInvoice: function (id, body) { return A().apiJSON("/api/admin/clients/" + enc(id) + "/invoice", { method: "POST", body: body || {} }); },
    // GET /api/admin/people/:user_id -> {person:{...profile,roles,is_coach,member_status,
    //   membership, statement:{items,total_owed_minor}, owed_minor, payments:[], upcoming:[],
    //   history:[], bookings_count, settlement?}}  — one record, drill-through to the event story.
    person: function (id, month) { return A().apiJSON("/api/admin/people/" + enc(id) + (month ? ("?month=" + enc(month)) : "")); },
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
    // The transaction record of a standalone purchase (pack/membership/invoice) — same shape as a booking story.
    orderRecord: function (orderId) { return A().apiJSON("/api/admin/orders/" + enc(orderId) + "/record"); },
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

    // ---- invoice a client's outstanding balance (intra-month statement invoice) ----
    // POST /api/admin/clients/<id>/statement-invoice  body: {due_date?, period?}
    statementInvoice: function (userId, body) {
      return A().apiJSON("/api/admin/clients/" + encodeURIComponent(userId) + "/statement-invoice", { method: "POST", body: body || {} });
    },

    // ---- billing profile (company & bank details for invoices/receipts) --
    // GET /api/admin/billing-profile -> {billing_profile:{...}}
    billingProfile: function () { return A().apiJSON("/api/admin/billing-profile"); },
    // PATCH /api/admin/billing-profile  body: {registered_name,company_reg_no,vat_number,
    //   bank_name,bank_account_name,bank_account_number,bank_branch_code,bank_swift,
    //   billing_email,billing_phone,invoice_prefix,invoice_terms,invoice_footer}
    patchBillingProfile: function (body) {
      return A().apiJSON("/api/admin/billing-profile", { method: "PATCH", body: body });
    },

    // ---- promotions (specials + promo codes) ----------------------------
    promotions: function () { return A().apiJSON("/api/admin/promotions"); },
    createPromotion: function (body) { return A().apiJSON("/api/admin/promotions", { method: "POST", body: body || {} }); },
    updatePromotion: function (id, body) { return A().apiJSON("/api/admin/promotions/" + enc(id), { method: "PATCH", body: body || {} }); },
    setPromotionStatus: function (id, status) { return A().apiJSON("/api/admin/promotions/" + enc(id) + "/status", { method: "POST", body: { status: status } }); },
    promotionRedemptions: function (id) { return A().apiJSON("/api/admin/promotions/" + enc(id) + "/redemptions"); },
    promotionCodes: function (id) { return A().apiJSON("/api/admin/promotions/" + enc(id) + "/codes"); },
    generatePromotionCodes: function (id, body) { return A().apiJSON("/api/admin/promotions/" + enc(id) + "/codes", { method: "POST", body: body || {} }); },
    revokePromotionCode: function (code) { return A().apiJSON("/api/admin/promotions/codes/revoke", { method: "POST", body: { code: code } }); },

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

    // GET /api/admin/financials/coach-statement?month=YYYY-MM -> month-end close-out: coach → client →
    // service, each row carrying how it was paid and whether that money reached the club's bank.
    coachStatement: function (month) {
      return A().apiJSON("/api/admin/financials/coach-statement" + (month ? "?month=" + enc(month) : ""));
    },
    // GET /api/admin/coach-statement?coach_user_id=&month= -> ONE coach's settlement statement:
    // by_client + sessions (the work log, by day) + settlement (the money math) + ledger_detail.
    coachSettlementStatement: function (coachUserId, month) {
      var q = [];
      if (coachUserId) q.push("coach_user_id=" + enc(coachUserId));
      if (month) q.push("month=" + enc(month));
      return A().apiJSON("/api/admin/coach-statement" + (q.length ? "?" + q.join("&") : ""));
    },

    // ---- equipment hire (ball machine / racquets / balls) ----------------
    equipment: function () { return A().apiJSON("/api/admin/equipment"); },
    createEquipment: function (body) { return A().apiJSON("/api/admin/equipment", { method: "POST", body: body }); },
    patchEquipment: function (id, body) { return A().apiJSON("/api/admin/equipment/" + enc(id), { method: "PATCH", body: body }); },
    deleteEquipment: function (id) { return A().apiJSON("/api/admin/equipment/" + enc(id), { method: "DELETE" }); },

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
    // PATCH /api/admin/classes/sessions/:session_id -- MOVE a session (time/duration/coach/courts)
    rescheduleClassSession: function (sessionId, body) {
      return A().apiJSON("/api/admin/classes/sessions/" + enc(sessionId),
        { method: "PATCH", body: body || {} });
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
    // GET /api/admin/financials/earnings-by-service?month=YYYY-MM -> {month, currency,
    //   summary:{billed_minor,collected_minor,outstanding_minor,club_keeps_minor,
    //            coach_payouts_due_minor,total_owed_now_minor,active_members,mrr_minor},
    //   services:[{key,label,billed_minor,collected_minor,outstanding_minor,count}]}
    //   + services:[{key,label,...fold}], clients:[{user_id,name,...fold}]
    earningsByService: function (month) { return A().apiJSON("/api/admin/financials/earnings-by-service" + (month ? ("?month=" + month) : "")); },
    // GET /api/admin/financials/revenue-club?month= -> {direct:[{key,label,...fold}], coaches:[{coach_user_id,
    //   name,sales_minor,net_minor,received_minor,owed_minor,club_comm_*,coach_keeps_*}], club:{...roll-up}}
    revenueClub: function (month) { return A().apiJSON("/api/admin/financials/revenue-club" + (month ? ("?month=" + encodeURIComponent(month)) : "")); },
    // GET /api/admin/financials/revenue-coach/:coach_user_id?month= -> ONE coach P&L object.
    revenueCoach: function (coachUserId, month) { return A().apiJSON("/api/admin/financials/revenue-coach/" + encodeURIComponent(coachUserId) + (month ? ("?month=" + encodeURIComponent(month)) : "")); },
    // GET /api/admin/financials/revenue-clients?category=&earned_by=&month= -> {clients:[{user_id,name,
    //   ...fold}], totals, label} — a service (+ optional coach/'club') split by client.
    earningsClients: function (opts) {
      opts = opts || {};
      var q = [];
      if (opts.category) q.push("category=" + encodeURIComponent(opts.category));
      if (opts.earned_by) q.push("earned_by=" + encodeURIComponent(opts.earned_by));
      if (opts.month) q.push("month=" + encodeURIComponent(opts.month));
      return A().apiJSON("/api/admin/financials/revenue-clients" + (q.length ? ("?" + q.join("&")) : ""));
    },
    // GET /api/admin/financials/transactions?category=&user_id=&earned_by=&month= -> {transactions:[{order_id,
    //   booking_id,enrolment_id,user_id,client_name,label,category,description,at,billed_minor,state}], totals}
    earningsTransactions: function (opts) {
      opts = opts || {};
      var q = [];
      if (opts.category) q.push("category=" + encodeURIComponent(opts.category));
      if (opts.user_id) q.push("user_id=" + encodeURIComponent(opts.user_id));
      if (opts.earned_by) q.push("earned_by=" + encodeURIComponent(opts.earned_by));
      if (opts.month) q.push("month=" + encodeURIComponent(opts.month));
      return A().apiJSON("/api/admin/financials/transactions" + (q.length ? ("?" + q.join("&")) : ""));
    },

    // ---- club <-> coach settlement (payouts + aging) ---------------------
    // GET /api/admin/financials/settlement -> {clients:[{user_id,name,owed_minor,age_days,bucket}],
    //   client_totals:{"0-30","31-60","61+"}, coaches:[{coach_user_id,name,balance_minor}], total_owed_minor}
    settlementOverview: function () { return A().apiJSON("/api/admin/financials/settlement"); },
    // GET /api/admin/coach-payouts[?coach_user_id=] -> {payouts:[...]}
    coachPayouts: function (opts) { return A().apiJSON("/api/admin/coach-payouts" + qs(opts)); },
    // POST /api/admin/coach-payouts {coach_user_id,amount_minor,direction,method?,reference?,note?,status?}
    recordCoachPayout: function (body) { return A().apiJSON("/api/admin/coach-payouts", { method: "POST", body: body }); },
    // PATCH /api/admin/coach-payouts/:id {status:'paid'|'void'}
    setCoachPayout: function (id, body) { return A().apiJSON("/api/admin/coach-payouts/" + enc(id), { method: "PATCH", body: body }); },

    // ---- insights (Phase 2 P1 read-layer) --------------------------------
    // GET /api/insights/court-utilisation?days= -> {days, overall_pct, booked_hours,
    //   available_hours, cells:[{weekday,hour,booked_hours,available_hours,pct}]}
    courtUtilisation: function (days) { return A().apiJSON("/api/insights/court-utilisation" + (days ? ("?days=" + days) : "")); },
    // GET /api/insights/web-metrics -> latest Google snapshot {connected,as_of,ga4:{totals,channels,
    //   top_pages,geo,conversions},gsc:{totals,top_queries,striking}} (fed by the marketing-digest ingest).
    webMetrics: function () { return A().apiJSON("/api/insights/web-metrics"); },
    // GET /api/insights/trial-cohorts?months= -> {months, cohorts:[{month,started,conv_14,conv_30,
    //   conv_ever,rate_14,rate_30,rate_ever}]} — trial→paid conversion by start-month cohort.
    trialCohorts: function (months) { return A().apiJSON("/api/insights/trial-cohorts" + (months ? ("?months=" + months) : "")); },
    // GET /api/insights/overview?month=YYYY-MM -> {month, currency, days:[iso], series:{visits,
    //   unique_visitors,bookings,bookings_court,bookings_lesson,bookings_class,member_bookings,
    //   revenue_gross_minor,revenue_net_minor,refunded_minor,new_clients,active_members,nps_responses,
    //   tier_series:{tier:[..]},members_joined,members_cancelled,trials_started,trials_lapsed,
    //   logged_in_new,logged_in_returning},
    //   kpis:{...,tier_current:{tier:n},payg_active,trials_active,trials_converted,trials_total,
    //   trial_conversion_rate,trials_started_month,members_joined,members_cancelled},
    //   breakdowns:{sources,top_pages,by_device}}
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

    // ---- community: Find a Game + the seat rule (community/routes.py) -------
    // GET -> {community_enabled, seat_rule_enforced, open_game_cutoff_hours, seat_pay_hours,
    //         guest_trial_days, seats_by_format, open_games, live_invites, discoverable_players,
    //         unpaid_seats_minor}
    communitySettings: function () { return A().apiJSON("/api/community/admin/settings"); },
    // PATCH — the ONE place the seat rule is switched on. Until this existed the flags could only be
    // changed with SQL, which is how the entitlement caps ended up shipped-but-inert for weeks.
    saveCommunitySettings: function (body) {
      return A().apiJSON("/api/community/admin/settings", { method: "PATCH", body: body || {} });
    },
    // EVERY seated game, not just the ones with a seat free — plus what is still owed on each.
    communityGames: function (days) {
      return A().apiJSON("/api/community/admin/games" + (days ? "?days=" + enc(days) : ""));
    },
    communityInvites: function () { return A().apiJSON("/api/community/admin/invites"); },
    revokeCommunityInvite: function (id) {
      return A().apiJSON("/api/community/admin/invites/" + enc(id) + "/revoke",
        { method: "POST", body: {} });
    },
    communityPlayers: function (q) {
      return A().apiJSON("/api/community/admin/players" + (q ? "?q=" + enc(q) : ""));
    },
    setPlayerLevel: function (userId, level) {
      return A().apiJSON("/api/community/admin/players/" + enc(userId) + "/level",
        { method: "PATCH", body: { level_num: level } });
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

  // THE payment-options card — one renderer for every service that can restrict how it's paid for
  // (memberships, equipment hire, …). `model` carries {modes, clubMethods}; ticking every enabled
  // method means INHERIT (null), any subset overrides for this service alone. Extracted rather than
  // copied when equipment gained payment options: a second copy is exactly how two surfaces drift
  // into disagreeing about what "all ticked" means.
  var PAY_MODE_LABELS = { online: "Pay online (card)", at_court: "Pay at the club",
                          monthly_account: "Monthly account" };

  function paymentOptionsCard(model, blurb) {
    var c = el("div", { class: "cf-card" }, [el("h3", { text: "Payment options" }),
      el("p", { class: "cf-muted cf-tiny", text: blurb })]);
    var methods = model.clubMethods || [];
    if (!methods.length) {
      c.appendChild(el("div", { class: "cf-muted cf-tiny",
                                text: "Enable payment methods on Club profile first." }));
      return c;
    }
    var checks = {};
    methods.forEach(function (mode) {
      var lbl = el("label", { class: "cf-row", style: "gap:8px;align-items:center;cursor:pointer;margin-top:6px" });
      var cb = el("input", { type: "checkbox" }); cb.style.width = "auto";
      cb.checked = model.modes ? (model.modes.indexOf(mode) >= 0) : true;
      checks[mode] = cb;
      cb.addEventListener("change", function () {
        var sel = methods.filter(function (x) { return checks[x].checked; });
        model.modes = (sel.length === methods.length) ? null : sel;   // all → inherit
      });
      lbl.appendChild(cb); lbl.appendChild(el("span", { text: PAY_MODE_LABELS[mode] || mode }));
      c.appendChild(lbl);
    });
    return c;
  }
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
  // COMPANY & BILLING DETAILS — the invoice/receipt letterhead identity + bank
  // details for EFT-payable invoices. -> GET/PATCH /api/admin/billing-profile.
  // Self-loads if `data.billing_profile` isn't supplied. club_admin+ only (server-gated).
  // ---------------------------------------------------------------------------
  function billingDetails(host, data, opts) {
    init(); opts = opts || {};
    function textarea(o) { return el("textarea", Object.assign({ class: "cf-input", rows: "2" }, o || {})); }

    function draw(bp) {
      bp = bp || {};
      var f = {
        registered_name: input({ value: bp.registered_name || "", placeholder: "Registered / legal company name" }),
        company_reg_no: input({ value: bp.company_reg_no || "", placeholder: "e.g. 2019/123456/07" }),
        vat_number: input({ value: bp.vat_number || "", placeholder: "Leave blank if not VAT-registered" }),
        billing_email: input({ value: bp.billing_email || "", placeholder: "Accounts / billing email", type: "email" }),
        billing_phone: input({ value: bp.billing_phone || "", placeholder: "Billing phone", type: "tel" }),
        bank_name: input({ value: bp.bank_name || "", placeholder: "e.g. FNB" }),
        bank_account_name: input({ value: bp.bank_account_name || "", placeholder: "Account holder" }),
        bank_account_number: input({ value: bp.bank_account_number || "", placeholder: "Account number" }),
        bank_branch_code: input({ value: bp.bank_branch_code || "", placeholder: "Branch / routing code" }),
        bank_swift: input({ value: bp.bank_swift || "", placeholder: "SWIFT / BIC (optional)" }),
        invoice_prefix: input({ value: bp.invoice_prefix || "INV-", placeholder: "INV-" }),
        invoice_terms: textarea({ placeholder: "e.g. Payment due within 7 days of invoice date." }),
        invoice_footer: textarea({ placeholder: "Optional footer / thank-you note shown on every invoice." }),
      };
      f.invoice_terms.value = bp.invoice_terms || "";
      f.invoice_footer.value = bp.invoice_footer || "";

      var company = el("div", { class: "cf-card" }, [
        el("h2", { text: "Company details" }),
        el("p", { class: "cf-muted", text: "Shown as the letterhead on every invoice and receipt. Your logo (Branding) and address (Club profile) appear too." }),
        field("Registered company name", f.registered_name),
        el("div", { class: "cf-grid cf-grid-2" }, [
          field("Company reg. no.", f.company_reg_no),
          field("VAT number", f.vat_number)]),
        el("div", { class: "cf-grid cf-grid-2" }, [
          field("Billing email", f.billing_email),
          field("Billing phone", f.billing_phone)]),
      ]);
      var bank = el("div", { class: "cf-card" }, [
        el("h2", { text: "Bank details" }),
        el("p", { class: "cf-muted", text: "Printed on unpaid invoices so clients can pay by EFT (the invoice number is the reference)." }),
        el("div", { class: "cf-grid cf-grid-2" }, [
          field("Bank", f.bank_name), field("Account name", f.bank_account_name)]),
        el("div", { class: "cf-grid cf-grid-2" }, [
          field("Account number", f.bank_account_number), field("Branch code", f.bank_branch_code)]),
        field("SWIFT / BIC", f.bank_swift),
      ]);
      var invoice = el("div", { class: "cf-card" }, [
        el("h2", { text: "Invoice options" }),
        field("Invoice number prefix", f.invoice_prefix),
        field("Payment terms", f.invoice_terms),
        field("Footer note", f.invoice_footer),
      ]);

      var btn = el("button", { class: "cf-btn cf-btn-primary", text: opts.saveLabel || "Save billing details" });
      invoice.appendChild(actionRow((opts.before || []).concat([btn])));
      btn.addEventListener("click", async function () {
        btn.disabled = true; btn.textContent = "Saving…";
        try {
          await window.AdminAPI.patchBillingProfile({
            registered_name: f.registered_name.value.trim(),
            company_reg_no: f.company_reg_no.value.trim(),
            vat_number: f.vat_number.value.trim(),
            billing_email: f.billing_email.value.trim(),
            billing_phone: f.billing_phone.value.trim(),
            bank_name: f.bank_name.value.trim(),
            bank_account_name: f.bank_account_name.value.trim(),
            bank_account_number: f.bank_account_number.value.trim(),
            bank_branch_code: f.bank_branch_code.value.trim(),
            bank_swift: f.bank_swift.value.trim(),
            invoice_prefix: f.invoice_prefix.value.trim() || "INV-",
            invoice_terms: f.invoice_terms.value.trim(),
            invoice_footer: f.invoice_footer.value.trim(),
          });
          UI.toast("Billing details saved.", "info");
          if (typeof opts.onSaved === "function") opts.onSaved();
        } catch (e) {
          UI.toast(UI.errMsg(e), "error");
        } finally { btn.disabled = false; btn.textContent = opts.saveLabel || "Save billing details"; }
      });

      UI.clear(host);
      host.appendChild(company); host.appendChild(bank); host.appendChild(invoice);
    }

    if (data && data.billing_profile) { draw(data.billing_profile); return; }
    UI.clear(host); host.appendChild(el("p", { class: "cf-muted", text: "Loading…" }));
    window.AdminAPI.billingProfile()
      .then(function (r) { draw((r && r.billing_profile) || {}); })
      .catch(function (e) { UI.clear(host); host.appendChild(el("p", { class: "cf-muted", text: UI.errMsg(e) })); });
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

    // "" when the court just follows the club window (the common case — no noise on every row).
    function peakSummary(c) {
      if (!c.peak_override) return "";
      function t(x) { return ("0" + Math.floor(x / 60)).slice(-2) + ":" + ("0" + (x % 60)).slice(-2); }
      var DAY = ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
      function one(days, a, b) {
        var d = (days != null && days !== "")
          ? String(days).split(",").map(function (x) { return DAY[parseInt(x, 10)] || ""; }).filter(Boolean).join("/")
          : "";
        return (d ? d + " " : "") + t(a) + "–" + t(b);
      }
      // Windows first; a court still on the legacy column falls back to it, exactly as the resolver
      // does — the row must say what is actually in force, not what the newer table happens to hold.
      var ws = (c.peak_windows || []).filter(function (w) { return w.start_min != null && w.end_min != null; });
      if (ws.length) {
        return "peak " + ws.map(function (w) { return one(w.days, w.start_min, w.end_min); }).join(" · ");
      }
      if (c.peak_start_min == null || c.peak_end_min == null) return "no peak";
      return "peak " + one(c.peak_days, c.peak_start_min, c.peak_end_min);
    }

    function courtRow(c) {
      var row = el("div", { class: "cf-item cf-pickable" }, [
        el("span", { class: "cf-chip court", text: "court" }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: c.name || "Court" }),
          // Peak is per COURT now, so the list says when a court differs from the club — otherwise
          // "peak on the show courts only" is invisible until you open all eight of them.
          el("div", { class: "cf-item-s", text: [(c.surface || "hard"), svcName(c.product_id),
                                                 peakSummary(c), hoursSummary(c)].filter(Boolean).join(" · ") }),
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
      var m = { name: c.name || "", surface: c.surface || "hard", product_id: c.product_id || "", rows: [],
                // PER-COURT peak. override=false inherits the club window; override=true means THIS
                // court's window is the answer — and an EMPTY one marks it never-peak.
                peakOverride: !!c.peak_override,
                // Windows come from diary.peak_window. A court that has NONE but still carries the
                // old single-column window is shown that window as its first row — the resolver
                // falls back to it, so the screen must show what is actually in force, and saving
                // then moves the court onto rows without the owner having to notice the migration.
                peakWindows: (function () {
                  var ws = (c.peak_windows || []).map(function (w) {
                    return { days: (w.days != null && w.days !== "")
                               ? String(w.days).split(",").map(function (x) { return x.trim(); }).filter(Boolean) : null,
                             start: w.start_min, end: w.end_min };
                  });
                  if (ws.length) return ws;
                  if (c.peak_start_min == null && c.peak_end_min == null) return [];
                  return [{ days: (c.peak_days != null && c.peak_days !== "")
                              ? String(c.peak_days).split(",").map(function (x) { return x.trim(); }).filter(Boolean) : null,
                            start: c.peak_start_min, end: c.peak_end_min }];
                })() };
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

        host.appendChild(peakCard());
        host.appendChild(serviceCard());

        // WHAT THIS COURT COSTS — a read-only summary of the court SERVICE it belongs to, so the
        // court is one place to SEE everything about it, with one tap to change any of it.
        //
        // Deliberately NOT editable here, and deliberately not duplicated. Price, payment methods,
        // membership cover and packs belong to the SERVICE, which several courts share — eight hard
        // courts are one price list. Editing them per court would either fork the model or quietly
        // mean "edit this for all eight", which is worse than sending you to the place that says so.
        // The button opens THE service editor (window.ServiceEditor — the same widget Setup →
        // Services uses) right here, and returns to this court on close.
        function serviceCard() {
          // `card0`, not `c` — `c` is openCourt's court argument and shadowing it here would break
          // the "back to this court" return below.
          var card0 = el("div", { class: "cf-card" }, [el("h3", { text: "Pricing & payment" })]);
          var svcId = m.product_id
            || ((DATA.courtServices || []).length === 1 ? DATA.courtServices[0].id : null);
          if (!svcId) {
            card0.appendChild(el("div", { class: "cf-muted cf-tiny", text:
              "This court isn't allocated to a court service yet, so it has no price list. Pick one "
              + "under Details above, or create one in Setup → Services." }));
            return card0;
          }
          var body = el("div"); card0.appendChild(body);
          body.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
          window.TFAuth.apiJSON("/api/services/" + encodeURIComponent(svcId)).then(function (r) {
            var svc = (r && r.service) || {};
            UI.clear(body);
            body.appendChild(el("p", { class: "cf-muted cf-tiny", text:
              "From the court service “" + (svc.name || "Court hire") + "”, shared by every court on "
              + "it. Change it once here and it applies to all of them." }));
            var list = el("div", { class: "cf-list" });
            (svc.variations || []).forEach(function (v) {
              var peak = (v.peak_amount_minor != null)
                ? "  ·  peak " + UI.money(v.peak_amount_minor, svc.currency) : "";
              list.appendChild(el("div", { class: "cf-item" }, [
                el("div", { text: (v.duration_minutes ? v.duration_minutes + " min" : "Per booking") }),
                el("div", { style: "font-weight:700;text-align:right", text: UI.money(v.amount_minor, svc.currency) + peak }),
              ]));
            });
            if (!(svc.variations || []).length) {
              list.appendChild(el("div", { class: "cf-empty", text: "No prices set — this court can't be booked until one is." }));
            }
            body.appendChild(list);
            function line(k, v) {
              return el("div", { class: "cf-row", style: "justify-content:space-between;margin-top:8px" }, [
                el("span", { class: "cf-muted", style: "font-size:.88rem", text: k }),
                el("span", { style: "font-weight:600;font-size:.88rem", text: v }),
              ]);
            }
            var modes = svc.payment_modes && svc.payment_modes.length
              ? svc.payment_modes.map(function (x) { return PAY_MODE_LABELS[x] || x; }).join(", ")
              : "Every method the club accepts";
            body.appendChild(line("Payment", modes));
            body.appendChild(line("Members & trialists",
              svc.members_covered === false ? "Pay for this court" : "Book it free"));
            if ((svc.packages || []).length) {
              body.appendChild(line("Packs", svc.packages.length + " on this service"));
            }
            var b = el("button", { class: "cf-btn cf-btn-sm", style: "margin-top:12px",
                                   text: "Edit " + (svc.name || "service") + " →" });
            b.addEventListener("click", function () {
              // THE service editor, mounted here; closing returns to this court.
              window.ServiceEditor.open(svcId, { host: host, onClose: function () { openCourt(c); } });
            });
            body.appendChild(b);
          }, function (e) {
            UI.clear(body);
            body.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) }));
          });
          return card0;
        }

        // Peak hours for THIS court. The peak AMOUNT stays on the court's service (Setup → Services →
        // per duration); this only decides WHEN that amount applies here — which is what makes "peak
        // on the show courts only" possible. Three states, and the third is the reason the override
        // flag exists: inherit the club window · this court's own window · this court never peaks.
        function peakCard() {
          var DOW = [["1", "Mon"], ["2", "Tue"], ["3", "Wed"], ["4", "Thu"], ["5", "Fri"], ["6", "Sat"], ["7", "Sun"]];
          function m2t(x) { if (x == null || x === "") return ""; x = parseInt(x, 10); return ("0" + Math.floor(x / 60)).slice(-2) + ":" + ("0" + (x % 60)).slice(-2); }
          function t2m(v) { if (!v) return null; var q = String(v).split(":"); return (parseInt(q[0], 10) || 0) * 60 + (parseInt(q[1], 10) || 0); }
          var c2 = el("div", { class: "cf-card" }, [
            el("h3", { text: "Peak hours for this court" }),
            el("p", { class: "cf-muted cf-tiny", text: "When this court charges its service's PEAK price. The amount itself is set per duration under Setup → Services." }),
          ]);
          var ov = el("input", { type: "checkbox" }); ov.style.width = "auto"; ov.checked = !!m.peakOverride;
          ov.addEventListener("change", function () { m.peakOverride = ov.checked; render(); });
          c2.appendChild(el("label", { class: "cf-row", style: "gap:8px;align-items:center;cursor:pointer;margin-top:6px" },
            [ov, el("span", { style: "font-weight:600", text: "Set peak hours just for this court" })]));
          if (!m.peakOverride) {
            c2.appendChild(el("div", { class: "cf-muted cf-tiny", style: "margin-top:6px", text: "Currently follows the club-wide peak hours (Setup → Club profile & payments)." }));
            return c2;
          }
          // N WINDOWS, not one. A real club's peak is not a single rule: NextPoint's is weekday
          // EVENINGS (Mon–Thu 17:00–19:00) AND Saturday MORNING (08:00–10:00). With one row an owner
          // had to pick which half of their peak to charge for, and the screen looked correctly
          // configured while doing it.
          var rows = el("div", { style: "margin-top:8px" });
          function windowRow(w, idx) {
            var sel = {};
            var chips = el("div", { class: "cf-row", style: "gap:4px;flex-wrap:wrap" });
            DOW.forEach(function (o) {
              var on = w.days ? w.days.indexOf(o[0]) >= 0 : false;
              sel[o[0]] = on;
              var b = el("button", { class: "cf-day" + (on ? " on" : ""), text: o[1], type: "button" });
              b.addEventListener("click", function () {
                sel[o[0]] = !sel[o[0]]; b.className = "cf-day" + (sel[o[0]] ? " on" : "");
                w.days = DOW.filter(function (x) { return sel[x[0]]; }).map(function (x) { return x[0]; });
              });
              chips.appendChild(b);
            });
            var pf = el("input", { class: "cf-input", type: "time", value: m2t(w.start), style: "max-width:110px" });
            var pt = el("input", { class: "cf-input", type: "time", value: m2t(w.end), style: "max-width:110px" });
            pf.addEventListener("input", function () { w.start = t2m(pf.value); });
            pt.addEventListener("input", function () { w.end = t2m(pt.value); });
            var rm = el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", type: "button", text: "Remove" });
            rm.addEventListener("click", function () {
              m.peakWindows.splice(idx, 1); render();
            });
            return el("div", { class: "cf-row", style: "gap:8px;align-items:center;flex-wrap:wrap;margin-top:6px" }, [
              el("span", { class: "cf-muted cf-tiny", text: "Peak on:" }), chips,
              el("span", { class: "cf-muted cf-tiny", text: "from" }), pf,
              el("span", { class: "cf-muted cf-tiny", text: "to" }), pt, rm,
            ]);
          }
          (m.peakWindows || []).forEach(function (w, i) { rows.appendChild(windowRow(w, i)); });
          if (!(m.peakWindows || []).length) {
            rows.appendChild(el("div", { class: "cf-muted cf-tiny", text: "No peak hours on this court — it always charges the base price." }));
          }
          c2.appendChild(rows);
          var add = el("button", { class: "cf-btn cf-btn-sm", type: "button", style: "margin-top:8px",
            text: "+ Add peak window" });
          add.addEventListener("click", function () {
            m.peakWindows = (m.peakWindows || []).concat([{ days: null, start: null, end: null }]);
            render();
          });
          c2.appendChild(add);
          c2.appendChild(el("div", { class: "cf-muted cf-tiny", style: "margin-top:8px", text:
            (m.peakDays && m.peakDays.length && m.peakStart != null && m.peakEnd != null)
              ? "This court charges peak on the days and hours above."
              : "Leave the days or times empty and this court NEVER charges peak — even when the club does." }));
          return c2;
        }

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
          // peak_windows is the source of truth now. The legacy single columns are CLEARED on every
          // save so a court can never end up with both — the resolver prefers rows and would ignore
          // the columns, and a stale column left behind is a value that looks live and isn't.
          var wins = (m.peakOverride ? (m.peakWindows || []) : [])
            .filter(function (w) { return w.start != null && w.end != null && w.end > w.start; })
            .map(function (w) {
              return { days: (w.days && w.days.length && w.days.length < 7) ? w.days.map(Number) : null,
                       start_min: w.start, end_min: w.end };
            });
          var body = { name: name, surface: m.surface, product_id: m.product_id || null,
            peak_override: !!m.peakOverride,
            peak_windows: wins,
            peak_days: null, peak_start_min: null, peak_end_min: null };
          await window.AdminAPI.patchResource(c.id, body);
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
        // Silent anti-abuse caps (null = no cap) + the signup-trial config.
        limits: { minutes: (g && g.plans[0]) ? g.plans[0].max_covered_minutes : null, perDay: (g && g.plans[0]) ? g.plans[0].max_covered_per_day : null, courtsDay: (g && g.plans[0]) ? g.plans[0].max_courts_per_day : null },
        trial: { on: !!(g && g.plans[0] && g.plans[0].is_trial), days: (g && g.plans[0] && g.plans[0].trial_days != null) ? g.plans[0].trial_days : null },
        // Free at peak? Defaults TRUE for a tier that predates the flag and for a brand-new one, so
        // adding this never silently started charging anybody.
        coversPeak: (g && g.plans[0] && g.plans[0].covers_peak === false) ? false : true,
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
        host.appendChild(limitsCard());
        host.appendChild(peakCoverCard());
        host.appendChild(trialCard());
        host.appendChild(paymentCard());
        host.appendChild(termsCard());
      }

      // Silent anti-abuse caps. Blank = no cap. Exceeding any -> that booking is PAYG (never blocked);
      // over-length durations are simply hidden from the member so it's never felt.
      function limitsCard() {
        var c = el("div", { class: "cf-card" }, [el("h3", { text: "Member limits" }),
          el("p", { class: "cf-muted cf-tiny", text: "Caps on what this membership covers for free. Leave blank for no cap. Beyond a cap the member simply pays as normal — they're never blocked." })]);
        function numRow(label, key, hint) {
          var i = input({ type: "number", min: 0, value: (m.limits[key] != null ? m.limits[key] : ""), placeholder: "no cap", style: "max-width:120px" });
          i.addEventListener("input", function () { var t = i.value.trim(); m.limits[key] = (t === "" ? null : (parseInt(t, 10) || 0)); });
          return el("div", { class: "cf-field" }, [el("label", { text: label }), i, hint ? el("div", { class: "cf-pref-note", text: hint }) : null].filter(Boolean));
        }
        c.appendChild(numRow("Max minutes per booking", "minutes", "e.g. 90 — longer durations are hidden for members."));
        c.appendChild(numRow("Max covered bookings per day", "perDay", "How many free courts a member can book in a day."));
        c.appendChild(numRow("Max courts per day", "courtsDay", "Distinct courts per day — stops one member holding several."));
        return c;
      }

      // Configurable signup trial (Feature 3): mark THIS tier as the free trial granted to a brand-new
      // member, scaled to N days (0 = trials off). A trial member inherits every limit above.
      function trialCard() {
        var c = el("div", { class: "cf-card" }, [el("h3", { text: "Signup trial" }),
          el("p", { class: "cf-muted cf-tiny", text: "Make this the free trial a brand-new member gets on their first login. Only one tier can be the trial. All the limits above apply to trial members too." })]);
        var cb = el("input", { type: "checkbox" }); cb.style.width = "auto"; cb.checked = !!m.trial.on;
        var daysWrap = el("div", { class: "cf-field", style: m.trial.on ? "" : "display:none" });
        var daysI = input({ type: "number", min: 0, value: (m.trial.days != null ? m.trial.days : 7), style: "max-width:100px" });
        daysI.addEventListener("input", function () { var t = daysI.value.trim(); m.trial.days = (t === "" ? null : (parseInt(t, 10) || 0)); });
        daysWrap.appendChild(el("label", { text: "Trial length (days)" })); daysWrap.appendChild(daysI);
        cb.addEventListener("change", function () { m.trial.on = cb.checked; if (cb.checked && m.trial.days == null) m.trial.days = 7; daysWrap.style.display = cb.checked ? "" : "none"; });
        c.appendChild(el("label", { class: "cf-row", style: "gap:10px;align-items:center;cursor:pointer;margin-top:6px" }, [cb, el("span", { style: "font-weight:600", text: "This tier is the signup trial" })]));
        c.appendChild(daysWrap);
        return c;
      }

      // "Free except at peak" — the rule that stops a free week being a free PRIME-TIME week.
      //
      // Deliberately NOT expressed as an access window. Doing that means writing the INVERSE of peak
      // ("everything except Mon–Thu 17:00–19:00 and Sat 08:00–10:00") into a second place, per tier,
      // by hand — and it goes wrong the first time peak moves, silently, because the only symptom is
      // members quietly playing free at peak again. This says what it means, and coverage reads
      // whatever peak is configured on the court being booked.
      function peakCoverCard() {
        var c = el("div", { class: "cf-card" }, [el("h3", { text: "Peak hours" }),
          el("p", { class: "cf-muted cf-tiny", text: "Whether this membership makes PEAK courts free too. Untick it and peak is simply charged at the normal rate — members are never blocked, and off-peak stays free. Peak hours themselves are set per court under Setup → Courts." })]);
        var cb = el("input", { type: "checkbox" }); cb.style.width = "auto"; cb.checked = m.coversPeak !== false;
        cb.addEventListener("change", function () { m.coversPeak = cb.checked; });
        c.appendChild(el("label", { class: "cf-row", style: "gap:10px;align-items:center;cursor:pointer;margin-top:6px" },
          [cb, el("span", { style: "font-weight:600", text: "Peak courts are free on this membership" })]));
        return c;
      }

      // Per-membership payment options — THE shared card (see paymentOptionsCard). Inherits the
      // membership default (then the club's global methods) unless tailored here.
      function paymentCard() {
        return paymentOptionsCard(m, "How members pay for THIS membership. Leave all ticked to "
          + "inherit the club default; untick to tailor. A single non-online option checks out "
          + "immediately.");
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
            var caps = { max_covered_minutes: m.limits.minutes, max_covered_per_day: m.limits.perDay, max_courts_per_day: m.limits.courtsDay };
            if (t.price_id) await window.AdminAPI.patchMembershipPlan(t.price_id, Object.assign({ tier: name, term_months: t.term_months, amount_minor: t.amount_minor || 0, set_window: true, access_days: m.win.days, access_start_min: m.win.start, access_end_min: m.win.end, set_modes: true, payment_modes: m.modes, set_limits: true, set_trial: true, is_trial: !!m.trial.on, trial_days: m.trial.days, covers_peak: m.coversPeak !== false }, caps));
            else await window.AdminAPI.createMembershipPlan(Object.assign({ tier: name, term_months: t.term_months, amount_minor: t.amount_minor || 0, access_days: m.win.days, access_start_min: m.win.start, access_end_min: m.win.end, payment_modes: m.modes, is_trial: !!m.trial.on, trial_days: m.trial.days, covers_peak: m.coversPeak !== false }, caps));
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
      if (ag.billing_model === "rent") subbits.push("bills own clients");
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
      var m = { rent_minor: ag.rent_minor || 0, rent_day: ag.rent_day || 1, coach_pct: (ag.coach_pct != null ? ag.coach_pct : ""), billing_model: ag.billing_model || "commission" };
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
        // HOW THE CLUB MONETISES THIS COACH — sits with rent and commission because those three
        // together are the whole arrangement. 'rent' means he invoices his own clients, so a lesson
        // he books AGAINST HIMSELF raises no club charge; that is how a rent coach holds a teaching
        // slot, and billing him for his own work put R68,000 of phantom debt on four coach accounts.
        // Explicit, never inferred from a 0% rate: a coach with NO commission rule resolves to 0%
        // too, so inferring would silently stop billing every unconfigured coach's clients.
        var modelS = el("select", { class: "cf-input", style: "max-width:300px" }, [
          el("option", { value: "commission", text: "Commission — the club bills their clients" }),
          el("option", { value: "rent", text: "Rent — they bill their own clients" }),
        ]);
        modelS.value = m.billing_model || "commission";
        var modelNote = el("p", { class: "cf-muted cf-tiny", style: "margin:6px 0 0" });
        function syncModelNote() {
          modelNote.textContent = (modelS.value === "rent")
            ? "Lessons this coach books against themselves raise NO club charge. Booking a named client still bills that client."
            : "The club bills this coach's clients and keeps the commission below.";
        }
        syncModelNote();
        modelS.addEventListener("change", function () { m.billing_model = modelS.value; syncModelNote(); });
        host.appendChild(el("div", { class: "cf-card" }, [
          el("h3", { text: "Billing model" }), modelS, modelNote]));
        var pctI = input({ type: "number", min: 0, max: 100, value: m.coach_pct, placeholder: String(DATA.agg.club_default_pct || 0), style: "max-width:100px" }); pctI.addEventListener("input", function () { m.coach_pct = pctI.value; });
        host.appendChild(el("div", { class: "cf-card" }, [el("h3", { text: "Default commission" }), el("p", { class: "cf-muted cf-tiny", text: "The % the club keeps on all this coach's lessons. Blank = the club default (" + (DATA.agg.club_default_pct || 0) + "%). Per-service overrides live in the service editor." }), el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [pctI, el("span", { class: "cf-muted", text: "% to the club" })])]));
      }
      async function save(btn) {
        btn.disabled = true; btn.textContent = "Saving…";
        try {
          await window.AdminAPI.putCoachAgreement(uid, { rent_minor: m.rent_minor, rent_day: m.rent_day, billing_model: m.billing_model });
          if (m.coach_pct !== "" && m.coach_pct != null) { var v = parseFloat(m.coach_pct); if (!isNaN(v)) await window.AdminAPI.setCommissionRule({ coach_user_id: uid, commission_pct: Math.max(0, Math.min(100, v)) }); }
          UI.toast("Saved.", "info"); renderList();
        } catch (e) { btn.disabled = false; btn.textContent = "Save & close"; UI.toast(UI.errMsg(e) || "Couldn't save.", "error"); }
      }
    }

    renderList();
  }

  // EQUIPMENT HIRE — a flat-fee, availability-tracked booking add-on (ball machine / racquets / balls).
  function equipmentManage(host) {
    init(); UI.clear(host);
    host.appendChild(el("div", { class: "cf-card" }, [
      el("h3", { text: "Equipment hire" }),
      el("p", { class: "cf-muted", text: "Add-ons clients hire with a court booking. Each has a flat fee and a quantity you own — a single ball machine can't be hired twice for the same time. Feature one on the client Home to promote it." }),
    ]));
    var listWrap = el("div", {}); host.appendChild(listWrap);
    var addBtn = el("button", { class: "cf-btn cf-btn-sm", style: "margin-top:10px", text: "+ Add equipment" });
    addBtn.addEventListener("click", function () { openItem(null); });
    host.appendChild(addBtn);

    function reload() {
      UI.clear(listWrap); listWrap.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
      window.AdminAPI.equipment().then(function (r) {
        UI.clear(listWrap);
        var items = r.equipment || [];
        if (!items.length) { listWrap.appendChild(el("div", { class: "cf-empty", text: "No equipment yet." })); return; }
        var list = el("div", { class: "cf-list" });
        items.forEach(function (it) {
          var price = it.amount_minor != null ? UI.money(it.amount_minor, it.currency_code || "ZAR") : "—";
          var sub = "Qty " + it.quantity + " · " + price + (it.feature_on_home ? " · featured" : "") + (it.active ? "" : " · hidden");
          var row = el("div", { class: "cf-item cf-item-tap" }, [
            el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: it.name }), el("div", { class: "cf-item-s", text: sub })]),
            el("span", { class: "cf-chip", text: "›" }),
          ]);
          row.addEventListener("click", function () { openItem(it); });
          list.appendChild(row);
        });
        listWrap.appendChild(list);
      }, function (e) { UI.clear(listWrap); listWrap.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    function openItem(it) {
      UI.clear(host);
      var m = { name: it ? it.name : "", amount: it ? (it.amount_minor || 0) : 0, quantity: it ? it.quantity : 1, feature: it ? !!it.feature_on_home : false, active: it ? it.active : true,
                // Equipment is a service and is paid for like one: null = inherit every club method.
                modes: (it && it.payment_modes) ? it.payment_modes.slice() : null, clubMethods: [],
                // Which COURT SERVICES this item is offered on. [] = ALL of them (the default, and
                // what every item created before this existed has).
                services: (it && it.services) ? it.services.slice() : [], courtServices: [] };
      // Needs the club's enabled methods AND its court services; fetch both, then render.
      Promise.all([
        window.TFAuth.apiJSON("/api/admin/membership-config").catch(function () { return {}; }),
        window.AdminAPI.products().catch(function () { return {}; }),
      ]).then(function (r) {
        m.clubMethods = (r[0] && r[0].club_payment_methods) || [];
        m.courtServices = ((r[1] && r[1].products) || [])
          .filter(function (p) { return p.kind === "court_booking" && p.active !== false; });
        renderItem();
      });
      function renderItem() {
      UI.clear(host);
      var saveB = el("button", { class: "cf-btn cf-btn-primary", text: "Save & close" });
      host.appendChild(el("div", { class: "cf-editbar" }, [
        el("button", { class: "cf-btn", text: "← Cancel", onclick: function () { equipmentManage(host); } }),
        el("strong", { text: it ? "Edit equipment" : "New equipment" }),
        el("span", { class: "cf-spacer" }), saveB,
      ]));
      var nameI = input({ value: m.name, placeholder: "e.g. Ball machine", style: "max-width:360px;font-weight:700" }); nameI.addEventListener("input", function () { m.name = nameI.value; });
      var amtI = input({ value: (m.amount / 100).toFixed(2), style: "max-width:120px" }); amtI.addEventListener("input", function () { m.amount = Math.round(parseFloat(amtI.value || "0") * 100); });
      var qtyI = input({ type: "number", min: 1, value: m.quantity, style: "max-width:100px" }); qtyI.addEventListener("input", function () { m.quantity = parseInt(qtyI.value, 10) || 1; });
      var featCb = el("input", { type: "checkbox" }); featCb.style.width = "auto"; featCb.checked = m.feature; featCb.addEventListener("change", function () { m.feature = featCb.checked; });
      host.appendChild(el("div", { class: "cf-card" }, [el("h3", { text: "Details" }),
        field("Name", nameI), field("Flat fee (R)", amtI), field("Quantity you own", qtyI),
        el("label", { class: "cf-row", style: "gap:10px;align-items:center;cursor:pointer;margin-top:6px" }, [featCb, el("span", { style: "font-weight:600", text: "Feature on the client Home (a hero tile)" })]),
      ]));
      host.appendChild(paymentOptionsCard(m, "How this equipment is paid for. It rides the court "
        + "booking's order, so if the court is free on a membership the booking still has to collect "
        + "this fee — a card-only item holds the booking until it's paid."));
      // Which court services offer this item. Nothing ticked = offered on ALL of them, which is the
      // default and what every existing item does — so an owner only opts in when they want to
      // restrict (clay-only shoes, a hard-court ball machine).
      if (m.courtServices.length > 1) {
        var sc = el("div", { class: "cf-card" }, [
          el("h3", { text: "Offered with" }),
          el("p", { class: "cf-muted cf-tiny", text: "Which court services can hire this. Leave all unticked to offer it with every court." }),
        ]);
        m.courtServices.forEach(function (svc) {
          var lbl = el("label", { class: "cf-row", style: "gap:8px;align-items:center;cursor:pointer;margin-top:6px" });
          var cb = el("input", { type: "checkbox" }); cb.style.width = "auto";
          cb.checked = m.services.indexOf(String(svc.id)) >= 0;
          cb.addEventListener("change", function () {
            var i = m.services.indexOf(String(svc.id));
            if (cb.checked && i < 0) m.services.push(String(svc.id));
            else if (!cb.checked && i >= 0) m.services.splice(i, 1);
          });
          lbl.appendChild(cb); lbl.appendChild(el("span", { text: svc.name }));
          sc.appendChild(lbl);
        });
        host.appendChild(sc);
      }
      if (it) {
        var tg = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", style: "margin-top:8px", text: it.active ? "Hide from booking" : "Show in booking" });
        tg.addEventListener("click", async function () { try { await window.AdminAPI.patchEquipment(it.id, { is_active: !it.active }); UI.toast("Saved.", "info"); equipmentManage(host); } catch (e) { UI.toast(UI.errMsg(e), "error"); } });
        host.appendChild(tg);
      }
      saveB.addEventListener("click", async function () {
        if (!(m.name || "").trim()) { UI.toast("Name it.", "warn"); return; }
        saveB.disabled = true;
        try {
          if (it) await window.AdminAPI.patchEquipment(it.id, { name: m.name, amount_minor: m.amount, quantity: m.quantity, feature_on_home: m.feature, payment_modes: m.modes, service_product_ids: m.services });
          else await window.AdminAPI.createEquipment({ name: m.name, amount_minor: m.amount, quantity: m.quantity, feature_on_home: m.feature, payment_modes: m.modes, service_product_ids: m.services });
          UI.toast("Saved.", "info"); equipmentManage(host);
        } catch (e) { saveB.disabled = false; UI.toast(UI.errMsg(e) || "Couldn't save.", "error"); }
      });
      }
    }
    reload();
  }

  // ---- Promotions & offers (Setup section) — specials with promo codes redeemed at checkout.
  function promotions(host) {
    init();
    function offerLabel(p) { return p.kind === "percent_off" ? (((p.percent_bps || 0) / 100) + "% off") : (UI.money(p.value_minor || 0) + " off"); }
    function scopeLabel(p) { return p.applies_to === "all" ? "Everything" : (p.applies_to.charAt(0).toUpperCase() + p.applies_to.slice(1)); }

    function draw() {
      UI.clear(host);
      host.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:flex-start;margin-bottom:12px" }, [
        el("div", {}, [el("h2", { text: "Promotions & offers" }),
          el("p", { class: "cf-muted", text: "Run a special with a promo code members enter at checkout — e.g. 20% off memberships." })]),
        el("button", { class: "cf-btn cf-btn-primary", text: "+ New promotion", onclick: function () { editModal(null); } }),
      ]));
      var list = el("div", { class: "cf-list" }, [el("div", { class: "cf-muted", text: "Loading…" })]);
      host.appendChild(list);
      window.AdminAPI.promotions().then(function (d) {
        UI.clear(list);
        var rows = (d && d.promotions) || [];
        if (!rows.length) { list.appendChild(el("div", { class: "cf-empty", text: "No promotions yet. Create one to run a special." })); return; }
        rows.forEach(function (p) {
          var used = (p.max_redemptions != null) ? (p.redemptions + "/" + p.max_redemptions + " used") : (p.redemptions + " used");
          var bits = [offerLabel(p), scopeLabel(p), used];
          if (p.code) bits.unshift("Code " + p.code);
          var row = el("div", { class: "cf-item", style: "cursor:pointer" }, [
            el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [el("span", { class: "cf-item-t", text: p.name }), p.status !== "active" ? UI.statusChip(p.status) : null].filter(Boolean)),
            el("div", { class: "cf-item-sub", text: bits.join(" · ") }),
          ]);
          row.addEventListener("click", function () { editModal(p); });
          list.appendChild(row);
        });
      }, function (e) { UI.clear(list); list.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    function editModal(p) {
      p = p || {}; var isNew = !p.id;
      var m = UI.modal(isNew ? "New promotion" : "Edit promotion");
      var f = {
        name: input({ value: p.name || "", placeholder: "e.g. January Membership 20%" }),
        code: input({ value: p.code || "", placeholder: "e.g. MEMBER20" }),
        kind: el("select", { class: "cf-input" }, [["percent_off", "% off"], ["amount_off", "Amount off"], ["bonus_period", "Free membership months (e.g. 3+1)"], ["bonus_units", "Free pack sessions (e.g. 10+2)"]].map(function (o) { return el("option", { value: o[0], text: o[1] }); })),
        value: input({ type: "number", value: p.kind === "amount_off" ? fromMinor(p.value_minor) : ((p.kind === "bonus_period" || p.kind === "bonus_units") ? (p.bonus_qty || "") : (p.percent_bps ? (p.percent_bps / 100) : "")) }),
        applies_to: el("select", { class: "cf-input" }, [["all", "Everything"], ["membership", "Memberships"], ["pack", "Packs"], ["court", "Court hire"], ["lesson", "Lessons"], ["class", "Classes"]].map(function (o) { return el("option", { value: o[0], text: o[1] }); })),
        max_redemptions: input({ type: "number", placeholder: "blank = unlimited", value: p.max_redemptions != null ? p.max_redemptions : "" }),
        per_customer_cap: input({ type: "number", value: p.per_customer_cap != null ? p.per_customer_cap : 1 }),
        min_spend: input({ type: "number", placeholder: "optional", value: p.min_spend_minor != null ? fromMinor(p.min_spend_minor) : "" }),
        ends_at: input({ type: "date", value: p.ends_at ? String(p.ends_at).slice(0, 10) : "" }),
        first_time: el("input", { type: "checkbox" }),
      };
      f.kind.value = p.kind || "percent_off"; f.applies_to.value = p.applies_to || "all";
      if (p.first_time_only) f.first_time.checked = true;
      var valLabel = el("label", {});
      function syncVal() {
        var k = f.kind.value;
        valLabel.textContent = k === "percent_off" ? "Percent off (e.g. 20)"
          : (k === "bonus_period" ? "Free months (on top of the paid term)"
          : (k === "bonus_units" ? "Free sessions (on top of the pack)" : "Amount off (R)"));
        // Bonus offers are scope-locked: free months → memberships, free sessions → packs.
        if (k === "bonus_period") { f.applies_to.value = "membership"; f.applies_to.disabled = true; }
        else if (k === "bonus_units") { f.applies_to.value = "pack"; f.applies_to.disabled = true; }
        else { f.applies_to.disabled = false; }
      }
      f.kind.addEventListener("change", syncVal); syncVal();

      m.body.appendChild(field("Name (internal)", f.name));
      m.body.appendChild(field("Promo code (what members type)", f.code));
      m.body.appendChild(el("div", { class: "cf-grid cf-grid-2" }, [field("Offer type", f.kind), el("div", { class: "cf-field" }, [valLabel, f.value])]));
      m.body.appendChild(field("Applies to", f.applies_to));
      m.body.appendChild(el("div", { class: "cf-grid cf-grid-2" }, [field("Total uses", f.max_redemptions), field("Uses per customer", f.per_customer_cap)]));
      m.body.appendChild(el("div", { class: "cf-grid cf-grid-2" }, [field("Min spend (R, optional)", f.min_spend), field("Ends on (optional)", f.ends_at)]));
      m.body.appendChild(el("label", { class: "cf-row", style: "gap:8px;align-items:center;margin-top:8px" }, [f.first_time, el("span", { text: "First-time purchases only" })]));

      var footer = el("div", { class: "cf-row", style: "justify-content:space-between;margin-top:14px" });
      var left = el("div", {});
      if (!isNew && p.status !== "archived") {
        left.appendChild(el("button", { class: "cf-btn cf-btn-sm", text: p.status === "paused" ? "Resume" : "Pause", onclick: function () { setStatus(p, p.status === "paused" ? "active" : "paused", m); } }));
        left.appendChild(el("button", { class: "cf-btn cf-btn-sm", style: "margin-left:6px", text: "Archive", onclick: function () { setStatus(p, "archived", m); } }));
      }
      footer.appendChild(left);
      var save = el("button", { class: "cf-btn cf-btn-primary", text: isNew ? "Create" : "Save" });
      footer.appendChild(save);
      m.body.appendChild(footer);
      if (!isNew) {
        m.body.appendChild(el("div", { class: "cf-row", style: "gap:14px;margin-top:10px" }, [
          el("button", { class: "cf-link", text: "View redemptions →", onclick: function () { m.close(); redemptions(p); } }),
          el("button", { class: "cf-link", text: "Unique codes →", onclick: function () { m.close(); codesModal(p); } }),
        ]));
      }

      save.addEventListener("click", async function () {
        var body = {
          name: f.name.value.trim(), code: f.code.value.trim() || null,
          kind: f.kind.value, applies_to: f.applies_to.value,
          per_customer_cap: parseInt(f.per_customer_cap.value, 10) || 1,
          first_time_only: f.first_time.checked,
          max_redemptions: f.max_redemptions.value.trim() ? parseInt(f.max_redemptions.value, 10) : null,
          min_spend_minor: f.min_spend.value.trim() ? Math.round(parseFloat(f.min_spend.value) * 100) : null,
          ends_at: f.ends_at.value || null,
        };
        if (f.kind.value === "percent_off") { body.percent_bps = Math.round(parseFloat(f.value.value || "0") * 100); body.value_minor = null; body.bonus_qty = null; }
        else if (f.kind.value === "bonus_period") { body.bonus_qty = parseInt(f.value.value || "0", 10) || 0; body.applies_to = "membership"; body.percent_bps = null; body.value_minor = null; }
        else if (f.kind.value === "bonus_units") { body.bonus_qty = parseInt(f.value.value || "0", 10) || 0; body.applies_to = "pack"; body.percent_bps = null; body.value_minor = null; }
        else { body.value_minor = Math.round(parseFloat(f.value.value || "0") * 100); body.percent_bps = null; body.bonus_qty = null; }
        if (!body.name) { UI.toast("Give it a name.", "warn"); return; }
        save.disabled = true;
        try {
          if (isNew) await window.AdminAPI.createPromotion(body); else await window.AdminAPI.updatePromotion(p.id, body);
          UI.toast("Saved.", "info"); m.close(); draw();
        } catch (e) { save.disabled = false; UI.toast(UI.errMsg(e), "error"); }
      });
    }

    function setStatus(p, status, m) {
      window.AdminAPI.setPromotionStatus(p.id, status).then(function () { UI.toast("Updated.", "info"); if (m) m.close(); draw(); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
    }

    function redemptions(p) {
      var m = UI.modal("Redemptions — " + p.name);
      var box = el("div", {}, [el("div", { class: "cf-muted", text: "Loading…" })]);
      m.body.appendChild(box);
      window.AdminAPI.promotionRedemptions(p.id).then(function (d) {
        UI.clear(box);
        var rows = (d && d.redemptions) || [];
        if (!rows.length) { box.appendChild(el("div", { class: "cf-empty", text: "No redemptions yet." })); return; }
        rows.forEach(function (r) {
          var who = [r.first_name, r.surname].filter(Boolean).join(" ") || r.email || "—";
          box.appendChild(el("div", { class: "cf-item" }, [
            el("div", { class: "cf-row", style: "justify-content:space-between" }, [el("span", { class: "cf-item-t", text: who }), el("span", { text: UI.money(r.discount_minor) })]),
            el("div", { class: "cf-item-sub", text: (r.status === "reversed" ? "Reversed · " : "") + String(r.redeemed_at).slice(0, 10) }),
          ]));
        });
      }, function (e) { UI.clear(box); box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    // Unique per-recipient codes: mint a batch (one per member for a Klaviyo campaign), copy them, revoke.
    function codesModal(p) {
      var m = UI.modal("Unique codes — " + p.name, { lg: true });
      m.body.appendChild(el("p", { class: "cf-muted", text: "Mint single-use codes (one per member) to embed in a Klaviyo campaign. Each redeems exactly once. Leave the promo's shared code blank when using these." }));
      var cnt = input({ type: "number", value: "50", style: "max-width:110px" });
      var pfx = input({ placeholder: "Prefix (optional, e.g. JAN)", style: "max-width:200px" });
      var gen = el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Generate" });
      m.body.appendChild(el("div", { class: "cf-row", style: "gap:8px;align-items:flex-end;margin:8px 0 12px" }, [
        el("div", { class: "cf-field" }, [el("label", { text: "How many" }), cnt]),
        el("div", { class: "cf-field" }, [el("label", { text: "Prefix" }), pfx]), gen]));
      var box = el("div", {});
      m.body.appendChild(box);
      function load() {
        UI.clear(box); box.appendChild(el("div", { class: "cf-muted", text: "Loading…" }));
        window.AdminAPI.promotionCodes(p.id).then(function (d) {
          UI.clear(box);
          var rows = (d && d.codes) || [];
          if (!rows.length) { box.appendChild(el("div", { class: "cf-empty", text: "No codes yet — generate a batch above." })); return; }
          var active = rows.filter(function (r) { return r.status === "active"; }).map(function (r) { return r.code; });
          box.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [
            el("span", { class: "cf-muted", text: rows.length + " code" + (rows.length > 1 ? "s" : "") }),
            el("button", { class: "cf-btn cf-btn-sm", text: "Copy all active", onclick: function () {
              try { navigator.clipboard.writeText(active.join("\n")); UI.toast("Copied " + active.length + " codes.", "info"); } catch (e) { UI.toast("Copy not available — select the list.", "warn"); } } }),
          ]));
          var ta = el("textarea", { class: "cf-input", rows: "8", readonly: "readonly", style: "font-family:monospace;font-size:.85rem" });
          ta.value = rows.map(function (r) { return r.code + (r.used_count ? "  (used)" : "") + (r.status !== "active" ? "  (revoked)" : ""); }).join("\n");
          box.appendChild(ta);
        }, function (e) { UI.clear(box); box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
      }
      gen.addEventListener("click", function () {
        gen.disabled = true; gen.textContent = "Generating…";
        window.AdminAPI.generatePromotionCodes(p.id, { count: parseInt(cnt.value, 10) || 1, prefix: pfx.value.trim() || null })
          .then(function (r) { UI.toast("Minted " + (r.count || 0) + " codes.", "info"); gen.disabled = false; gen.textContent = "Generate"; load(); },
                function (e) { gen.disabled = false; gen.textContent = "Generate"; UI.toast(UI.errMsg(e), "error"); });
      });
      load();
    }

    draw();
  }

  // ---- Community & games (community/) ---------------------------------------
  // The screen that turns the seat rule on — and the only one that can, since the two switches are
  // otherwise SQL-only. It is deliberately blunt about what flipping seat_rule_enforced does to
  // members' bills, because that is a change to what people pay and it should not be a surprise
  // discovered at the desk.
  function communityManage(host) {
    init(); UI.clear(host);
    var wrap = el("div", {});
    host.appendChild(wrap);

    function draw(cfg) {
      UI.clear(wrap);

      // --- the two switches ---
      var c = el("div", { class: "cf-card" }, [
        el("h3", { text: "Find a Game" }),
        el("p", { class: "cf-muted", text: "Let members find each other, post open games and bring guests. Two switches, on purpose: you can give members the feature before you change what anyone pays." }),
      ]);
      function flag(key, label, hint, warn) {
        var lbl = el("label", { class: "cf-row", style: "cursor:pointer;gap:10px;align-items:flex-start;margin-top:12px" });
        var cb = el("input", { type: "checkbox" });
        cb.checked = !!cfg[key];
        cb.addEventListener("change", function () {
          var body = {}; body[key] = cb.checked;
          window.AdminAPI.saveCommunitySettings(body).then(function (r) {
            UI.toast(label + (cb.checked ? " on" : " off"), "info"); draw(r);
          }, function (e) { cb.checked = !cb.checked; UI.toast(UI.errMsg(e), "error"); });
        });
        lbl.appendChild(cb);
        lbl.appendChild(el("div", {}, [
          el("div", { text: label }),
          el("div", { class: "cf-muted cf-tiny", text: hint }),
          warn ? el("div", { class: "cf-tiny", style: "color:#8a5a00;margin-top:2px", text: warn }) : null,
        ].filter(Boolean)));
        c.appendChild(lbl);
      }
      flag("community_enabled", "Community features",
        "Members can see open games, join them, invite friends and message each other.");
      // The copy has to match the Book-a-court rule (2026-08-15), because this is the screen an owner
      // reads immediately before telling their members what is about to change. The previous wording
      // ("each player pays a share", "a member who books a court and doesn't fill the spare seat is
      // charged for it") described the OPEN-game rule as if it applied to every booking — it does
      // not, and an owner acting on it would have warned their members about a charge that never
      // arrives and missed the one that does.
      flag("seat_rule_enforced", "Charge for every seat",
        "Applies to OPEN games — a court shared with whoever answers. Everyone playing pays a share; "
        + "members play on their membership. Booking a court privately is unchanged: the booker pays "
        + "the normal court price, and a guest pays a share only when the booker is a member.",
        "This changes what members pay. In an OPEN game a spare seat nobody takes is added to the "
        + "booker's bill. A privately booked court is never held or cancelled because a guest hasn't "
        + "paid — collect that at the desk. Tell your members before you switch it on.");
      wrap.appendChild(c);

      // --- what one player pays ---
      // The single most consequential number on this screen, so it shows the ACTUAL rands for the
      // club's own durations rather than leaving the owner to do percentages in their head.
      var sh = el("div", { class: "cf-card" }, [
        el("h3", { text: "What one player pays" }),
        el("p", { class: "cf-muted", text: "What one player pays in an OPEN game. A share is a fixed slice of the court price — it does NOT change when someone else joins, leaves, or turns out to be a member. At 50%, two paying players add up to the court price. A privately booked court is charged at its normal price instead." }),
      ]);
      var pct = el("input", { class: "cf-input", type: "number", min: "0", max: "100",
        style: "max-width:110px", value: String(cfg.seat_share_pct == null ? 50 : cfg.seat_share_pct) });
      var rnd = el("select", { class: "cf-input", style: "max-width:220px" }, [
        ["none", "No rounding"], ["up_5", "Round up to nearest R5"], ["up_10", "Round up to nearest R10"],
        ["nearest_5", "Nearest R5"], ["nearest_10", "Nearest R10"],
      ].map(function (o) { return el("option", { value: o[0], text: o[1] }); }));
      rnd.value = cfg.seat_rounding || "up_10";
      function saveShare() {
        window.AdminAPI.saveCommunitySettings({
          seat_share_pct: parseInt(pct.value, 10), seat_rounding: rnd.value,
        }).then(function (r) { UI.toast("Saved", "info"); draw(r); },
          function (e) { UI.toast(UI.errMsg(e), "error"); });
      }
      pct.addEventListener("change", saveShare);
      rnd.addEventListener("change", saveShare);
      sh.appendChild(el("div", { class: "cf-row", style: "gap:12px;align-items:flex-end;margin-top:8px" }, [
        el("div", { class: "cf-field", style: "margin:0" }, [el("label", { text: "Share of the court (%)" }), pct]),
        el("div", { class: "cf-field", style: "margin:0" }, [el("label", { text: "Rounding" }), rnd]),
      ]));
      var ex = el("div", { class: "cf-list", style: "margin-top:10px" });
      (cfg.share_examples || []).forEach(function (x) {
        ex.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: x.duration_minutes + " min · court " + UI.money(x.court_minor, "ZAR") }),
            el("div", { class: "cf-item-s", text: "two paying players = " + UI.money(x.share_minor * 2, "ZAR")
              + (x.share_minor * 2 === x.court_minor ? " (exactly the court)" : "") }),
          ]),
          el("span", { class: "cf-chip", text: UI.money(x.share_minor, "ZAR") + " each" }),
        ]));
      });
      if ((cfg.share_examples || []).length) sh.appendChild(ex);
      sh.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin-top:8px",
        text: "With more than two paying players the club collects more than one court fee — a doubles game with four non-members pays four shares. That is deliberate: four people use a court more than two." }));
      wrap.appendChild(sh);

      // --- what it's doing right now ---
      var stats = el("div", { class: "cf-card" }, [el("h3", { text: "Right now" })]);
      var g = el("div", { class: "cf-stats" });
      [["Open games", cfg.open_games], ["Invites out", cfg.live_invites],
       ["Players findable", cfg.discoverable_players],
       ["Seats unpaid", UI.money(cfg.unpaid_seats_minor || 0, "ZAR")]].forEach(function (p) {
        g.appendChild(el("div", { class: "cf-stat" }, [
          el("div", { class: "cf-stat-v", text: String(p[1] == null ? "—" : p[1]) }),
          el("div", { class: "cf-stat-k", text: p[0] }),
        ]));
      });
      stats.appendChild(g);
      wrap.appendChild(stats);

      // --- timings ---
      var t = el("div", { class: "cf-card" }, [
        el("h3", { text: "Timings" }),
        el("p", { class: "cf-muted", text: "How long a spare seat stays open, and how long someone has to pay for one." }),
      ]);
      function num(key, label, hint, suffix) {
        var inp = el("input", { class: "cf-input", type: "number", min: "1", value: String(cfg[key] || "") });
        inp.addEventListener("change", function () {
          var body = {}; body[key] = parseInt(inp.value, 10);
          window.AdminAPI.saveCommunitySettings(body).then(function (r) {
            UI.toast("Saved", "info"); draw(r);
          }, function (e) { UI.toast(UI.errMsg(e), "error"); });
        });
        t.appendChild(el("div", { class: "cf-field", style: "margin-top:10px" }, [
          el("label", { text: label + (suffix ? " (" + suffix + ")" : "") }), inp,
          el("div", { class: "cf-muted cf-tiny", text: hint }),
        ]));
      }
      num("open_game_cutoff_hours", "Spare seat closes", "Hours before the game. After this an unfilled seat is added to the booker's bill.", "hours");
      num("seat_pay_hours", "Time to pay a seat", "How long a player has to pay before their seat is released.", "hours");
      num("guest_trial_days", "Free week for an invited friend", "A guest invited by a member plays free for this many days, once, ever.", "days");
      wrap.appendChild(t);

      // --- the configuration trap, stated ---
      // The entitlement caps shipped and sat inert for weeks because nobody set them. Say the quiet
      // part on the screen rather than leaving the owner to assume doubles works.
      var fmt = cfg.seats_by_format || {};
      wrap.appendChild(el("div", { class: "cf-card" }, [
        el("h3", { text: "Seats per format" }),
        el("p", { class: "cf-muted", text: "How many players share a court fee. Singles " + (fmt.singles || 2)
          + ", doubles " + (fmt.doubles || 4) + ", on your own " + (fmt.practice || 1) + "." }),
        // STALE COPY, CORRECTED 2026-08-11. This still described the ORIGINAL split model — one court
        // fee divided among the un-covered players — which Tomo replaced with a FIXED share months
        // ago. It told an owner a doubles guest pays "a quarter" (R37.50 on a R150 court) when the
        // engine charges them a full share (R80), and it contradicted the paragraph directly above
        // it. Wrong by more than double, on the one screen where the money gets switched on.
        el("p", { class: "cf-muted cf-tiny", text: "Doubles does NOT divide the fee four ways — a share is a fixed slice of the court, so every player who isn't covered pays the SAME share as they would in singles. Two members plus two guests means each guest pays a full share, and the club collects more than one court fee. That is deliberate: four people use a court more than two." }),
      ]));
    }

    wrap.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
    window.AdminAPI.communitySettings().then(draw, function (e) {
      UI.clear(wrap); wrap.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) }));
    });
  }

  // ---- Games & invites (the owner's operational view) ------------------------
  function communityGames(host) {
    init(); UI.clear(host);
    var tabs = el("div", { class: "cf-row", style: "gap:8px;margin-bottom:12px" });
    var body = el("div", {});
    host.appendChild(tabs); host.appendChild(body);
    var active = "games";
    [["games", "Games"], ["invites", "Invites"], ["players", "Players & levels"]].forEach(function (t) {
      var b = el("button", { class: "cf-btn cf-btn-sm", text: t[1] });
      b.addEventListener("click", function () { active = t[0]; paint(); });
      tabs.appendChild(b);
    });

    function paint() {
      Array.prototype.forEach.call(tabs.children, function (b, i) {
        b.className = "cf-btn cf-btn-sm" + (["games", "invites", "players"][i] === active ? " cf-btn-primary" : "");
      });
      UI.clear(body);
      body.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
      if (active === "games") return drawGames();
      if (active === "invites") return drawInvites();
      return drawPlayers();
    }

    function drawGames() {
      window.AdminAPI.communityGames(14).then(function (r) {
        UI.clear(body);
        var rows = r.games || [];
        if (!rows.length) { body.appendChild(el("div", { class: "cf-empty", text: "No seated games in the next 14 days." })); return; }
        var list = el("div", { class: "cf-list" });
        rows.forEach(function (g) {
          var when = "";
          try { when = UI.fmtDate(g.starts_at) + " " + UI.fmtTime(g.starts_at); } catch (e) {}
          var bits = [g.court_name, g.host_name, g.seats_taken + "/" + g.seats_total + " seats"];
          if (g.open_seats > 0) bits.push(g.open_seats + " open");
          if (window.CFIntent.word(g.play_intent)) bits.push(window.CFIntent.word(g.play_intent));
          // The number an owner actually scans for: is somebody about to play on an unpaid court?
          if (g.owed_minor > 0) bits.push("owed " + UI.money(g.owed_minor, "ZAR"));
          var row = el("div", { class: "cf-item cf-item-tap" }, [
            el("div", { class: "cf-item-main" }, [
              el("div", { class: "cf-item-t",
                text: when + " · " + window.CFIntent.format(g.play_format || "singles") }),
              el("div", { class: "cf-item-s", text: bits.filter(Boolean).join(" · ") }),
            ]),
            el("span", { class: "cf-chip" + (g.owed_minor > 0 ? " held" : ""), text: g.status }),
          ]);
          // → the GAME view (who is on the court, which seat owes, the chat), not straight to the
          // booking record. The record answers "what was charged"; only the game answers "who by".
          // The game view carries a "Booking details" button through to #/event/<id>.
          row.addEventListener("click", function () { location.hash = "#/game/" + g.booking_id; });
          list.appendChild(row);
        });
        body.appendChild(list);
      }, function (e) { UI.clear(body); body.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    function drawInvites() {
      window.AdminAPI.communityInvites().then(function (r) {
        UI.clear(body);
        var rows = r.invites || [];
        if (!rows.length) { body.appendChild(el("div", { class: "cf-empty", text: "No invitations yet." })); return; }
        var list = el("div", { class: "cf-list" });
        rows.forEach(function (iv) {
          // "My friend says they never got their free week" is otherwise unanswerable without SQL.
          var sub = ["from " + iv.invited_by, iv.status,
                     iv.trial_granted ? "free week granted" : ""].filter(Boolean).join(" · ");
          var acts = [];
          if (iv.status === "sent") {
            var rev = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Revoke" });
            rev.addEventListener("click", function () {
              window.AdminAPI.revokeCommunityInvite(iv.id).then(function () {
                UI.toast("Invitation revoked", "info"); paint();
              }, function (e) { UI.toast(UI.errMsg(e), "error"); });
            });
            acts.push(rev);
          }
          list.appendChild(el("div", { class: "cf-item" }, [
            el("div", { class: "cf-item-main" }, [
              el("div", { class: "cf-item-t", text: iv.email }),
              el("div", { class: "cf-item-s", text: sub }),
            ]),
            el("span", { class: "cf-spacer" }),
          ].concat(acts)));
        });
        body.appendChild(list);
      }, function (e) { UI.clear(body); body.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    function drawPlayers() {
      window.AdminAPI.communityPlayers().then(function (r) {
        UI.clear(body);
        // NOT "until a coach corrects it" — coaches were taken out of this lane entirely on
        // 2026-08-12 (owner's decision), so that sentence promised a correction nobody could make.
        body.appendChild(el("p", { class: "cf-muted", text: "A level is self-declared from the five questions until you correct it here. Being matched far above or below your standard is what makes people stop using this, so a wrong level is worth fixing." }));
        var rows = r.players || [];
        if (!rows.length) { body.appendChild(el("div", { class: "cf-empty", text: "No player profiles yet." })); return; }
        var list = el("div", { class: "cf-list" });
        rows.forEach(function (p) {
          var inp = el("input", { class: "cf-input", type: "number", step: "0.1", min: "1", max: "10",
            style: "max-width:90px", value: p.level == null ? "" : String(p.level) });
          inp.addEventListener("change", function () {
            window.AdminAPI.setPlayerLevel(p.user_id, parseFloat(inp.value)).then(function () {
              UI.toast("Level saved", "info"); paint();
            }, function (e) { UI.toast(UI.errMsg(e), "error"); });
          });
          list.appendChild(el("div", { class: "cf-item" }, [
            el("div", { class: "cf-item-main" }, [
              // A member with no first name rendered as a bare "—", which reads as a broken row
              // rather than as missing data (seen live 2026-08-15).
              el("div", { class: "cf-item-t", text: p.name || "(no name on the account)" }),
              el("div", { class: "cf-item-s", text: (p.level_source ? "set by " + p.level_source : "not set")
                + (p.visible ? " · findable" : " · not findable") }),
            ]),
            el("span", { class: "cf-spacer" }), inp,
          ]));
        });
        body.appendChild(list);
      }, function (e) { UI.clear(body); body.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    paint();
  }

  window.AdminUI = {
    communityManage: communityManage, communityGames: communityGames,
    clubProfile: clubProfile, billingDetails: billingDetails, promotions: promotions, hours: hours, courts: courts, courtsManage: courtsManage,
    coachManage: coachManage,
    services: services, coaches: coaches, membershipPlans: membershipPlans,
    membershipServices: membershipServices, equipmentManage: equipmentManage,
    coachAgreements: coachAgreements,
    courtRates: courtRates, pricingHome: pricingHome,
  };
})();

