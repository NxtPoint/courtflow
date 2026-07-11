// attribution.js — first-touch ad-click / UTM capture for Google Ads offline conversions.
//
// NextPoint-ONLY. This is deliberately NOT part of the shared cookieless beacon (analytics.js),
// which is kept in lock-step with the 1050/ten-fifty5 repo. Ad-click attribution is nextpoint's
// own concern, so it lives here.
//
// Flow:
//   1. On EVERY page load, capture the FIRST gclid / gbraid / wbraid / fbclid / utm_* seen (plus the
//      landing path + referrer) into localStorage. First-touch wins — a later organic visit never
//      overwrites the original paid click.
//   2. Once the visitor is signed in (window.TFAuth resolves authed), flush the buffer ONCE to
//      POST /api/me/acquisition, which persists it onto core.acquisition (server also first-touch).
//   3. A later cron uploads the REAL downstream conversion (first booking / membership) to Google
//      Ads by gclid — so Ads optimises for people who become members, not just clickers.
//
// Marketing pages don't load TFAuth, so capture happens there and the flush fires later inside the
// authenticated app. Fire-and-forget; never blocks or breaks the page.
(function () {
  var STORE = "cf_attr", DONE = "cf_attr_flushed";
  var CLICK_KEYS = ["gclid", "gbraid", "wbraid", "fbclid"];
  var UTM_KEYS = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"];

  function capture() {
    try {
      if (localStorage.getItem(STORE)) return;            // first-touch wins — never overwrite
      var p = new URLSearchParams(location.search), attr = {}, has = false;
      CLICK_KEYS.concat(UTM_KEYS).forEach(function (k) {
        var v = p.get(k);
        if (v) { attr[k] = String(v).slice(0, 512); has = true; }
      });
      if (!has) return;                                   // organic visit, nothing to attribute
      attr.landing_page = location.pathname.slice(0, 512);
      attr.referrer = (document.referrer || "").slice(0, 512);
      attr.ts = new Date().toISOString();
      localStorage.setItem(STORE, JSON.stringify(attr));
    } catch (e) { /* storage disabled (private mode) — ignore */ }
  }

  function flush() {
    try {
      if (localStorage.getItem(DONE)) return;             // already persisted
      var raw = localStorage.getItem(STORE);
      if (!raw) return;                                   // nothing captured
      if (!window.TFAuth || !TFAuth.ready) return;        // marketing page — flush later, in the app
      TFAuth.ready().then(function () {
        if (!(TFAuth.isAuthed && TFAuth.isAuthed())) return;   // not signed in yet
        var attr;
        try { attr = JSON.parse(raw); } catch (e) { return; }
        TFAuth.apiJSON("/api/me/acquisition", { method: "POST", body: attr })
          .then(function () { try { localStorage.setItem(DONE, "1"); } catch (e) {} })
          .catch(function () { /* transient (cold start / 401 pre-auth) — retry next authed load */ });
      }).catch(function () {});
    } catch (e) { /* never break the page */ }
  }

  capture();
  // Flush when TFAuth is available. attribution.js may load before auth_client.js, so poll briefly
  // (script-order agnostic); give up after ~10s on pages that never load auth (marketing).
  var tries = 0;
  (function tick() {
    try { if (localStorage.getItem(DONE)) return; } catch (e) { return; }
    if (window.TFAuth && TFAuth.ready) { flush(); return; }
    if (++tries > 20) return;
    setTimeout(tick, 500);
  })();
})();
