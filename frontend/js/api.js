// api.js — thin typed wrappers over the live B (diary) / C (billing) APIs.
//
// Every wrapper maps 1:1 to a route verified against diary/routes.py and
// billing/routes.py. club_id is NEVER sent in the body — the server derives it from
// the Clerk JWT principal (auth/principal.py). Depends on window.TFAuth (auth_client.js).
//
// All calls go through TFAuth.apiJSON, which attaches the Bearer header and throws
// {status, body:{error,message}} on a non-2xx so the UI can surface e.g. 409 SLOT_TAKEN.
(function () {
  function A() {
    if (!window.TFAuth) throw new Error("auth_client.js must load before api.js");
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

  var API = {
    // ---- identity --------------------------------------------------------
    // GET /api/whoami -> {user_id, club_id, role, email, method}
    whoami: function () { return A().apiJSON("/api/whoami"); },

    // ---- diary: read -----------------------------------------------------
    // GET /api/diary/availability -> {slots:[{start,end,resource_id,resource_name,kind,price}], count}
    // opts: {kind, resource_id, coach_id, surface, date_from, date_to, duration, audience, any}
    availability: function (opts) {
      return A().apiJSON("/api/diary/availability" + qs(opts));
    },
    // GET /api/diary/resources -> {resources:[{id,kind,name,surface,coach_user_id,capacity,is_active,rank}]}
    resources: function () { return A().apiJSON("/api/diary/resources"); },

    // GET /api/diary/classes -> {classes:[{id,class_name,coach_user_id,starts_at,ends_at,
    //                            capacity,price_id,enrolled,waitlisted,spots_left}], count}
    classes: function (opts) { return A().apiJSON("/api/diary/classes" + qs(opts)); },

    // ---- diary: bookings -------------------------------------------------
    // GET /api/diary/bookings -> {bookings:[{id,booking_type,resource_id,resource_name,
    //   coach_user_id,starts_at,ends_at,status,order_id,settlement_mode,booked_by_user_id}], count}
    // opts: {date_from, date_to, status, resource_id, as_coach}
    bookings: function (opts) { return A().apiJSON("/api/diary/bookings" + qs(opts)); },

    // GET /api/diary/bookings/:id -> {booking:{...,parties:[...]}}
    getBooking: function (id) {
      return A().apiJSON("/api/diary/bookings/" + encodeURIComponent(id));
    },

    // POST /api/diary/bookings -> {order_id, status, checkout, amount_minor}
    // body: {booking_type, resource_id, starts_at, ends_at, settlement_mode, parties,
    //        coach_user_id, court_resource_id, audience, notes}
    createBooking: function (body) {
      return A().apiJSON("/api/diary/bookings", { method: "POST", body: body });
    },

    // PATCH /api/diary/bookings/:id  body: {starts_at, ends_at, scope?("this"|"series")}
    rescheduleBooking: function (id, body) {
      return A().apiJSON("/api/diary/bookings/" + encodeURIComponent(id),
        { method: "PATCH", body: body });
    },

    // POST /api/diary/bookings/:id/cancel  body: {reason?}
    cancelBooking: function (id, body) {
      return A().apiJSON("/api/diary/bookings/" + encodeURIComponent(id) + "/cancel",
        { method: "POST", body: body || {} });
    },

    // POST /api/diary/bookings/:id/status
    // body: {status:"completed"|"no_show"|"attended", party_id?, attended?}
    setBookingStatus: function (id, body) {
      return A().apiJSON("/api/diary/bookings/" + encodeURIComponent(id) + "/status",
        { method: "POST", body: body });
    },

    // ---- lesson approval lifecycle (requested=awaiting coach, proposed=awaiting client) --
    // POST /api/diary/bookings/:id/accept  -> the awaited party confirms (assigns court + settles)
    acceptBooking: function (id) {
      return A().apiJSON("/api/diary/bookings/" + encodeURIComponent(id) + "/accept",
        { method: "POST", body: {} });
    },
    // POST /api/diary/bookings/:id/propose  body: {starts_at, ends_at} -> propose a new time
    proposeTime: function (id, body) {
      return A().apiJSON("/api/diary/bookings/" + encodeURIComponent(id) + "/propose",
        { method: "POST", body: body });
    },
    // POST /api/diary/bookings/:id/decline  body: {reason?} -> decline a requested/proposed lesson
    declineBooking: function (id, body) {
      return A().apiJSON("/api/diary/bookings/" + encodeURIComponent(id) + "/decline",
        { method: "POST", body: body || {} });
    },

    // ---- diary: classes enrolment ---------------------------------------
    // POST /api/diary/classes/:id/enrol  body: {settlement_mode, audience, user_id?(admin/coach)}
    enrol: function (classId, body) {
      return A().apiJSON("/api/diary/classes/" + encodeURIComponent(classId) + "/enrol",
        { method: "POST", body: body || {} });
    },
    // POST /api/diary/classes/:id/cancel-enrolment  body: {user_id?}
    cancelEnrolment: function (classId, body) {
      return A().apiJSON("/api/diary/classes/" + encodeURIComponent(classId) + "/cancel-enrolment",
        { method: "POST", body: body || {} });
    },

    // ---- diary: coach/admin ----------------------------------------------
    // POST /api/diary/time-off  body: {resource_id, starts_at, ends_at, reason}
    timeOff: function (body) {
      return A().apiJSON("/api/diary/time-off", { method: "POST", body: body });
    },
    // GET /api/diary/master -> {events:[{id,booking_type,resource_id,resource_name,kind,
    //   coach_user_id,starts_at,ends_at,status,booked_by_user_id,order_id,settlement_mode}], count}
    // opts: {date_from, date_to}
    master: function (opts) { return A().apiJSON("/api/diary/master" + qs(opts)); },

    // ---- billing ---------------------------------------------------------
    // GET /api/billing/config?club_id= -> {online_enabled, provider, currency, public_key}
    // Public probe; club_id is the only place we pass it (read-only policy lookup).
    billingConfig: function (clubId) {
      return A().apiJSON("/api/billing/config" + qs({ club_id: clubId }));
    },
    // POST /api/billing/desk-payment (admin)
    // body: {order_id, amount_minor?, provider?(cash|card_at_desk|eft), currency_code?, provider_payment_id?}
    deskPayment: function (body) {
      return A().apiJSON("/api/billing/desk-payment", { method: "POST", body: body });
    },

    // ---- me: client self-service ("My Account") -------------------------
    // GET /api/me/profile -> {email(read-only), first_name, surname, phone, dob, address_*,
    //   city, postal_code, country, emergency_contact_name/phone, marketing_opt_in, role}
    getProfile: function () { return A().apiJSON("/api/me/profile"); },
    // PATCH /api/me/profile  body: editable fields only (email IGNORED server-side) -> refreshed profile
    patchProfile: function (body) {
      return A().apiJSON("/api/me/profile", { method: "PATCH", body: body });
    },
    // GET /api/me/dependents -> {dependents:[{id, dependent_user_id, first_name, surname, dob,
    //   relationship, is_minor, notes, is_active}], count}
    dependents: function () { return A().apiJSON("/api/me/dependents"); },
    // POST /api/me/dependents  body: {first_name(req), surname?, dob?, relationship?, is_minor?, notes?}
    addDependent: function (body) {
      return A().apiJSON("/api/me/dependents", { method: "POST", body: body });
    },
    // PATCH /api/me/dependents/:id  body: editable fields
    patchDependent: function (id, body) {
      return A().apiJSON("/api/me/dependents/" + encodeURIComponent(id),
        { method: "PATCH", body: body });
    },
    // DELETE /api/me/dependents/:id -> soft remove
    removeDependent: function (id) {
      return A().apiJSON("/api/me/dependents/" + encodeURIComponent(id), { method: "DELETE" });
    },

    // ---- me: financials + refund requests ("My Account → Financials") ----
    // GET /api/me/financials -> {currency, plan:{type,active,name,current_period_end,price_minor,sold},
    //   usage_this_month:{court,lesson,class,total}, spend:{this_month_minor,history:[{period,paid_minor,orders}]},
    //   account:{balance_minor,open_charges}, next_charge:{kind,amount_minor,due_date}}
    financials: function () { return A().apiJSON("/api/me/financials"); },
    // GET /api/me/statement?month= -> client coaching statement (mirror of the coach's): per coach,
    // lessons paid this month + outstanding arrears. {month, currency, coaches:[...], arrears_items, totals}
    myStatement: function (opts) { return A().apiJSON("/api/me/statement" + qs(opts)); },
    // POST /api/me/statement/pay  body {order_ids?} (subset = part-settle; default = all owed)
    //   -> {order_id, amount_minor, currency} — pay owed orders online (Yoco). 409 NOTHING_OWED.
    payStatement: function (body) { return A().apiJSON("/api/me/statement/pay", { method: "POST", body: body || {} }); },
    // GET /api/me/orders -> {orders:[{id,created_at,amount_minor,currency_code,status,settlement_mode,
    //   description,has_open_refund,refund_status,refundable}], count}
    myOrders: function () { return A().apiJSON("/api/me/orders"); },
    // GET /api/me/bookings/:id -> {booking:{id,booking_type,status,starts_at,ends_at,duration_minutes,
    //   is_future,court_name,coach_name,venue:{club_name,address},players:[{name,kind}],
    //   charge:{amount_minor,currency,status,settlement_mode,order_id,refundable,has_open_refund},
    //   ics_url,can:{...}}} — the full "booking story" for the detail view.
    bookingStory: function (id) { return A().apiJSON("/api/me/bookings/" + encodeURIComponent(id)); },
    // GET /api/me/billing/summary?month= -> {month,currency,total_minor,categories:[{key,label,count,
    //   total_minor,items:[{booking_id,starts_at,amount_minor,status,coach_name,court_name}]}]}
    billingSummary: function (month) { return A().apiJSON("/api/me/billing/summary" + (month ? ("?month=" + encodeURIComponent(month)) : "")); },
    // GET /api/me/activity -> {activity:[{at,kind,title,detail,amount_minor,currency,direction}]}
    //   the client's transaction log (payments, refunds, charges, coaching, memberships).
    activity: function (limit) { return A().apiJSON("/api/me/activity" + (limit ? ("?limit=" + limit) : "")); },
    // GET /api/me/refund-requests -> {requests:[{id,order_id,amount_minor,reason,status,...}], count}
    refundRequests: function () { return A().apiJSON("/api/me/refund-requests"); },
    // POST /api/me/refund-requests  body: {order_id(req), amount_minor?, reason?} -> {refund_request}
    requestRefund: function (body) {
      return A().apiJSON("/api/me/refund-requests", { method: "POST", body: body });
    },
    // POST /api/me/refund-requests/:id/cancel -> {ok, refund_request}
    cancelRefundRequest: function (id) {
      return A().apiJSON("/api/me/refund-requests/" + encodeURIComponent(id) + "/cancel",
        { method: "POST", body: {} });
    },

    // ---- me: notifications / in-app inbox -------------------------------
    // GET /api/me/notifications?unread= -> {notifications:[{id,kind,title,body,link,data,
    //   read_at,email_status,created_at}], unread_count, count}
    notifications: function (opts) { return A().apiJSON("/api/me/notifications" + qs(opts)); },
    // POST /api/me/notifications/read  body {id?|all:true} -> {ok, updated, unread_count}
    markNotificationsRead: function (body) {
      return A().apiJSON("/api/me/notifications/read", { method: "POST", body: body || {} });
    },
  };

  window.API = API;
})();
