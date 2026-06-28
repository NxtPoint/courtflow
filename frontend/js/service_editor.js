// service_editor.js — THE one place a service is edited (golden rule). A single modal over the
// unified /api/services/<id> API, opened identically by the owner and the coach. Manages everything
// that makes a service work: pricing variations · payment preference · packages · commission. The
// owner edits all; the coach edits all EXCEPT commission (greyed). Self-contained over window.TFAuth.
(function () {
  var UI, el;
  var st = { id: null, svc: null, bg: null, onSaved: null };

  function money(minor, ccy) { return UI.money(minor, ccy || (st.svc && st.svc.currency) || "ZAR"); }
  function fmtDur(m) {
    m = parseInt(m, 10) || 0; if (!m) return "Any length";
    var h = Math.floor(m / 60), mm = m % 60, out = [];
    if (h) out.push(h + " hr"); if (mm) out.push(mm + " min");
    return out.join(" ");
  }
  function MODE_LABEL(m) {
    return { online: "Pay online (card)", at_court: "Pay at the club", monthly_account: "Monthly account" }[m] || m;
  }

  function api(path, opts) { return window.TFAuth.apiJSON("/api/services/" + encodeURIComponent(st.id) + path, opts); }
  function refresh(r) { if (r && r.service) { st.svc = r.service; render(); } }
  function fail(e) { UI.toast(UI.errMsg(e) || "Couldn't save.", "error"); }

  // ---- shell ----------------------------------------------------------------
  function open(productId, opts) {
    UI = window.UI; el = UI.el;
    st.id = productId; st.onSaved = (opts && opts.onSaved) || null; st.svc = null;
    var bg = el("div", { class: "cf-modal-bg" });
    bg.appendChild(el("div", { class: "cf-modal cf-modal-lg", id: "cf-svc" }, [el("div", { class: "cf-loading", text: "Loading service…" })]));
    document.body.appendChild(bg); st.bg = bg;
    window.TFAuth.apiJSON("/api/services/" + encodeURIComponent(productId)).then(function (r) {
      st.svc = r.service; render();
    }, function (e) {
      var m = document.getElementById("cf-svc"); if (m) { m.innerHTML = ""; m.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); m.appendChild(closeRow()); }
    });
  }
  function close() { if (st.bg && st.bg.parentNode) document.body.removeChild(st.bg); st.bg = null; if (st.onSaved) st.onSaved(); }
  function closeRow() { return el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:14px" }, [el("button", { class: "cf-btn", text: "Done", onclick: close })]); }

  function sectionCard(title, hint) {
    var c = el("div", { class: "cf-card" });
    c.appendChild(el("h3", { text: title }));
    if (hint) c.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin:-4px 0 10px", text: hint }));
    return c;
  }

  // ---- 1. header ------------------------------------------------------------
  function header(svc) {
    var nameI = el("input", { class: "cf-input", value: svc.name || "", style: "max-width:320px;font-weight:700" });
    var save = el("button", { class: "cf-btn cf-btn-sm", text: "Save" });
    save.addEventListener("click", function () { api("", { method: "PATCH", body: { name: nameI.value.trim() } }).then(refresh, fail).then(function () { UI.toast("Saved.", "info"); }); });
    return el("div", { class: "cf-card" }, [
      el("div", { class: "cf-row", style: "justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px" }, [
        el("div", { class: "cf-row", style: "gap:8px;align-items:center;flex-wrap:wrap" }, [
          el("span", { class: "cf-chip " + (svc.service_kind || ""), text: svc.service_kind || "service" }),
          nameI, save,
        ]),
        el("button", { class: "cf-btn cf-btn-sm", text: "✕", title: "Close", onclick: close }),
      ]),
    ]);
  }

  // ---- 2. variations (per-duration prices) ----------------------------------
  function variationsSection(svc) {
    var card = sectionCard("Pricing & variations", "The same service at different lengths — each its own price.");
    var list = el("div", { class: "cf-list" });
    (svc.variations || []).forEach(function (v) {
      var amt = el("input", { class: "cf-input", value: (v.amount_minor / 100).toFixed(2), style: "max-width:120px" });
      var saveB = el("button", { class: "cf-btn cf-btn-sm", text: "Save" });
      saveB.addEventListener("click", function () {
        api("/variations/" + v.price_id, { method: "PATCH", body: { amount_minor: Math.round(parseFloat(amt.value || "0") * 100) } }).then(refresh, fail).then(function () { UI.toast("Saved.", "info"); });
      });
      var rmB = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Remove" });
      rmB.addEventListener("click", function () { if (confirm("Remove the " + fmtDur(v.duration_minutes) + " option?")) api("/variations/" + v.price_id, { method: "DELETE" }).then(refresh, fail); });
      var row = el("div", { class: "cf-item" }, [
        el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: fmtDur(v.duration_minutes) })]),
        el("span", { class: "cf-muted", text: (svc.currency || "R") === "ZAR" ? "R" : svc.currency }), amt,
        el("span", { class: "cf-spacer" }), saveB, rmB,
      ]);
      if ((v.status || "active") !== "active") row.style.opacity = "0.6";
      list.appendChild(row);
    });
    if (!(svc.variations || []).length) list.appendChild(el("div", { class: "cf-empty", text: "No prices yet. Add the first below." }));
    card.appendChild(list);
    // add
    var nDur = el("input", { class: "cf-input", type: "number", min: 1, placeholder: "mins (e.g. 60)", style: "max-width:130px" });
    var nAmt = el("input", { class: "cf-input", placeholder: "0.00", style: "max-width:120px" });
    var addB = el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Add variation" });
    addB.addEventListener("click", function () {
      var d = parseInt(nDur.value, 10);
      if (!d || d < 1) { UI.toast("Enter the length in minutes.", "warn"); return; }
      api("/variations", { method: "POST", body: { duration_minutes: d, amount_minor: Math.round(parseFloat(nAmt.value || "0") * 100) } }).then(refresh, fail).then(function () { UI.toast("Added.", "info"); });
    });
    card.appendChild(el("div", { class: "cf-row", style: "gap:6px;align-items:center;margin-top:10px;flex-wrap:wrap" }, [nDur, nAmt, addB]));
    return card;
  }

  // ---- 3. payment preference -------------------------------------------------
  function paymentSection(svc) {
    var card = sectionCard("Payment preference", "How clients can pay for THIS service (from the methods your club enables).");
    var enabled = svc.club_payment_methods || [];
    if (!enabled.length) { card.appendChild(el("div", { class: "cf-empty", text: "Enable payment methods in Settings → Club profile first." })); return card; }
    var current = svc.payment_modes; // null = all enabled
    var boxes = {};
    enabled.forEach(function (m) {
      var cb = el("input", { type: "checkbox" }); cb.style.width = "auto";
      cb.checked = (current == null) || current.indexOf(m) >= 0;
      boxes[m] = cb;
      card.appendChild(el("label", { class: "cf-row", style: "gap:10px;align-items:center;cursor:pointer;margin-top:6px" }, [cb, el("span", { style: "font-weight:600", text: MODE_LABEL(m) })]));
    });
    var save = el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", style: "margin-top:10px", text: "Save payment options" });
    save.addEventListener("click", function () {
      var chosen = enabled.filter(function (m) { return boxes[m].checked; });
      if (!chosen.length) { UI.toast("Pick at least one way to pay.", "warn"); return; }
      api("", { method: "PATCH", body: { payment_modes: chosen } }).then(refresh, fail).then(function () { UI.toast("Saved.", "info"); });
    });
    card.appendChild(save);
    return card;
  }

  // ---- 4. packages -----------------------------------------------------------
  function packagesSection(svc) {
    var card = sectionCard("Packages", "Prepaid bundles for this service — buy several upfront and draw them down.");
    var list = el("div", { class: "cf-list" });
    (svc.packages || []).forEach(function (pk) {
      var per = pk.sessions_count ? Math.round(pk.price_minor / pk.sessions_count) : pk.price_minor;
      var rmB = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Retire" });
      rmB.addEventListener("click", function () { if (confirm("Retire this package?")) api("/packages/" + pk.id, { method: "PATCH", body: { status: "retired" } }).then(refresh, fail); });
      var row = el("div", { class: "cf-item" }, [
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: (pk.label || (pk.sessions_count + " sessions")) }),
          el("div", { class: "cf-item-s", text: pk.sessions_count + " × " + fmtDur(pk.duration_minutes) + " · " + money(per) + " each" }),
        ]),
        el("strong", { text: money(pk.price_minor) }),
        el("span", { class: "cf-spacer" }), rmB,
      ]);
      if ((pk.status || "active") !== "active") row.style.opacity = "0.6";
      list.appendChild(row);
    });
    if (!(svc.packages || []).length) list.appendChild(el("div", { class: "cf-empty", text: "No packages yet." }));
    card.appendChild(list);
    var nN = el("input", { class: "cf-input", type: "number", min: 1, placeholder: "# sessions", style: "max-width:110px" });
    var nDur = el("input", { class: "cf-input", type: "number", min: 1, placeholder: "mins each", style: "max-width:110px" });
    var nAmt = el("input", { class: "cf-input", placeholder: "0.00 total", style: "max-width:120px" });
    var addB = el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Add package" });
    addB.addEventListener("click", function () {
      var n = parseInt(nN.value, 10);
      if (!n || n < 1) { UI.toast("How many sessions?", "warn"); return; }
      api("/packages", { method: "POST", body: { sessions_count: n, duration_minutes: parseInt(nDur.value, 10) || null, price_minor: Math.round(parseFloat(nAmt.value || "0") * 100) } }).then(refresh, fail).then(function () { UI.toast("Package added.", "info"); });
    });
    card.appendChild(el("div", { class: "cf-row", style: "gap:6px;align-items:center;margin-top:10px;flex-wrap:wrap" }, [nN, nDur, nAmt, addB]));
    return card;
  }

  // ---- 5. commission (owner edits; coach greyed) ----------------------------
  function commissionSection(svc) {
    var c = svc.commission || {};
    var card = sectionCard("Commission", "What the club keeps on this service. Only the club can change it.");
    var owner = !!svc.can_edit_commission;
    var pctI = el("input", { class: "cf-input", type: "number", min: 0, max: 100, value: (c.effective_pct || 0), style: "max-width:100px" });
    if (!owner) { pctI.disabled = true; pctI.style.opacity = "0.6"; }
    var rowKids = [pctI, el("span", { class: "cf-muted", text: "% to the club" })];
    if (owner) {
      var save = el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Save" });
      save.addEventListener("click", function () {
        var v = parseFloat(pctI.value);
        if (isNaN(v) || v < 0 || v > 100) { UI.toast("Enter 0–100.", "warn"); return; }
        api("", { method: "PATCH", body: { commission_pct: v } }).then(refresh, fail).then(function () { UI.toast("Commission saved.", "info"); });
      });
      rowKids.push(save);
      card.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin:-4px 0 8px", text: "Club default is " + (c.club_default_pct || 0) + "%. Setting a value here overrides it for this service." }));
    } else {
      card.appendChild(el("span", { class: "cf-chip", text: "Set by the club" }));
    }
    card.appendChild(el("div", { class: "cf-row", style: "gap:8px;align-items:center;margin-top:6px" }, rowKids));
    return card;
  }

  function render() {
    var m = document.getElementById("cf-svc"); if (!m) return;
    m.innerHTML = "";
    var svc = st.svc;
    m.appendChild(header(svc));
    m.appendChild(variationsSection(svc));
    m.appendChild(paymentSection(svc));
    m.appendChild(packagesSection(svc));
    if (svc.commission && svc.commission.applies) m.appendChild(commissionSection(svc));
  }

  window.ServiceEditor = { open: open, close: close };
})();
