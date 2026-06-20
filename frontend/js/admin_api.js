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

    // ---- coaches ---------------------------------------------------------
    // GET /api/admin/coaches -> {coaches:[{id,email,display_name,status,...}]}
    coaches: function () { return A().apiJSON("/api/admin/coaches"); },
    // POST /api/admin/coaches/invite  body: {email,phone,first_name,surname,display_name}
    //   -> {coach, invite_link}
    inviteCoach: function (body) {
      return A().apiJSON("/api/admin/coaches/invite", { method: "POST", body: body });
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
          listBox.appendChild(el("div", { class: "cf-item" }, [
            el("span", { class: "cf-chip coach", text: "coach" }),
            el("div", { class: "cf-item-main" }, [
              el("div", { class: "cf-item-t", text: c.display_name || c.email || "Coach" }),
              el("div", { class: "cf-item-s", text: (c.email || "") + (c.status ? " · " + c.status : "") }),
            ]),
          ]));
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

  window.AdminUI = {
    clubProfile: clubProfile, hours: hours, courts: courts,
    services: services, coaches: coaches,
  };
})();

