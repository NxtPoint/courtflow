// analytics.js — first-party page-view beacon (powers the Business Overview dashboard).
//
// Sends a tiny event to POST /api/track/page on each page load + SPA route change. NO cookies,
// NO third parties (not Google Analytics): a first-party `anon_id` UUID in localStorage lets us
// count UNIQUE visitors; referrer + UTM give acquisition source; the server adds country from the
// Cloudflare edge header. Uses navigator.sendBeacon (text/plain → no CORS preflight), so it works
// from the DB-less marketing site to the API host. Fire-and-forget; never blocks the page.
(function () {
  // The beacon lives on the API service (courtflow-api). Prefer an injected base, else default.
  var API = (window.__API_BASE || "https://courtflow-api.onrender.com").replace(/\/+$/, "");

  function anonId() {
    try {
      var k = "cf_anon_id", v = localStorage.getItem(k);
      if (!v) {
        v = (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
          : ("a-" + Date.now() + "-" + Math.random().toString(36).slice(2));
        localStorage.setItem(k, v);
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

  var lastPath = null;
  function send() {
    var here = location.pathname + location.search;
    if (here === lastPath) return;          // dedupe rapid duplicate fires
    lastPath = here;
    var payload = {
      path: location.pathname.slice(0, 300),
      referrer: (document.referrer || "").slice(0, 300),
      anon_id: anonId(),
    };
    var u = utm(); if (u) payload.utm = u;
    if (window.__CLUB_ID__) payload.club_id = window.__CLUB_ID__;
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

  if (document.readyState === "complete" || document.readyState === "interactive") send();
  else window.addEventListener("DOMContentLoaded", send);
})();
