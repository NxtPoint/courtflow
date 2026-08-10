// widgets/game.js — Widgets.Game + Widgets.GameList: the ONE render of a game and of the
// Find-a-Game feed, shared by the client, coach and admin apps (the GOLDEN RULE — a second render of
// a capability is a bug; role differences are CONFIG).
//
// A GAME IS A BOOKING, so this widget deliberately does NOT duplicate the booking record — the money
// history, receipts, refunds and the audit log all stay on Widgets.TransactionDetail, reached by
// cfg.onNavigate({kind:"booking"}). What lives here is only what a booking has never had: who is on
// the court, what each of them owes, and the conversation.
//
// cfg for Game:
//   cfg.data.get(id)        -> Promise<game>          (GET /api/community/games/<id>)
//   cfg.data.chat(id)       -> Promise<{messages}>    (GET .../chat)
//   cfg.actions             -> { join, leave, invite, post, result, playAgain } — a button appears
//                              only when the payload's can{} allows it AND the app wired a handler
//   cfg.onNavigate          -> fn({kind, id})
//   cfg.me                  -> the viewer's user_id (for "you")
//
// The widget is pure render + events: no endpoints, no location.hash, no globals mutated. Money is
// rendered ONLY for the viewer's own seat — the API deliberately returns null for everyone else's,
// because what another player owes is between them and the club.
(function () {
  // Shared helpers only — no local money formatter, no local card. UI.money already handles the
  // currency symbol and the null case, and a second one here is exactly the drift the golden rule
  // exists to stop.
  var el = window.UI.el, card = window.UI.card, money = window.UI.money;

  // What kind of tennis this is — a SEPARATE axis from singles/doubles (which is a seat
  // count, i.e. a money field). Mismatched intent spoils a session as reliably as a
  // mismatched level, so it is shown on the card, not buried in the chat.
  var INTENT = { social: "Social hit", practice: "Practice", competitive: "Competitive" };

  function seatChip(seat) {
    // The vocabulary a player actually needs: am I paid for, or do I owe? "covered" is deliberately
    // worded as a benefit ("Included") rather than as jargon.
    if (seat.covered) return { text: "Included", tone: "ok" };
    if (seat.seat_status === "collapsed") return { text: "Unfilled seat", tone: "warn" };
    if (seat.paid === true) return { text: "Paid", tone: "ok" };
    if (seat.paid === false) return { text: "Awaiting payment", tone: "warn" };
    return { text: "", tone: "" };
  }

  function seatRow(seat, cfg) {
    var chip = seatChip(seat);
    var name = seat.is_me ? "You" : (seat.name || "Open seat");
    var sub = [];
    if (seat.role === "host") sub.push("booked the court");
    if (seat.is_me && seat.amount_minor) sub.push("you owe " + money(seat.amount_minor));
    var right = [];
    if (chip.text) right.push(el("span", { class: "cf-chip cf-chip-" + (chip.tone || ""), text: chip.text }));
    if (seat.is_me && seat.paid === false && cfg.actions && cfg.actions.pay) {
      right.push(el("button", {
        class: "cf-btn cf-btn-primary cf-btn-sm", text: "Pay " + money(seat.amount_minor),
        onclick: function () { cfg.actions.pay.run(seat); }
      }));
    }
    return el("div", { class: "cf-item" }, [
      el("div", { class: "cf-item-main" }, [
        el("div", { class: "cf-item-t", text: name }),
        sub.length ? el("div", { class: "cf-item-s", text: sub.join(" · ") }) : null
      ].filter(Boolean)),
      el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, right)
    ]);
  }

  function openSeatRow(cfg, game) {
    // The empty seat is the product. It is rendered as an invitation, not as an absence — and it
    // carries the honest warning that an unfilled seat becomes the holder's to pay for, so a charge
    // at the cutoff is never a surprise.
    var acts = [];
    if (game.can && game.can.join && cfg.actions && cfg.actions.join) {
      acts.push(el("button", {
        class: "cf-btn cf-btn-primary cf-btn-sm", text: "Take this seat",
        onclick: function () { cfg.actions.join.run(game); }
      }));
    }
    if (game.can && game.can.invite && cfg.actions && cfg.actions.invite) {
      acts.push(el("button", {
        class: "cf-btn cf-btn-sm", text: "Invite someone",
        onclick: function () { cfg.actions.invite.run(game); }
      }));
    }
    return el("div", { class: "cf-item cf-item-dashed" }, [
      el("div", { class: "cf-item-main" }, [
        el("div", { class: "cf-item-t", text: "Open seat" }),
        el("div", { class: "cf-item-s", text: "Anyone can take it — they pay their share" })
      ]),
      el("div", { class: "cf-row", style: "gap:8px" }, acts)
    ]);
  }

  function chatBlock(cfg, game, msgs) {
    var list = el("div", { class: "cf-chat" });
    (msgs || []).forEach(function (m) {
      list.appendChild(el("div", { class: "cf-chat-line" + (m.system ? " cf-chat-sys" : "") }, [
        m.system ? null : el("span", { class: "cf-chat-who", text: (m.is_me ? "You" : m.name) + ": " }),
        el("span", { text: m.body })
      ].filter(Boolean)));
    });
    if (!(msgs || []).length) {
      list.appendChild(el("div", { class: "cf-empty", text: "No messages yet — say hello." }));
    }
    var input = el("input", { class: "cf-input", placeholder: "Message the other players…" });
    function send() {
      var body = input.value.trim();
      if (!body) return;
      input.value = "";
      Promise.resolve(cfg.actions.post.run(game, body)).then(function () { api.refresh(); },
        function (e) { window.UI.toast(window.UI.errMsg(e), "error"); });
    }
    input.addEventListener("keydown", function (ev) { if (ev.key === "Enter") send(); });
    var box = el("div", {}, [list]);
    if (cfg.actions && cfg.actions.post) {
      box.appendChild(el("div", { class: "cf-row", style: "gap:8px;margin-top:8px" }, [
        input, el("button", { class: "cf-btn cf-btn-sm", text: "Send", onclick: send })
      ]));
    }
    return card("Chat", box);
  }

  var api = null;

  function mount(host, cfg) {
    cfg = cfg || {};
    var gameId = cfg.id;

    function render(game, msgs) {
      window.UI.clear(host);
      var when = "";
      try {
        when = window.UI.fmtDate(game.starts_at) + " · " + window.UI.fmtTime(game.starts_at)
          + "–" + window.UI.fmtTime(game.ends_at);
      } catch (e) { when = ""; }

      var title = (game.play_format === "doubles" ? "Doubles" : "Singles") + " game";
      if (game.play_intent) title = (INTENT[game.play_intent] || title) + " · " + title.toLowerCase();
      host.appendChild(window.UI.pageHeader
        ? window.UI.pageHeader(title, when)
        : el("h2", { text: title }));

      // The state banner: a held game is the one thing a player must understand at a glance, because
      // the court is not theirs until everybody has paid.
      if (game.status === "held") {
        host.appendChild(el("div", { class: "cf-banner cf-banner-warn" }, [
          el("strong", { text: "Waiting on payment. " }),
          el("span", { text: "The court is held for now — it's confirmed once every player has paid their share." })
        ]));
      }

      var seats = el("div", { class: "cf-list" });
      (game.seats || []).forEach(function (s) { seats.appendChild(seatRow(s, cfg)); });
      for (var i = 0; i < (game.open_seats || 0); i++) seats.appendChild(openSeatRow(cfg, game));
      host.appendChild(card("Who's playing", seats));

      var acts = [];
      if (game.can && game.can.leave && cfg.actions && cfg.actions.leave) {
        acts.push(el("button", {
          class: "cf-btn cf-btn-danger", text: "Leave this game",
          onclick: function () { cfg.actions.leave.run(game); }
        }));
      }
      if (cfg.actions && cfg.actions.result && new Date(game.ends_at) < new Date()) {
        acts.push(el("button", {
          class: "cf-btn cf-btn-primary", text: "Enter the result",
          onclick: function () { cfg.actions.result.run(game); }
        }));
      }
      if (cfg.onNavigate) {
        acts.push(el("button", {
          class: "cf-btn cf-btn-ghost", text: "Booking details",
          onclick: function () { cfg.onNavigate({ kind: "booking", id: game.booking_id }); }
        }));
      }
      if (acts.length) host.appendChild(el("div", { class: "cf-row cf-actions", style: "gap:8px" }, acts));

      host.appendChild(chatBlock(cfg, game, msgs));
    }

    function load() {
      return Promise.all([
        cfg.data.get(gameId),
        cfg.data.chat ? cfg.data.chat(gameId).then(function (r) { return (r && r.messages) || []; },
          function () { return []; }) : Promise.resolve([])
      ]).then(function (both) { render(both[0], both[1]); },
        function (e) {
          window.UI.clear(host);
          host.appendChild(el("div", { class: "cf-empty", text: window.UI.errMsg(e) }));
        });
    }

    api = { refresh: load, destroy: function () { window.UI.clear(host); } };
    load();
    return api;
  }

  // ---- the Find-a-Game feed -------------------------------------------------
  function mountList(host, cfg) {
    cfg = cfg || {};

    function row(g) {
      var when = "";
      try {
        when = window.UI.fmtDate(g.starts_at) + " · " + window.UI.fmtTime(g.starts_at);
      } catch (e) { when = ""; }
      var bits = [g.court_name, g.play_format === "doubles" ? "Doubles" : "Singles"];
      if (g.play_intent) bits.push(INTENT[g.play_intent] || g.play_intent);
      if (g.host_level) bits.push("Level " + g.host_level);
      var n = el("button", { class: "cf-item cf-item-click", style: "width:100%;text-align:left" }, [
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: when + " · " + (g.host_name || "A member") }),
          el("div", { class: "cf-item-s", text: bits.filter(Boolean).join(" · ") })
        ]),
        el("span", {
          class: "cf-chip " + (g.im_in ? "cf-chip-ok" : ""),
          text: g.im_in ? "You're in" : (g.open_seats + (g.open_seats === 1 ? " seat" : " seats"))
        })
      ]);
      n.addEventListener("click", function () {
        if (cfg.onNavigate) cfg.onNavigate({ kind: "game", id: g.booking_id });
      });
      return n;
    }

    function render(games) {
      window.UI.clear(host);
      if (!games.length) {
        host.appendChild(el("div", { class: "cf-empty" }, [
          el("div", { text: "No open games right now." }),
          cfg.actions && cfg.actions.create
            ? el("button", {
              class: "cf-btn cf-btn-primary", style: "margin-top:10px", text: "Book a court and open it up",
              onclick: function () { cfg.actions.create.run(); }
            }) : null
        ].filter(Boolean)));
        return;
      }
      var list = el("div", { class: "cf-list" });
      games.forEach(function (g) { list.appendChild(row(g)); });
      host.appendChild(list);
    }

    function load() {
      return cfg.data.list().then(function (r) { render((r && r.games) || []); },
        function (e) {
          window.UI.clear(host);
          host.appendChild(el("div", { class: "cf-empty", text: window.UI.errMsg(e) }));
        });
    }
    load();
    return { refresh: load, destroy: function () { window.UI.clear(host); } };
  }

  window.Widgets.Game = { mount: mount };
  window.Widgets.GameList = { mount: mountList };
})();
