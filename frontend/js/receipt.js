// receipt.js — render a printable payment receipt for ?order=<id>.
// Fetches GET /api/billing/receipt/<id> via TFAuth (Bearer-auth), renders, offers Print.
// Self-contained (no api.js dependency) so it stays in the payments lane.
(function () {
  function qp(n) { return new URLSearchParams(location.search).get(n) || ""; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function money(minor, ccy) {
    var sym = (ccy === "ZAR" || !ccy) ? "R" : (ccy + " ");
    return sym + ((Number(minor || 0)) / 100).toFixed(2);
  }
  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    return isNaN(d) ? String(iso).slice(0, 16).replace("T", " ")
      : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }

  function render(r) {
    var refunded = (r.refunded_minor || 0) > 0;
    var lines = (r.lines || []).map(function (l) {
      return '<tr><td>' + esc(l.description || "Booking") +
        (l.qty > 1 ? ' <span style="color:#999">×' + l.qty + '</span>' : '') +
        '</td><td class="num">' + money(l.amount_minor, r.currency) + '</td></tr>';
    }).join("") || '<tr><td colspan="2" style="color:#999">No line items</td></tr>';

    var pays = (r.payments || []).map(function (p) {
      var dir = p.direction === "refund" ? "Refund" : "Payment";
      return dir + " · " + esc(p.provider) + " · " + money(p.amount_minor, p.currency || r.currency) +
        " · " + esc(p.status) + (p.reference ? " · ref " + esc(p.reference) : "") +
        " · " + fmtDate(p.created_at);
    }).join("<br>");

    var statusChip = refunded
      ? '<span class="r-chip refunded">refunded</span>'
      : '<span class="r-chip paid">' + esc(r.status || "paid") + '</span>';

    document.getElementById("r-body").innerHTML =
      '<div class="r-head">' +
        '<div><div class="r-club">' + esc(r.club_name) + '</div>' +
          '<div style="color:#888;font-size:.85rem;margin-top:2px">Tennis club</div></div>' +
        '<div class="r-meta"><b>Receipt ' + esc(r.receipt_no) + '</b><br>' +
          fmtDate(r.issued_at) + '<br>' + statusChip + '</div>' +
      '</div>' +
      '<h1>Receipt</h1>' +
      (r.payer_email ? '<p style="margin:0 0 14px;color:#555">Billed to <b>' + esc(r.payer_email) + '</b></p>' : '') +
      '<table><thead><tr><th>Description</th><th class="num">Amount</th></tr></thead>' +
        '<tbody>' + lines + '</tbody></table>' +
      '<div class="r-total"><span>Total</span><span>' + money(r.amount_minor, r.currency) + '</span></div>' +
      (refunded ? '<div class="r-total r-refund" style="font-size:1rem"><span>Refunded</span><span>−' +
        money(r.refunded_minor, r.currency) + '</span></div>' +
        '<div class="r-total" style="font-size:1rem"><span>Net</span><span>' +
        money(r.net_minor, r.currency) + '</span></div>' : '') +
      (pays ? '<div class="r-pay">' + pays + '</div>' : '') +
      '<div class="r-actions">' +
        '<button class="r-btn" onclick="window.print()">Print / Save PDF</button>' +
        '<a class="r-btn ghost" href="/my.html">My bookings</a>' +
      '</div>';
    document.getElementById("r-foot").textContent =
      "Thank you for playing at " + (r.club_name || "NextPoint") + ".";
  }

  var Receipt = {
    start: async function () {
      var orderId = qp("order");
      var auth = window.TFAuth;
      if (auth && auth.ready) { try { await auth.ready(); } catch (e) {} }
      if (!orderId || !auth || !auth.apiJSON) {
        document.getElementById("r-body").innerHTML =
          '<p style="color:#b4232a">Couldn\'t load this receipt.</p>' +
          '<div class="r-actions"><a class="r-btn ghost" href="/my.html">My bookings</a></div>';
        return;
      }
      try {
        var res = await auth.apiJSON("/api/billing/receipt/" + encodeURIComponent(orderId));
        render(res.receipt || res);
      } catch (e) {
        var msg = (e && (e.message || (e.body && e.body.error))) || "Receipt unavailable.";
        document.getElementById("r-body").innerHTML =
          '<p style="color:#b4232a">' + esc(msg) + '</p>' +
          '<div class="r-actions"><a class="r-btn ghost" href="/my.html">My bookings</a></div>';
      }
    },
  };
  window.Receipt = Receipt;
})();
