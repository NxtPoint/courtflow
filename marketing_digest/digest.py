#!/usr/bin/env python3
"""marketing_digest — the daily cross-brand organic-growth report (GA4 + Search Console).

Runs in GitHub Actions authenticated via Workload Identity Federation (keyless — no service
account key, per the org security policy). It AUTO-DISCOVERS every GA4 property and Search
Console site the `marketing-engine` service account has been granted access to, so coverage is
controlled purely by the per-property grants in the GA4/GSC consoles — add a brand there and it
shows up here, no code change.

For each brand it reports (guarded — one property erroring never crashes the run):
  GA4 (last 7d):   active users, sessions, top pages, traffic by channel
  GSC (last 28d):  clicks, impressions, top queries, and STRIKING-DISTANCE queries
                   (avg position 8-20, ranked by impressions) — the exact things to write next.

Output: a Markdown digest written to marketing_digest/reports/latest.md (+ a dated copy) and to
the GitHub Actions step summary. Email/Slack delivery is a later add-on (V2).
"""
import os
import sys
import datetime as _dt

import google.auth
from google.analytics.admin import AnalyticsAdminServiceClient
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Dimension, Metric, OrderBy,
)
from googleapiclient.discovery import build as gbuild

SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]

# Fixed "today" passed in by the workflow (UTC date string) so re-runs are deterministic.
TODAY = os.environ.get("DIGEST_DATE") or _dt.datetime.utcnow().strftime("%Y-%m-%d")

# Brands: a GA4 property display-name or a GSC site URL is matched to a brand by substring, and each
# brand's slice of the report is emailed to its own inbox. Add a brand = add a row (+ grant the SA).
# `ads_customer` = the brand's Google Ads customer id (env, digits only) — set to add a Google Ads
# block to that brand's report. DARK until GOOGLE_ADS_DEVELOPER_TOKEN + the id are set (Ten-Fifty5
# has no ads by design, so its env stays unset).
BRANDS = [
    {"name": "NextPoint Tennis", "email": "info@nextpointtennis.com",
     "match": ["nextpoint"],
     # NextPoint Ads account 704-275-3564 (public acct number). Overridable by env; the account is
     # standalone (no manager), so this is also the login_customer_id. DARK until the dev token is set.
     "ads_customer": os.environ.get("GOOGLE_ADS_CUSTOMER_ID_NEXTPOINT", "7042753564").replace("-", "").strip()},
    {"name": "Ten-Fifty5", "email": "info@ten-fifty5.com",
     "match": ["ten-fifty5", "tenfifty5", "fifty5"],
     "ads_customer": os.environ.get("GOOGLE_ADS_CUSTOMER_ID_TENFIFTY5", "").replace("-", "").strip()},
]

# Google Ads (optional). DARK until BOTH the developer token AND a brand's ads_customer id are set,
# so the digest runs unchanged without it. Auth reuses the WIF service-account credentials (adwords
# scope) — the SA must be added as a user on the Ads account. NEEDS a first live verification on
# switch-on (the Ads-API auth path is the one thing that can't be tested until the token exists).
ADS_DEV_TOKEN = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip()


def brand_for(text):
    """Return the BRANDS row whose match-substrings appear in text, else None."""
    t = (text or "").lower()
    for b in BRANDS:
        if any(m in t for m in b["match"]):
            return b
    return None


def md_to_html(md):
    """Tiny Markdown -> HTML for the email body (headings, bold, bullets) — no dependency."""
    import html as _html
    import re
    out = ["<div style=\"font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
           "font-size:15px;line-height:1.55;color:#17211b;max-width:640px\">"]
    in_list = False
    for raw in md.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        is_bullet = stripped.startswith(("- ", "    - ", "* "))
        if is_bullet:
            if not in_list:
                out.append("<ul style=\"margin:6px 0 12px;padding-left:20px\">")
                in_list = True
            item = _html.escape(re.sub(r"^[-*]\s+", "", stripped))
            item = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", item)
            out.append(f"<li style=\"margin:2px 0\">{item}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if not stripped:
            continue
        esc = _html.escape(stripped)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
        if stripped.startswith("# "):
            out.append(f"<h2 style=\"font-size:20px;margin:14px 0 4px\">{esc[2:]}</h2>")
        elif stripped.startswith("## "):
            out.append(f"<h3 style=\"font-size:17px;margin:16px 0 4px\">{esc[3:]}</h3>")
        elif stripped.startswith("### "):
            out.append(f"<h4 style=\"font-size:15px;margin:14px 0 2px;color:#2f6b44\">{esc[4:]}</h4>")
        elif stripped.startswith("_") and stripped.endswith("_"):
            out.append(f"<p style=\"color:#5e6e63;margin:2px 0 10px\">{esc.strip('_')}</p>")
        else:
            out.append(f"<p style=\"margin:6px 0\">{esc}</p>")
    if in_list:
        out.append("</ul>")
    out.append("</div>")
    return "\n".join(out)


def email_report(api_base, ops_key, to, subject, md):
    """POST a brand's report to the OPS-guarded API endpoint, which sends it via the platform SES."""
    import json as _json
    import urllib.request
    payload = _json.dumps({
        "to": to, "subject": subject, "text": md, "html": md_to_html(md),
        "from_name": "Marketing Digest",
    }).encode("utf-8")
    import urllib.error
    req = urllib.request.Request(
        api_base.rstrip("/") + "/api/cron/marketing-digest-email",
        data=payload, method="POST",
        headers={"Content-Type": "application/json", "X-Ops-Key": ops_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # Return the code + body instead of raising, so a 403/400 is diagnosable in the report.
        return e.code, e.read().decode("utf-8", "replace")


def _creds():
    creds, _ = google.auth.default(scopes=SCOPES)
    return creds


# ---------------------------------------------------------------- GA4
def ga4_properties(creds):
    """Every GA4 property the SA can see -> [(property_id, display_name)]."""
    client = AnalyticsAdminServiceClient(credentials=creds)
    out = []
    for summ in client.list_account_summaries():
        for p in summ.property_summaries:
            out.append((p.property.split("/")[-1], p.display_name))
    return out


def ga4_block(creds, property_id, name):
    client = BetaAnalyticsDataClient(credentials=creds)
    lines = [f"### 📊 {name} — GA4 (last 7 days)"]

    # Headline totals
    try:
        r = client.run_report(RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date="7daysAgo", end_date="yesterday")],
            metrics=[Metric(name="activeUsers"), Metric(name="sessions"),
                     Metric(name="screenPageViews")],
        ))
        if r.rows:
            v = r.rows[0].metric_values
            lines.append(f"- **{v[0].value}** active users · **{v[1].value}** sessions · "
                         f"**{v[2].value}** page views")
        else:
            lines.append("- _no traffic recorded in the window_")
    except Exception as e:
        lines.append(f"- ⚠️ totals unavailable ({type(e).__name__})")

    # Top pages
    try:
        r = client.run_report(RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date="7daysAgo", end_date="yesterday")],
            dimensions=[Dimension(name="pagePath")],
            metrics=[Metric(name="screenPageViews")],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"),
                               desc=True)],
            limit=5,
        ))
        if r.rows:
            lines.append("- **Top pages:**")
            for row in r.rows:
                lines.append(f"    - `{row.dimension_values[0].value}` — "
                             f"{row.metric_values[0].value} views")
    except Exception as e:
        lines.append(f"- ⚠️ top pages unavailable ({type(e).__name__})")

    # Traffic by channel
    try:
        r = client.run_report(RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date="7daysAgo", end_date="yesterday")],
            dimensions=[Dimension(name="sessionDefaultChannelGroup")],
            metrics=[Metric(name="sessions")],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
            limit=6,
        ))
        if r.rows:
            chans = ", ".join(f"{row.dimension_values[0].value} {row.metric_values[0].value}"
                              for row in r.rows)
            lines.append(f"- **By channel:** {chans}")
    except Exception as e:
        lines.append(f"- ⚠️ channels unavailable ({type(e).__name__})")

    return "\n".join(lines)


# ---------------------------------------------------------------- Search Console
def gsc_sites(creds):
    svc = gbuild("searchconsole", "v1", credentials=creds, cache_discovery=False)
    resp = svc.sites().list().execute()
    # Only sites we can actually read (owner/full/restricted), skip unverified.
    return [s["siteUrl"] for s in resp.get("siteEntry", [])
            if s.get("permissionLevel") != "siteUnverifiedUser"]


def gsc_block(creds, site_url):
    svc = gbuild("searchconsole", "v1", credentials=creds, cache_discovery=False)
    end = (_dt.date.fromisoformat(TODAY) - _dt.timedelta(days=1)).isoformat()
    start = (_dt.date.fromisoformat(TODAY) - _dt.timedelta(days=28)).isoformat()
    lines = [f"### 🔎 {site_url} — Search Console (last 28 days)"]

    def query(dimensions, row_limit=25):
        return svc.searchanalytics().query(siteUrl=site_url, body={
            "startDate": start, "endDate": end,
            "dimensions": dimensions, "rowLimit": row_limit,
        }).execute().get("rows", [])

    # Totals
    try:
        rows = svc.searchanalytics().query(siteUrl=site_url, body={
            "startDate": start, "endDate": end}).execute().get("rows", [])
        if rows:
            r = rows[0]
            lines.append(f"- **{int(r['clicks'])}** clicks · **{int(r['impressions'])}** "
                         f"impressions · CTR {r['ctr']*100:.1f}% · avg pos {r['position']:.1f}")
        else:
            lines.append("- _no search data in the window_")
    except Exception as e:
        lines.append(f"- ⚠️ totals unavailable ({type(e).__name__})")

    # Top queries
    try:
        rows = query(["query"], 5)
        if rows:
            lines.append("- **Top queries:**")
            for r in rows:
                lines.append(f"    - “{r['keys'][0]}” — {int(r['clicks'])} clicks / "
                             f"{int(r['impressions'])} impr / pos {r['position']:.1f}")
    except Exception as e:
        lines.append(f"- ⚠️ top queries unavailable ({type(e).__name__})")

    # Striking distance: avg position 8-20, ranked by impressions -> what to write next
    try:
        rows = query(["query"], 200)
        striking = [r for r in rows if 8.0 <= r["position"] <= 20.0]
        striking.sort(key=lambda r: r["impressions"], reverse=True)
        if striking:
            lines.append("- **🎯 Striking-distance queries** (pos 8-20 — nudge these to page 1):")
            for r in striking[:8]:
                lines.append(f"    - “{r['keys'][0]}” — pos {r['position']:.1f}, "
                             f"{int(r['impressions'])} impr, {int(r['clicks'])} clicks")
        else:
            lines.append("- 🎯 no striking-distance queries yet (early days)")
    except Exception as e:
        lines.append(f"- ⚠️ striking-distance unavailable ({type(e).__name__})")

    return "\n".join(lines)


# ---------------------------------------------------------------- main
def ads_block(customer_id):
    """Google Ads campaign metrics (last 7 days) for one customer id, as a Markdown block. Returns
    None when Ads isn't configured (dark). Guarded: any error -> a short note, never crashes the run.
    Auth = the WIF service-account creds with the adwords scope (SA added as a user on the Ads account)."""
    if not (ADS_DEV_TOKEN and customer_id):
        return None
    lines = ["### 📣 Google Ads (last 7 days)"]
    try:
        from google.ads.googleads.client import GoogleAdsClient
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/adwords"])
        client = GoogleAdsClient(credentials=creds, developer_token=ADS_DEV_TOKEN,
                                 login_customer_id=customer_id)
        svc = client.get_service("GoogleAdsService")
        query = ("SELECT campaign.name, campaign.status, metrics.cost_micros, metrics.clicks, "
                 "metrics.impressions, metrics.conversions FROM campaign "
                 "WHERE segments.date DURING LAST_7_DAYS ORDER BY metrics.cost_micros DESC")
        rows = list(svc.search(customer_id=customer_id, query=query))
        if not rows:
            lines.append("- _no ad activity in the window_")
            return "\n".join(lines)
        t_cost = t_clicks = t_conv = 0.0
        camp = []
        for r in rows:
            cost = r.metrics.cost_micros / 1e6
            conv = float(r.metrics.conversions)
            t_cost += cost; t_clicks += r.metrics.clicks; t_conv += conv
            cpa = f", {cost / conv:.0f}/conv" if conv else ""
            camp.append(f"    - {r.campaign.name} ({r.campaign.status.name}): {cost:.0f} spend, "
                        f"{r.metrics.clicks} clicks, {conv:.0f} conv{cpa}")
        head = f"- **{t_cost:.0f} spend · {int(t_clicks)} clicks · {t_conv:.0f} conversions**"
        head += (f" · {t_cost / t_conv:.0f}/conversion" if t_conv
                 else " · ⚠️ 0 conversions — bidding can't optimise")
        lines += [head, "- **By campaign:**"] + camp
        return "\n".join(lines)
    except Exception as e:
        lines.append(f"- ⚠️ Ads unavailable ({type(e).__name__}: {e}) — verify dev-token approval + SA access")
        return "\n".join(lines)


def _brand_report(name, ga4_blocks, gsc_blocks, ads_blocks=None):
    p = [f"# 🎾 {name} — marketing digest · {TODAY}",
         "_Organic growth from GA4 + Search Console. The 🎯 striking-distance queries are the "
         "highest-value action: you rank page 1-2 for them, so a post or page nudges them to the top._",
         "", "## GA4 — your traffic (last 7 days)"]
    p += ga4_blocks or ["_no GA4 property granted to the engine yet_"]
    p += ["", "## Search Console — what you rank for (last 28 days)"]
    p += gsc_blocks or ["_no Search Console property granted to the engine yet_"]
    if ads_blocks:
        p += ["", "## Google Ads — paid performance"] + ads_blocks
    return "\n".join(p)


def main():
    # Force UTF-8 on stdout/stderr so the emoji/curly-quotes in the report can't crash print()
    # on a runner whose console encoding isn't UTF-8 (this caused a spurious exit-1 on the first run).
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    creds = _creds()

    # Group each discovered property/site into its brand (unmatched -> "other", kept in the combined view).
    buckets = {b["name"]: {"brand": b, "ga4": [], "gsc": [], "ads": []} for b in BRANDS}
    other = {"ga4": [], "gsc": []}
    notes = []

    try:
        props = ga4_properties(creds)
    except Exception as e:
        props = []
        notes.append(f"⚠️ GA4 discovery failed ({type(e).__name__}: {e}) — is the SA granted Viewer?")
    for pid, name in sorted(props, key=lambda x: x[1].lower()):
        try:
            block = ga4_block(creds, pid, name)
        except Exception as e:
            block = f"### {name}\n- ⚠️ failed ({type(e).__name__}: {e})"
        b = brand_for(name)
        (buckets[b["name"]]["ga4"] if b else other["ga4"]).append(block)

    try:
        sites = gsc_sites(creds)
    except Exception as e:
        sites = []
        notes.append(f"⚠️ Search Console discovery failed ({type(e).__name__}: {e}) — is the SA added?")
    for site in sorted(sites):
        try:
            block = gsc_block(creds, site)
        except Exception as e:
            block = f"### {site}\n- ⚠️ failed ({type(e).__name__}: {e})"
        b = brand_for(site)
        (buckets[b["name"]]["gsc"] if b else other["gsc"]).append(block)

    # Google Ads per brand (dark unless GOOGLE_ADS_DEVELOPER_TOKEN + the brand's ads_customer are set).
    for name, data in buckets.items():
        blk = ads_block(data["brand"].get("ads_customer"))
        if blk:
            data["ads"].append(blk)

    # Combined report for GitHub (every brand + anything unmatched).
    combined = [f"# 🎾 Marketing digest — {TODAY}",
                "_Cross-brand organic growth (GA4 + Search Console)._", ""]
    for name, data in buckets.items():
        combined.append(_brand_report(name, data["ga4"], data["gsc"], data["ads"]))
        combined.append("\n---\n")
    if other["ga4"] or other["gsc"]:
        combined += ["## Unmatched properties (add a BRANDS row to route these)"] + other["ga4"] + other["gsc"]
    combined += notes
    report = "\n".join(combined)

    # Email each brand its OWN slice to its OWN inbox (via the OPS-guarded API -> platform SES).
    # Fold the outcome INTO the report so the send status is visible in the digest itself
    # (step summary + committed file) — no CI-log diving needed to see if delivery worked.
    email_lines = []
    api = os.environ.get("MARKETING_DIGEST_API", "").strip()
    ops = os.environ.get("OPS_KEY", "").strip()
    if api and ops:
        for name, data in buckets.items():
            if not (data["ga4"] or data["gsc"]):
                email_lines.append(f"- {name}: skipped (no data granted to the engine yet)")
                continue
            md = _brand_report(name, data["ga4"], data["gsc"], data["ads"])
            subj = f"🎾 {name} — marketing digest ({TODAY})"
            try:
                status, resp = email_report(api, ops, data["brand"]["email"], subj, md)
                email_lines.append(f"- {name} → {data['brand']['email']}: HTTP {status} — {resp}")
            except Exception as e:
                email_lines.append(f"- {name} → {data['brand']['email']}: FAILED ({type(e).__name__}: {e})")
    elif not api:
        email_lines.append("- skipped: MARKETING_DIGEST_API not set on the workflow")
    else:
        email_lines.append("- skipped: OPS_KEY secret not set on the repo")

    combined += ["", "## 📬 Email delivery (this run)"] + email_lines
    report = "\n".join(combined)

    # Write report files + Actions step summary.
    outdir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(outdir, exist_ok=True)
    for fn in ("latest.md", f"{TODAY}.md"):
        with open(os.path.join(outdir, fn), "w", encoding="utf-8") as f:
            f.write(report)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(report)

    for line in email_lines:
        print("[email] " + line)
    try:
        print(report)
    except Exception:
        pass  # report is already written to files + step summary; never fail the job on a log write
    return 0


if __name__ == "__main__":
    sys.exit(main())
