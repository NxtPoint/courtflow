// service_editor.js — THE one place a service is edited (golden rule). A FULL-SCREEN view (not a
// popup) over the unified /api/services/<id> API, opened identically by the owner and the coach.
// Everything that makes a service work — pricing variations · payment preference · packages ·
// commission — edited together, with a single "Save & close" (no inline saves; changes batch until
// you save). Owner edits all; the coach edits all EXCEPT commission (greyed).
(function () {
  var UI, el;
  var st = { id: null, host: null, onClose: null, svc: null, m: null, del: null };

  function money(minor, ccy) { return UI.money(minor, ccy || (st.svc && st.svc.currency) || "ZAR"); }
  function fmtDur(m) {
    m = parseInt(m, 10) || 0; if (!m) return "Any length";
    var h = Math.floor(m / 60), mm = m % 60, out = [];
    if (h) out.push(h + " hr"); if (mm) out.push(mm + " min");
    return out.join(" ");
  }
  function MODE_LABEL(m) { return { online: "Pay online (card)", at_court: "Pay at the club", monthly_account: "Monthly account" }[m] || m; }
  function api(path, opts) { return window.TFAuth.apiJSON("/api/services/" + encodeURIComponent(st.id) + path, opts); }
  function inp(value, attrs) { return el("input", Object.assign({ class: "cf-input", value: value == null ? "" : value }, attrs || {})); }

  // ---- open: render a full-screen editor into the caller's host ------------
  function open(productId, opts) {
    UI = window.UI; el = UI.el; opts = opts || {};
    st.id = productId; st.host = opts.host || document.getElementById("cf-main"); st.onClose = opts.onClose || null;
    UI.clear(st.host); st.host.appendChild(el("div", { class: "cf-loading", text: "Loading service…" }));
    window.TFAuth.apiJSON("/api/services/" + encodeURIComponent(productId)).then(function (r) {
      st.svc = r.service; buildModel(); render();
    }, function (e) { UI.clear(st.host); st.host.appendChild(el("div", { class: "cf-card cf-empty", text: UI.errMsg(e) })); st.host.appendChild(backBtn()); });
  }
  function backBtn() { return el("button", { class: "cf-btn", style: "margin-top:12px", text: "← Back", onclick: close }); }
  function close() { if (st.onClose) st.onClose(); }

  function buildModel() {
    var s = st.svc;
    st.m = {
      name: s.name || "",
      payment_modes: (s.payment_modes ? s.payment_modes.slice() : (s.club_payment_methods || []).slice()),
      commission_pct: (s.commission && s.commission.effective_pct) || 0,
      variations: (s.variations || []).map(function (v) { return { price_id: v.price_id, duration_minutes: v.duration_minutes, amount_minor: v.amount_minor, peak_amount_minor: (v.peak_amount_minor != null ? v.peak_amount_minor : null) }; }),
      packages: (s.packages || []).map(function (p) { return { id: p.id, label: p.label, sessions_count: p.sessions_count, duration_minutes: p.duration_minutes, price_minor: p.price_minor, assigned: p.assigned !== false, adopt: false }; }),
      members_covered: (s.members_covered !== false),   // court services: false = PAYG-only (clay)
    };
    st.del = { variations: [], packages: [] };
  }

  // ---- render ---------------------------------------------------------------
  function render() {
    var host = st.host, s = st.svc, m = st.m; UI.clear(host);

    // sticky header: Cancel · title · Save & close
    var saveB = el("button", { class: "cf-btn cf-btn-primary", text: "Save & close" });
    saveB.addEventListener("click", function () { saveAndClose(saveB); });
    host.appendChild(el("div", { class: "cf-editbar" }, [
      el("button", { class: "cf-btn", text: "← Cancel", onclick: close }),
      el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [
        el("span", { class: "cf-chip " + (s.service_kind || ""), text: s.service_kind || "service" }),
        el("strong", { text: s.name || "Service" }),
      ]),
      el("span", { class: "cf-spacer" }), saveB,
    ]));

    // details
    var nameI = inp(m.name, { style: "max-width:360px" }); nameI.addEventListener("input", function () { m.name = nameI.value; });
    host.appendChild(el("div", { class: "cf-card" }, [el("h3", { text: "Details" }), field("Name", nameI)]));

    host.appendChild(variationsCard());
    if (s.kind === "court_booking") host.appendChild(membersCard());
    host.appendChild(paymentCard());
    host.appendChild(packagesCard());
    if (s.commission && s.commission.applies) host.appendChild(commissionCard());
  }
  function field(label, control, hint) { return el("div", { class: "cf-field" }, [el("label", { text: label }), control, hint ? el("div", { class: "cf-pref-note", text: hint }) : null].filter(Boolean)); }
  function card(title, hint) { var c = el("div", { class: "cf-card" }); c.appendChild(el("h3", { text: title })); if (hint) c.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin:-4px 0 10px", text: hint })); return c; }

  function variationsCard() {
    var isCourt = (st.svc.kind === "court_booking");
    var c = card("Pricing & variations", isCourt
      ? "The same service at different lengths — each its own price. Set an optional PEAK price to charge more during your club's peak hours (Settings → Club profile)."
      : "The same service at different lengths — each its own price.");
    var list = el("div", { class: "cf-list" });
    st.m.variations.forEach(function (v) {
      var durI = inp(v.duration_minutes, { type: "number", min: 1, style: "max-width:90px" });
      durI.addEventListener("input", function () { v.duration_minutes = parseInt(durI.value, 10) || null; });
      var amtI = inp((v.amount_minor / 100).toFixed(2), { style: "max-width:120px" });
      amtI.addEventListener("input", function () { v.amount_minor = Math.round(parseFloat(amtI.value || "0") * 100); });
      var rm = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Remove" });
      rm.addEventListener("click", function () { if (v.price_id) st.del.variations.push(v.price_id); st.m.variations.splice(st.m.variations.indexOf(v), 1); render(); });
      var row = [durI, el("span", { class: "cf-muted", text: "min →" }), amtI];
      if (isCourt) {
        // Court PEAK price (optional). Blank = no uplift (charged the normal price at all times).
        var peakI = inp(v.peak_amount_minor != null ? (v.peak_amount_minor / 100).toFixed(2) : "", { placeholder: "off-peak only", style: "max-width:120px" });
        peakI.addEventListener("input", function () { var t = peakI.value.trim(); v.peak_amount_minor = (t === "" ? null : Math.round(parseFloat(t || "0") * 100)); });
        row.push(el("span", { class: "cf-muted", text: "· peak R" }), peakI);
      }
      row.push(el("span", { class: "cf-spacer" }), rm);
      list.appendChild(el("div", { class: "cf-item" }, row));
    });
    if (!st.m.variations.length) list.appendChild(el("div", { class: "cf-empty", text: "No prices yet. Add one below." }));
    c.appendChild(list);
    c.appendChild(el("button", { class: "cf-btn cf-btn-sm", style: "margin-top:10px", text: "+ Add variation", onclick: function () { st.m.variations.push({ duration_minutes: 60, amount_minor: 0 }); render(); } }));
    return c;
  }

  function membersCard() {
    var c = card("Membership", "Whether an active member's booking of this court is free. Turn OFF for a PAYG-only court (e.g. a premium clay court) — members pay like everyone else.");
    var cb = el("input", { type: "checkbox" }); cb.style.width = "auto"; cb.checked = !!st.m.members_covered;
    cb.addEventListener("change", function () { st.m.members_covered = cb.checked; });
    c.appendChild(el("label", { class: "cf-row", style: "gap:10px;align-items:center;cursor:pointer;margin-top:6px" }, [
      cb, el("span", { style: "font-weight:600", text: "Members book this court free" }),
    ]));
    return c;
  }

  function paymentCard() {
    var c = card("Payment preference", "How clients can pay for THIS service (from the methods your club enables).");
    var enabled = st.svc.club_payment_methods || [];
    if (!enabled.length) { c.appendChild(el("div", { class: "cf-empty", text: "Enable payment methods in Settings → Club profile first." })); return c; }
    enabled.forEach(function (mode) {
      var cb = el("input", { type: "checkbox" }); cb.style.width = "auto"; cb.checked = st.m.payment_modes.indexOf(mode) >= 0;
      cb.addEventListener("change", function () { var i = st.m.payment_modes.indexOf(mode); if (cb.checked && i < 0) st.m.payment_modes.push(mode); else if (!cb.checked && i >= 0) st.m.payment_modes.splice(i, 1); });
      c.appendChild(el("label", { class: "cf-row", style: "gap:10px;align-items:center;cursor:pointer;margin-top:6px" }, [cb, el("span", { style: "font-weight:600", text: MODE_LABEL(mode) })]));
    });
    return c;
  }

  function packagesCard() {
    var c = card("Packages", "Prepaid bundles for THIS service only — buy several upfront and draw them down. A pack belongs to this service and its owner.");
    var list = el("div", { class: "cf-list" });
    st.m.packages.forEach(function (p) {
      // A LEGACY pack (no product_id) cross-shown from another same-kind service of this coach — it
      // isn't part of THIS service. Show it plainly with an explicit "Assign to this service" toggle,
      // never an editable row, so it can't be silently claimed on save. (Fixes packs bleeding across
      // a coach's Private + Semi-private services.)
      if (p.id && p.assigned === false) {
        var desc = (p.label || (p.sessions_count + " sessions")) + " · R" + ((p.price_minor || 0) / 100).toFixed(2);
        var assignBtn = el("button", { class: "cf-btn cf-btn-sm" + (p.adopt ? " cf-btn-primary" : ""), text: p.adopt ? "Will be assigned ✓" : "Assign to this service" });
        assignBtn.addEventListener("click", function () { p.adopt = !p.adopt; render(); });
        list.appendChild(el("div", { class: "cf-item", style: "flex-wrap:wrap;gap:6px;opacity:" + (p.adopt ? "1" : ".7") }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: desc }),
            el("div", { class: "cf-item-s", text: p.adopt ? "Will move to this service when you save." : "Shared from your other service — not part of this one." }),
          ]),
          el("span", { class: "cf-chip", text: "legacy" }),
          assignBtn,
        ]));
        return;
      }
      var lI = inp(p.label || "", { placeholder: "Label (optional)", style: "max-width:150px" }); lI.addEventListener("input", function () { p.label = lI.value; });
      var nI = inp(p.sessions_count, { type: "number", min: 1, style: "max-width:80px" }); nI.addEventListener("input", function () { p.sessions_count = parseInt(nI.value, 10) || null; });
      var dI = inp(p.duration_minutes, { type: "number", min: 1, placeholder: "mins", style: "max-width:80px" }); dI.addEventListener("input", function () { p.duration_minutes = parseInt(dI.value, 10) || null; });
      var aI = inp((p.price_minor / 100).toFixed(2), { style: "max-width:100px" }); aI.addEventListener("input", function () { p.price_minor = Math.round(parseFloat(aI.value || "0") * 100); });
      var vI = inp(p.validity_days || "", { type: "number", min: 0, placeholder: "never", style: "max-width:80px" }); vI.addEventListener("input", function () { p.validity_days = parseInt(vI.value, 10) || null; });
      var rm = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Remove" });
      rm.addEventListener("click", function () { if (p.id) st.del.packages.push(p.id); st.m.packages.splice(st.m.packages.indexOf(p), 1); render(); });
      var row = [lI, nI, el("span", { class: "cf-muted", text: "×" }), dI, el("span", { class: "cf-muted", text: "min · R" }), aI, el("span", { class: "cf-muted", text: "· valid" }), vI, el("span", { class: "cf-muted", text: "days" }), el("span", { class: "cf-spacer" }), rm];
      list.appendChild(el("div", { class: "cf-item", style: "flex-wrap:wrap;gap:6px" }, row));
    });
    if (!st.m.packages.length) list.appendChild(el("div", { class: "cf-empty", text: "No packages yet." }));
    c.appendChild(list);
    c.appendChild(el("button", { class: "cf-btn cf-btn-sm", style: "margin-top:10px", text: "+ Add package", onclick: function () { st.m.packages.push({ sessions_count: 5, duration_minutes: st.m.variations[0] ? st.m.variations[0].duration_minutes : 60, price_minor: 0 }); render(); } }));
    return c;
  }

  function commissionCard() {
    var co = st.svc.commission || {}, owner = !!st.svc.can_edit_commission;
    var c = card("Commission", "What the club keeps on this service. Only the club can change it.");
    var pctI = inp(st.m.commission_pct, { type: "number", min: 0, max: 100, style: "max-width:100px" });
    if (!owner) { pctI.disabled = true; pctI.style.opacity = "0.6"; }
    else pctI.addEventListener("input", function () { st.m.commission_pct = parseFloat(pctI.value); });
    if (owner) c.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin:-4px 0 8px", text: "Club default is " + (co.club_default_pct || 0) + "%. A value here overrides it for this service." }));
    else c.appendChild(el("span", { class: "cf-chip", text: "Set by the club" }));
    c.appendChild(el("div", { class: "cf-row", style: "gap:8px;align-items:center;margin-top:6px" }, [pctI, el("span", { class: "cf-muted", text: "% to the club" })]));
    return c;
  }

  // ---- save (batch) ---------------------------------------------------------
  async function saveAndClose(btn) {
    btn.disabled = true; var lbl = btn.textContent; btn.textContent = "Saving…";
    var m = st.m, del = st.del;
    try {
      var body = { name: m.name, payment_modes: m.payment_modes };
      if (st.svc.kind === "court_booking") body.members_covered = !!m.members_covered;
      if (st.svc.can_edit_commission) { var v = parseFloat(m.commission_pct); if (!isNaN(v)) body.commission_pct = Math.max(0, Math.min(100, v)); }
      await api("", { method: "PATCH", body: body });
      var isCourtSvc = (st.svc.kind === "court_booking");
      for (var i = 0; i < m.variations.length; i++) {
        var vr = m.variations[i]; if (!vr.duration_minutes) continue;
        var vbody = { duration_minutes: vr.duration_minutes, amount_minor: vr.amount_minor || 0 };
        // Peak price only meaningful for court services; send it (incl. null to clear) so it round-trips.
        if (isCourtSvc) vbody.peak_amount_minor = (vr.peak_amount_minor != null ? vr.peak_amount_minor : null);
        if (vr.price_id) await api("/variations/" + vr.price_id, { method: "PATCH", body: vbody });
        else await api("/variations", { method: "POST", body: vbody });
      }
      for (var d = 0; d < del.variations.length; d++) await api("/variations/" + del.variations[d], { method: "DELETE" });
      for (var j = 0; j < m.packages.length; j++) {
        var pk = m.packages[j]; if (!pk.sessions_count) continue;
        if (pk.id) {
          // A legacy pack cross-shown from another service that the coach did NOT claim — leave it
          // exactly as-is (never touch a pack that isn't this service's unless explicitly assigned).
          if (pk.assigned === false && !pk.adopt) continue;
          await api("/packages/" + pk.id, { method: "PATCH", body: { adopt: pk.adopt ? true : undefined, label: pk.label, sessions_count: pk.sessions_count, duration_minutes: pk.duration_minutes, price_minor: pk.price_minor || 0, validity_days: pk.validity_days || null } });
        } else await api("/packages", { method: "POST", body: { label: pk.label || null, sessions_count: pk.sessions_count, duration_minutes: pk.duration_minutes, price_minor: pk.price_minor || 0, validity_days: pk.validity_days || null } });
      }
      for (var pd = 0; pd < del.packages.length; pd++) await api("/packages/" + del.packages[pd], { method: "PATCH", body: { status: "retired" } });
      UI.toast("Saved.", "info"); close();
    } catch (e) { btn.disabled = false; btn.textContent = lbl; UI.toast(UI.errMsg(e) || "Couldn't save.", "error"); }
  }

  window.ServiceEditor = { open: open };
})();
