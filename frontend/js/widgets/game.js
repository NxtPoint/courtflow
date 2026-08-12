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
//   cfg.actions             -> { join, leave, invite, post, pay, result, confirmResult, playAgain }
//                              — a button appears only when the payload's can{} allows it AND the app
//                              wired a handler. result.run(game, body) takes the whole result body.
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
  var INTENT = window.CFIntent;      // ONE vocabulary, defined in crm_ui.js — never a local copy

  function seatChip(seat) {
    // The vocabulary a player actually needs: am I paid for, or do I owe? "covered" is deliberately
    // worded as a benefit ("Included") rather than as jargon.
    if (seat.covered) return { text: "Included", tone: "ok" };          // .cf-chip.ok  (existing)
    if (seat.seat_status === "collapsed") return { text: "Unfilled seat", tone: "held" };
    if (seat.paid === true) return { text: "Paid", tone: "ok" };
    if (seat.paid === false) return { text: "Awaiting payment", tone: "held" };
    return { text: "", tone: "" };
  }

  function seatRow(seat, cfg) {
    var chip = seatChip(seat);
    var name = seat.is_me ? "You" : (seat.name || "Open seat");
    var sub = [];
    if (seat.role === "host") sub.push("booked the court");
    if (seat.is_me && seat.amount_minor) sub.push("you owe " + money(seat.amount_minor));
    var right = [];
    if (chip.text) right.push(el("span", { class: "cf-chip " + (chip.tone || ""), text: chip.text }));
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

  // ---- the result ----------------------------------------------------------
  // What happened, and the other player agreeing it happened. An unconfirmed result is a CLAIM, not
  // evidence (results.py), so the render says which it is rather than showing a bare scoreline that
  // reads as settled fact.
  var OUTCOMES = [
    { key: "played", label: "We played" },
    { key: "cancelled", label: "Called off" },
    { key: "no_show", label: "Someone didn't turn up" }
  ];

  function outcomeWord(o) {
    for (var i = 0; i < OUTCOMES.length; i++) if (OUTCOMES[i].key === o) return OUTCOMES[i].label;
    return o || "";
  }

  function resultModal(cfg, game) {
    var m = window.UI.modal("Enter the result", {});
    var chosen = (game.result && game.result.outcome) || "played";
    var winner = (game.result && game.result.winner_user_id) || "";

    var outRow = el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap" });
    var btns = {};
    function paint() {
      OUTCOMES.forEach(function (o) {
        btns[o.key].className = "cf-btn cf-btn-sm" + (chosen === o.key ? " cf-btn-primary" : "");
      });
      // A winner and a score only make sense for a game that was actually played.
      playedBox.style.display = (chosen === "played") ? "" : "none";
    }
    OUTCOMES.forEach(function (o) {
      var b = el("button", { type: "button", class: "cf-btn cf-btn-sm", text: o.label });
      b.addEventListener("click", function () { chosen = o.key; paint(); });
      btns[o.key] = b;
      outRow.appendChild(b);
    });

    var who = el("select", { class: "cf-select" });
    // "No winner" is FIRST and is the default: most club tennis is a hit, not a match, and forcing a
    // winner would either produce junk data or stop people filing a result at all.
    who.appendChild(el("option", { value: "", text: "No winner — just a hit" }));
    (game.seats || []).forEach(function (s) {
      if (!s.user_id) return;
      who.appendChild(el("option", {
        value: s.user_id, text: s.is_me ? "You" : (s.name || "Player"),
        selected: String(winner) === String(s.user_id) ? "selected" : null
      }));
    });
    var score = el("input", {
      class: "cf-input", placeholder: "6-4 6-3 (optional)",
      value: (game.result && game.result.score_text) || ""
    });
    var playedBox = el("div", {}, [
      el("div", { class: "cf-field" }, [el("label", { text: "Winner" }), who]),
      el("div", { class: "cf-field" }, [el("label", { text: "Score" }), score])
    ]);

    m.body.appendChild(el("p", {
      class: "cf-muted", style: "margin:0 0 10px;font-size:.85rem",
      text: game.result && game.result.confirmed
        ? "This result was already agreed. Changing it withdraws that agreement and the other player is asked again."
        : "Whoever else played can confirm it. Until they do it's recorded as unconfirmed."
    }));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "What happened" }), outRow]));
    m.body.appendChild(playedBox);
    paint();

    var save = el("button", { class: "cf-btn cf-btn-primary", text: "Save result" });
    m.body.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:12px" }, [
      el("button", { class: "cf-btn", text: "Cancel", onclick: m.close }), save
    ]));
    save.addEventListener("click", function () {
      save.disabled = true;
      Promise.resolve(cfg.actions.result.run(game, {
        outcome: chosen,
        winner_user_id: (chosen === "played" && who.value) ? who.value : null,
        score_text: (chosen === "played" && score.value.trim()) ? score.value.trim() : null
      })).then(function () { m.close(); api.refresh(); },
        function (e) { save.disabled = false; window.UI.toast(window.UI.errMsg(e), "error"); });
    });
  }

  function resultBlock(cfg, game) {
    var r = game.result;
    var kids = [el("h2", { style: "margin:0 0 8px", text: "Result" })];
    if (!r) {
      kids.push(el("div", { class: "cf-empty", text: "No result recorded yet." }));
    } else {
      var line = outcomeWord(r.outcome);
      if (r.outcome === "played") {
        var wname = "";
        (game.seats || []).forEach(function (s) {
          if (r.winner_user_id && String(s.user_id) === String(r.winner_user_id)) {
            wname = s.is_me ? "You" : (s.name || "");
          }
        });
        if (wname) line += " · " + wname + " won";
        if (r.score_text) line += " · " + r.score_text;
      }
      kids.push(el("div", { class: "cf-item" }, [
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: line }),
          el("div", {
            class: "cf-item-s",
            text: r.confirmed ? "Agreed by both players"
              : (r.reported_by_me ? "Waiting for the other player to confirm"
                : "Reported by the other player — not confirmed yet")
          })
        ]),
        el("span", {
          class: "cf-chip " + (r.confirmed ? "ok" : "held"),
          text: r.confirmed ? "Confirmed" : "Unconfirmed"
        })
      ]));
    }
    var acts = [];
    if (game.can && game.can.confirm_result && cfg.actions && cfg.actions.confirmResult) {
      acts.push(el("button", {
        class: "cf-btn cf-btn-primary cf-btn-sm", text: "That's right — confirm",
        onclick: function () { cfg.actions.confirmResult.run(game); }
      }));
    }
    if (game.can && game.can.record_result && cfg.actions && cfg.actions.result) {
      acts.push(el("button", {
        class: "cf-btn cf-btn-sm", text: r ? "Change the result" : "Enter the result",
        onclick: function () { resultModal(cfg, game); }
      }));
    }
    if (acts.length) kids.push(el("div", { class: "cf-row", style: "gap:8px;margin-top:10px;flex-wrap:wrap" }, acts));
    return card(kids);
  }

  // ---- would you play them again? ------------------------------------------
  // PRIVATE. The answer is never shown to its subject, never aggregated into a public score and
  // never rendered anywhere but here, on the rater's own screen. It exists only to weight matching —
  // which is exactly why people answer it honestly, and why the copy says so out loud.
  function rateBlock(cfg, game) {
    if (!(game.rate || []).length || !cfg.actions || !cfg.actions.playAgain) return null;
    var list = el("div", { class: "cf-list" });
    (game.rate || []).forEach(function (p) {
      var yes = el("button", { type: "button", class: "cf-btn cf-btn-sm", text: "Yes" });
      var no = el("button", { type: "button", class: "cf-btn cf-btn-sm", text: "Rather not" });
      function paint(v) {
        yes.className = "cf-btn cf-btn-sm" + (v === true ? " cf-btn-primary" : "");
        no.className = "cf-btn cf-btn-sm" + (v === false ? " cf-btn-danger" : "");
      }
      paint(p.again === true ? true : (p.again === false ? false : null));
      function send(v) {
        paint(v);
        Promise.resolve(cfg.actions.playAgain.run(game, p.user_id, v)).then(
          function () { p.again = v; },
          function (e) { paint(p.again); window.UI.toast(window.UI.errMsg(e), "error"); });
      }
      yes.addEventListener("click", function () { send(true); });
      no.addEventListener("click", function () { send(false); });
      list.appendChild(el("div", { class: "cf-item" }, [
        el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: p.name || "Player" })]),
        el("div", { class: "cf-row", style: "gap:6px" }, [yes, no])
      ]));
    });
    return card([
      el("h2", { style: "margin:0 0 4px", text: "Would you play them again?" }),
      el("p", { class: "cf-muted cf-tiny", style: "margin:0 0 8px",
        text: "Only you ever see this. It's never shown to them — it just helps us suggest better games." }),
      list
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
    return card([el("h2", { style: "margin:0 0 8px", text: "Chat" }), box]);
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

      // WHEN THE GAME IS is the most important fact on this screen, so it is rendered as information.
      // It used to be passed as UI.pageHeader's SECOND argument — which is the BACK-LINK LABEL — so
      // the date came out as "‹ Sat, 15 Aug · 14:00–15:30" with a chevron, and tapping the date
      // navigated the member away from the game (history.back()). The host app already supplies the
      // real back header, so this widget must not render a second one.
      var title = INTENT.word(game.play_intent)
        ? INTENT.word(game.play_intent) + " · " + INTENT.format(game.play_format || "singles")
        : INTENT.format(game.play_format || "singles") + " game";
      host.appendChild(el("div", { style: "margin:0 0 12px" }, [
        el("h1", { class: "cf-ph-title", style: "margin:0", text: title }),
        when ? el("div", { class: "cf-muted", style: "margin-top:2px", text: when }) : null,
      ].filter(Boolean)));

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
      host.appendChild(card([el("h2", { style: "margin:0 0 8px", text: "Who's playing" }), seats]));

      var acts = [];
      if (game.can && game.can.leave && cfg.actions && cfg.actions.leave) {
        acts.push(el("button", {
          class: "cf-btn cf-btn-danger", text: "Leave this game",
          onclick: function () { cfg.actions.leave.run(game); }
        }));
      }
      if (cfg.onNavigate) {
        acts.push(el("button", {
          class: "cf-btn cf-btn-ghost", text: "Booking details",
          onclick: function () { cfg.onNavigate({ kind: "booking", id: game.booking_id }); }
        }));
      }
      if (acts.length) host.appendChild(el("div", { class: "cf-row", style: "gap:8px;margin-top:12px;flex-wrap:wrap" }, acts));

      // The result only exists once the game is over — the server decides that (can.record_result),
      // not the browser, whose clock is whatever the member's phone says.
      if (game.result || (game.can && game.can.record_result)) {
        host.appendChild(resultBlock(cfg, game));
      }
      var rate = rateBlock(cfg, game);
      if (rate) host.appendChild(rate);

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
      if (INTENT.word(g.play_intent)) bits.push(INTENT.word(g.play_intent));
      if (g.host_level) bits.push("Level " + g.host_level);
      var n = el("button", { class: "cf-item cf-item-tap", style: "width:100%;text-align:left;cursor:pointer;border:1px solid var(--border);background:var(--surface)" }, [
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: when + " · " + (g.host_name || "A member") }),
          el("div", { class: "cf-item-s", text: bits.filter(Boolean).join(" · ") })
        ]),
        el("span", {
          class: "cf-chip " + (g.im_in ? "ok" : ""),
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
