// account.js — the client "My Account" page (docs/specs/client-self-service-spec.md §7).
// Two tabs (mirrors settings.js): Profile (editable demographics; email read-only) and
// Family (children/dependents list + add/edit/remove). Reuses cf-* + UI helpers; no new CSS.
//
// API (api.js): getProfile/patchProfile + dependents/addDependent/patchDependent/removeDependent,
// each 1:1 with /api/me/*. club_id + user_id are server-derived from the principal — never sent.
(function () {
  var UI, el;
  var state = { profile: null, dependents: [], tab: "profile" };

  var TABS = [
    { k: "profile", t: "Profile" },
    { k: "family", t: "Family" },
  ];

  function root() { return document.getElementById("cf-account"); }

  function tabBar() {
    var nav = el("nav", { class: "cf-nav", style: "margin-bottom:16px" });
    TABS.forEach(function (tab) {
      var a = el("a", { href: "#" + tab.k, text: tab.t });
      if (tab.k === state.tab) a.classList.add("active");
      a.addEventListener("click", function (ev) { ev.preventDefault(); select(tab.k); });
      nav.appendChild(a);
    });
    return nav;
  }

  function select(k) {
    state.tab = k;
    try { history.replaceState(null, "", "#" + k); } catch (e) {}
    render();
  }

  function render() {
    var host = root(); UI.clear(host);
    host.appendChild(el("div", { class: "cf-card" }, [
      el("h2", { text: "My Account" }),
      el("p", { class: "cf-muted", text: "Manage your details and your family. Changes save per section." }),
    ]));
    host.appendChild(tabBar());
    var sectionHost = el("div");
    host.appendChild(sectionHost);
    if (state.tab === "profile") renderProfile(sectionHost);
    else renderFamily(sectionHost);
  }

  // ---- Profile tab ----------------------------------------------------------
  function field(label, input, hint) {
    var kids = [el("label", { text: label }), input];
    if (hint) kids.push(el("div", { class: "cf-pref-note", text: hint }));
    return el("div", { class: "cf-field" }, kids);
  }
  function input(value, attrs) {
    var a = Object.assign({ class: "cf-input", value: value == null ? "" : value }, attrs || {});
    return el("input", a);
  }

  function renderProfile(host) {
    var pr = state.profile || {};
    var card = el("div", { class: "cf-card" });
    card.appendChild(el("h3", { text: "Your details" }));

    // Email — READ-ONLY (the client id / login). Disabled input + helper text.
    var emailIn = input(pr.email, { type: "email", disabled: "disabled" });
    card.appendChild(field("Email", emailIn, "This is your login — contact the club to change it."));

    var fn = input(pr.first_name), sn = input(pr.surname), ph = input(pr.phone, { type: "tel" });
    var dob = input(pr.dob, { type: "date" });
    card.appendChild(el("div", { class: "cf-grid cf-grid-2" }, [
      field("First name", fn), field("Surname", sn),
      field("Phone", ph), field("Date of birth", dob),
    ]));

    // Address
    card.appendChild(el("h3", { text: "Address", style: "margin-top:18px" }));
    var a1 = input(pr.address_line1), a2 = input(pr.address_line2);
    var city = input(pr.city), pc = input(pr.postal_code), country = input(pr.country);
    card.appendChild(field("Address line 1", a1));
    card.appendChild(field("Address line 2", a2));
    card.appendChild(el("div", { class: "cf-grid cf-grid-2" }, [
      field("City", city), field("Postal code", pc),
    ]));
    card.appendChild(field("Country", country));

    // Emergency contact
    card.appendChild(el("h3", { text: "Emergency contact", style: "margin-top:18px" }));
    var ecn = input(pr.emergency_contact_name), ecp = input(pr.emergency_contact_phone, { type: "tel" });
    card.appendChild(el("div", { class: "cf-grid cf-grid-2" }, [
      field("Contact name", ecn), field("Contact phone", ecp),
    ]));

    // Marketing consent
    var consentLbl = el("label", { class: "cf-row", style: "cursor:pointer;gap:10px;margin-top:14px" });
    var consentCb = el("input", { type: "checkbox" });
    consentCb.checked = !!pr.marketing_opt_in;
    consentCb.style.width = "auto";
    consentLbl.appendChild(consentCb);
    consentLbl.appendChild(el("span", { style: "font-weight:600",
      text: "Send me news, offers and club updates by email" }));
    card.appendChild(consentLbl);

    // Save
    var save = el("button", { class: "cf-btn cf-btn-primary cf-btn-lg", style: "margin-top:18px",
      text: "Save changes" });
    save.addEventListener("click", function () {
      var body = {
        first_name: fn.value.trim(), surname: sn.value.trim(), phone: ph.value.trim(),
        dob: dob.value || null,
        address_line1: a1.value.trim(), address_line2: a2.value.trim(),
        city: city.value.trim(), postal_code: pc.value.trim(), country: country.value.trim(),
        emergency_contact_name: ecn.value.trim(), emergency_contact_phone: ecp.value.trim(),
        marketing_opt_in: consentCb.checked,
      };
      saveProfile(body, save);
    });
    card.appendChild(el("div", { style: "margin-top:4px" }, [save]));
    host.appendChild(card);
  }

  async function saveProfile(body, btn) {
    btn.disabled = true; var orig = btn.textContent; btn.textContent = "Saving…";
    try {
      state.profile = await window.API.patchProfile(body);
      UI.toast("Profile saved.", "info");
      render();
    } catch (e) {
      btn.disabled = false; btn.textContent = orig;
      // Surface field-level validation if present.
      var fields = e && e.body && e.body.fields;
      if (e && e.status === 422 && fields) {
        var first = Object.keys(fields)[0];
        UI.toast("Please check " + first.replace(/_/g, " ") + ": " + fields[first], "error");
      } else {
        UI.toast(UI.errMsg(e), "error");
      }
    }
  }

  // ---- Family tab -----------------------------------------------------------
  function ageFromDob(dob) {
    if (!dob) return null;
    var d = new Date(dob); if (isNaN(d)) return null;
    var now = new Date();
    var a = now.getFullYear() - d.getFullYear();
    var m = now.getMonth() - d.getMonth();
    if (m < 0 || (m === 0 && now.getDate() < d.getDate())) a--;
    return a >= 0 ? a : null;
  }

  function renderFamily(host) {
    var card = el("div", { class: "cf-card" });
    card.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center" }, [
      el("h3", { text: "Children & family" }),
      el("button", { class: "cf-btn cf-btn-primary", text: "+ Add child",
        onclick: function () { dependentModal(null); } }),
    ]));
    card.appendChild(el("p", { class: "cf-muted cf-tiny",
      text: "Add a child to book courts, lessons and classes on their behalf — bookings stay on your account." }));

    if (!state.dependents.length) {
      card.appendChild(el("div", { class: "cf-empty", text: "No children added yet." }));
      host.appendChild(card);
      return;
    }
    var list = el("div", { class: "cf-list", style: "margin-top:10px" });
    state.dependents.forEach(function (d) {
      var age = ageFromDob(d.dob);
      var sub = [];
      if (d.relationship && d.relationship !== "child") sub.push(d.relationship);
      if (age != null) sub.push(age + " yrs");
      list.appendChild(el("div", { class: "cf-item" }, [
        el("span", { class: "cf-chip", text: "👤" }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: (d.first_name || "") + " " + (d.surname || "") }),
          el("div", { class: "cf-item-s", text: sub.join(" · ") || "Family member" }),
        ]),
        el("div", { class: "cf-row", style: "gap:6px" }, [
          el("button", { class: "cf-btn cf-btn-sm", text: "Edit",
            onclick: function () { dependentModal(d); } }),
          el("button", { class: "cf-btn cf-btn-sm", text: "Remove",
            onclick: function () { removeDependent(d); } }),
        ]),
      ]));
    });
    card.appendChild(list);
    host.appendChild(card);
  }

  // Add/edit child modal (reuses the my.js reschedule modal markup pattern: cf-modal-bg > cf-modal).
  function dependentModal(dep) {
    var editing = !!dep;
    var bg = el("div", { class: "cf-modal-bg" });
    var fn = input(dep && dep.first_name, { placeholder: "First name" });
    var sn = input(dep && dep.surname, { placeholder: "Surname (optional)" });
    var dob = input(dep && dep.dob, { type: "date" });
    var rel = el("select", { class: "cf-select" });
    [["child", "Child"], ["spouse", "Spouse"], ["partner", "Partner"], ["other", "Other"]].forEach(function (o) {
      rel.appendChild(el("option", { value: o[0], text: o[1],
        selected: (dep && dep.relationship === o[0]) ? "selected" : null }));
    });
    var notes = input(dep && dep.notes, { placeholder: "Notes (optional)" });

    var save = el("button", { class: "cf-btn cf-btn-primary", text: editing ? "Save" : "Add child" });
    save.addEventListener("click", function () {
      var body = {
        first_name: fn.value.trim(), surname: sn.value.trim() || null,
        dob: dob.value || null, relationship: rel.value, notes: notes.value.trim() || null,
      };
      if (!body.first_name) { UI.toast("First name is required.", "error"); return; }
      saveDependent(dep, body, save, bg);
    });

    var modal = el("div", { class: "cf-modal" }, [
      el("h2", { text: editing ? "Edit family member" : "Add a child" }),
      el("p", { class: "cf-muted cf-tiny", text: "Children don't need a login — you book and pay for them." }),
      field("First name", fn),
      field("Surname", sn),
      el("div", { class: "cf-grid cf-grid-2" }, [
        field("Date of birth", dob),
        field("Relationship", rel),
      ]),
      field("Notes", notes),
      el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px;gap:8px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: function () { document.body.removeChild(bg); } }),
        save,
      ]),
    ]);
    bg.appendChild(modal);
    document.body.appendChild(bg);
  }

  async function saveDependent(dep, body, btn, bg) {
    btn.disabled = true; var orig = btn.textContent; btn.textContent = "Saving…";
    try {
      if (dep) await window.API.patchDependent(dep.id, body);
      else await window.API.addDependent(body);
      document.body.removeChild(bg);
      await loadDependents();
      UI.toast(dep ? "Saved." : "Child added.", "info");
      render();
    } catch (e) {
      btn.disabled = false; btn.textContent = orig;
      UI.toast(UI.errMsg(e), "error");
    }
  }

  async function removeDependent(dep) {
    if (!window.confirm("Remove " + (dep.first_name || "this family member") + "?")) return;
    try {
      await window.API.removeDependent(dep.id);
      await loadDependents();
      UI.toast("Removed.", "info");
      render();
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  async function loadDependents() {
    var r = await window.API.dependents();
    state.dependents = r.dependents || [];
  }

  // ---- boot -----------------------------------------------------------------
  window.Account = {
    start: async function (principal) {
      UI = window.UI; el = UI.el;
      var host = root();
      UI.clear(host); host.appendChild(el("div", { class: "cf-loading", text: "Loading your account…" }));
      try {
        var pr = await window.API.getProfile();
        state.profile = pr;
        await loadDependents();
      } catch (e) {
        UI.clear(host);
        host.appendChild(el("div", { class: "cf-card cf-empty", text: UI.errMsg(e) }));
        return;
      }
      var hash = (location.hash || "").replace("#", "");
      if (TABS.some(function (t) { return t.k === hash; })) state.tab = hash;
      render();
    },
  };
})();
