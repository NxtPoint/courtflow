// auth_client.js — Clerk-only Bearer-token auth helper for the CourtFlow portal SPAs.
//
// Exposes window.TFAuth. Ported from 1050's auth_client.js but Clerk-ONLY
// (decision D6: the legacy ?email&key client path is dropped — Clerk session JWT only).
//
// "Members area, auth once" design — Clerk loads in exactly ONE place:
//   * The top frame (portal, or a page opened standalone) LOADS Clerk directly and
//     acts as a token PROVIDER for its child iframes (if any are ever embedded).
//   * Child iframes do NOT load Clerk. They RELAY to the parent via postMessage to
//     learn auth status and mint a fresh token per request (avoids the per-page
//     Clerk download + handshake lag).
//
// Every API call adds `Authorization: Bearer <session token>` and points at the API
// base (window.__API_BASE, or ?api=, default https://api.nextpointtennis.com). The
// server derives club + role from the JWT (auth/principal.py) — the client never
// asserts club_id.
//
// Config is server-substituted by Agent F's /auth_client.js serving route:
//   __AUTH_ENABLED__  __CLERK_PUBLISHABLE_KEY__  __CLERK_JWT_TEMPLATE__
// (If served as a plain static file the placeholders stay; pkOk() then fails and
//  TFAuth reports unauthenticated rather than crashing.)
(function () {
  var CFG = {
    enabled: "__AUTH_ENABLED__" === "1",
    pk: "__CLERK_PUBLISHABLE_KEY__",
    tmpl: "__CLERK_JWT_TEMPLATE__",
  };
  var P = new URLSearchParams(location.search);
  // API base: explicit window global wins, then ?api=, then the production default.
  var apiBase = (window.__API_BASE
    || P.get("api")
    || "https://api.nextpointtennis.com").trim().replace(/\/+$/, "");
  window.__API_BASE = apiBase;

  var inIframe = (window.self !== window.top);
  var clerk = null;       // top-frame Clerk instance (provider)
  var relayEmail = null;  // email learned from the parent in relay mode
  var authed = false;     // resolved authentication state
  var readyP = null;

  function pkOk(k) { return !!k && k.indexOf("__") !== 0 && /^pk_(test|live)_/.test(k); }
  function tmplOpts() { return (CFG.tmpl && CFG.tmpl.indexOf("__") !== 0) ? { template: CFG.tmpl } : undefined; }
  function frontendApi(k) {
    try { return atob(k.split("_").slice(2).join("_")).replace(/\$+$/, ""); }
    catch (e) { return null; }
  }
  function loadClerk() {
    return new Promise(function (resolve, reject) {
      var host = frontendApi(CFG.pk);
      if (!host) return reject(new Error("bad pk"));
      var s = document.createElement("script");
      s.async = true; s.crossOrigin = "anonymous";
      s.setAttribute("data-clerk-publishable-key", CFG.pk);
      s.src = "https://" + host + "/npm/@clerk/clerk-js@5/dist/clerk.browser.js";
      s.onload = resolve; s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  // ---- relay client (child iframe -> parent portal) --------------------------
  var _reqId = 0, _pending = {};
  function callParent(kind) {
    return new Promise(function (resolve) {
      var id = ++_reqId;
      _pending[id] = resolve;
      try { window.parent.postMessage({ __tfauth: 1, dir: "req", id: id, kind: kind }, "*"); }
      catch (e) { delete _pending[id]; resolve(null); return; }
      setTimeout(function () { if (_pending[id]) { delete _pending[id]; resolve(null); } }, 4000);
    });
  }

  // ---- single message listener: client responses + provider requests ---------
  window.addEventListener("message", function (e) {
    var d = e.data;
    if (!d || d.__tfauth !== 1) return;
    if (d.dir === "res" && _pending[d.id]) {
      var r = _pending[d.id]; delete _pending[d.id]; r(d.payload); return;
    }
    if (d.dir === "req" && !inIframe) {
      if (e.origin !== location.origin) return;   // never serve a token cross-origin
      serveChild(d, e.source);
    }
  });

  async function serveChild(d, src) {
    await ready();
    var payload = null;
    if (d.kind === "status") {
      payload = { authed: authed, email: email() };
    } else if (d.kind === "token") {
      if (clerk && clerk.session) {
        try { payload = await clerk.session.getToken(tmplOpts()); } catch (e2) { payload = null; }
      }
    }
    try { src.postMessage({ __tfauth: 1, dir: "res", id: d.id, payload: payload }, "*"); } catch (e3) {}
  }

  // Reject after `ms` so a hung Clerk/network call can never block the UI forever
  // (the caller catches -> the page shows an error instead of an endless spinner).
  function _withTimeout(promise, ms, label) {
    return Promise.race([
      Promise.resolve(promise),
      new Promise(function (_, reject) {
        setTimeout(function () { reject(new Error("timeout:" + (label || "op"))); }, ms);
      }),
    ]);
  }

  // ---- resolve auth state once -----------------------------------------------
  function ready() {
    if (readyP) return readyP;
    readyP = (async function () {
      if (!CFG.enabled || !pkOk(CFG.pk)) { authed = false; return; }
      if (inIframe) {
        var status = await callParent("status");
        if (status && status.authed) { authed = true; relayEmail = status.email || ""; }
        else { authed = false; }
        return;
      }
      try {
        await loadClerk();
        clerk = window.Clerk;
        await _withTimeout(clerk.load(), 15000, "clerk_load");
        authed = !!(clerk && clerk.user);
      } catch (e) { authed = false; }
    })();
    return readyP;
  }

  async function authHeaders() {
    await ready();
    if (!authed) return {};
    var token = null;
    try {
      token = inIframe ? await _withTimeout(callParent("token"), 8000, "relay_token")
                       : (clerk && clerk.session ? await _withTimeout(clerk.session.getToken(tmplOpts()), 8000, "get_token") : null);
    } catch (e) { token = null; }
    return token ? { "Authorization": "Bearer " + token } : {};
  }

  function isAuthed() {
    return inIframe ? !!relayEmail : !!(clerk && clerk.user);
  }

  function email() {
    if (inIframe) return relayEmail || "";
    if (clerk && clerk.user && clerk.user.primaryEmailAddress) {
      return clerk.user.primaryEmailAddress.emailAddress;
    }
    return "";
  }

  // apiFetch — prepend the API base, attach the Bearer header, return the raw Response.
  // A 70s AbortController timeout lets a free-tier COLD START (~30-60s wake) finish, while
  // still guaranteeing a truly-hung request rejects (caller shows an error) instead of
  // spinning forever. (A keep-warm ping or the Starter plan removes the cold start entirely.)
  async function apiFetch(path, opts) {
    opts = opts || {};
    var headers = Object.assign({}, await authHeaders(), opts.headers || {});
    var url = (path.indexOf("http") === 0) ? path : (apiBase + path);
    var ctrl = (typeof AbortController !== "undefined") ? new AbortController() : null;
    var timer = ctrl ? setTimeout(function () { try { ctrl.abort(); } catch (e) {} }, 70000) : null;
    try {
      return await fetch(url, Object.assign({}, opts, { headers: headers },
        ctrl ? { signal: ctrl.signal } : {}));
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  // apiJSON — apiFetch + JSON body helper. Throws {status, body} on non-2xx so
  // callers can surface the server's {error, message} (e.g. 409 SLOT_TAKEN).
  async function apiJSON(path, opts) {
    opts = Object.assign({}, opts || {});
    if (opts.body && typeof opts.body !== "string") {
      opts.body = JSON.stringify(opts.body);
      opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    }
    var res = await apiFetch(path, opts);
    var data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      var err = new Error((data && (data.message || data.error)) || ("HTTP " + res.status));
      err.status = res.status; err.body = data;
      throw err;
    }
    return data;
  }

  // Redirect the browser into Clerk's hosted sign-in (used when unauthenticated).
  async function requireAuth() {
    await ready();
    if (authed) return true;
    if (clerk && clerk.redirectToSignIn) {
      try { clerk.redirectToSignIn({ redirectUrl: location.href }); } catch (e) {}
    }
    return false;
  }

  async function signOut() {
    try { if (clerk && clerk.signOut) await clerk.signOut(); } catch (e) {}
  }

  window.TFAuth = {
    ready: ready,
    authHeaders: authHeaders,
    apiFetch: apiFetch,
    apiJSON: apiJSON,
    isAuthed: isAuthed,
    email: email,
    requireAuth: requireAuth,
    signOut: signOut,
    apiBase: function () { return apiBase; },
  };
})();
