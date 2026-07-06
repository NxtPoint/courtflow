// analytics.js — first-party, cookieless page-view beacon (powers the Business Overview dashboard).
//
// SHARED ENGINE (replicable across sites — kept in lock-step with the 1050/ten-fifty5 repo; only the
// API default + the anon_id storage key differ per site). Sends a tiny event to POST /api/track/page
// on each page load + SPA route change, and a `leave` event on unload for time-on-site. NO cookies,
// NO third parties (not Google Analytics): a first-party `anon_id` UUID in localStorage counts UNIQUE
// visitors; referrer + UTM give acquisition source; the server adds country (Cloudflare edge header)
// + device/browser/OS (User-Agent). Uses navigator.sendBeacon (text/plain → no CORS preflight), so
// it works from the DB-less marketing site to the API host. Fire-and-forget; never blocks the page.
(function () {
  // The beacon lives on the API service (courtflow-api). Prefer an injected base, else default.
  var API = (window.__API_BASE || "https://courtflow-api.onrender.com").replace(/\/+$/, "");
  var ANON_KEY = "cf_anon_id";

  function anonId() {
    try {
      var v = localStorage.getItem(ANON_KEY);
      if (!v) {
        v = (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
          : ("a-" + Date.now() + "-" + Math.random().toString(36).slice(2));
        localStorage.setItem(ANON_KEY, v);
      }
      return v;
    } catch (e) { return null; }
  }

  function utm() {
    var p = new URLSearchParams(location.search), out = null;
    ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"].forEach(function (k) {
      var val = p.get(k);
      if (val) { (out = out || {})[k.slice(4)] = val; }   // utm_source -> source
    });
    return out;
  }

  function newPvid() {
    try { if (window.crypto && crypto.randomUUID) return crypto.randomUUID(); } catch (e) {}
    return "p-" + Date.now() + "-" + Math.random().toString(36).slice(2);
  }

  function post(payload) {
    try {
      var url = API + "/api/track/page";
      var body = JSON.stringify(payload);
      if (navigator.sendBeacon) {
        navigator.sendBeacon(url, new Blob([body], { type: "text/plain" }));
      } else {
        fetch(url, { method: "POST", body: body, keepalive: true,
          headers: { "Content-Type": "text/plain" } });
      }
    } catch (e) { /* never break the page */ }
  }

  var lastPath = null, pvid = null, startedAt = 0, leaveSent = false;
  var authed = false, authPvSent = false;   // signed-in state (set by the app via window.cfAuthed)

  function sendLeave() {
    if (leaveSent || !pvid || !startedAt) return;
    leaveSent = true;
    var ms = Date.now() - startedAt;
    if (ms < 1000 || ms > 6 * 60 * 60 * 1000) return;   // drop sub-1s + runaway tabs
    var payload = { event: "leave", path: location.pathname.slice(0, 300),
                    pvid: pvid, anon_id: anonId(), duration_ms: ms };
    if (window.__CLUB_ID__) payload.club_id = window.__CLUB_ID__;
    post(payload);
  }

  function send() {
    var here = location.pathname + location.search;
    if (here === lastPath) return;          // dedupe rapid duplicate fires
    sendLeave();                            // close the previous pageview (SPA nav)
    lastPath = here;
    pvid = newPvid(); startedAt = Date.now(); leaveSent = false;
    var payload = {
      path: location.pathname.slice(0, 300),
      referrer: (document.referrer || "").slice(0, 300),
      anon_id: anonId(),
      pvid: pvid,
      sw: (window.screen && screen.width) || window.innerWidth || null,
      lang: navigator.language || null,
    };
    try { payload.tz = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) {}
    var u = utm(); if (u) payload.utm = u;
    if (window.__CLUB_ID__) payload.club_id = window.__CLUB_ID__;
    if (authed || window.__CF_AUTHED) { payload.authed = true; authPvSent = true; }   // precise logged-in signal
    post(payload);
  }

  // The app (auth_client.js) calls this once Clerk resolves so pageviews carry a logged-in flag.
  // If we only learn we're signed in AFTER the first (anonymous) pageview already fired and the
  // user never navigates, record one authed pageview now so single-page logged-in visits count.
  window.cfAuthed = function (v) {
    v = !!v;
    if (v === authed) return;
    authed = v;
    if (authed && !authPvSent && lastPath) { lastPath = null; send(); }
  };

  // SPA route changes: wrap history pushState/replaceState + listen to pop/hash.
  function hook(name) {
    var orig = history[name];
    if (typeof orig !== "function") return;
    history[name] = function () {
      var r = orig.apply(this, arguments);
      setTimeout(send, 0);
      return r;
    };
  }
  hook("pushState"); hook("replaceState");
  window.addEventListener("popstate", send);
  window.addEventListener("hashchange", send);

  // Time-on-site: flush a leave event when the page is hidden/closed (pagehide is the reliable one).
  window.addEventListener("pagehide", sendLeave);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") sendLeave();
  });

  if (document.readyState === "complete" || document.readyState === "interactive") send();
  else window.addEventListener("DOMContentLoaded", send);
})();
