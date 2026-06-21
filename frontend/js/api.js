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
  };

  window.API = API;
})();
