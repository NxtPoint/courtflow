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
def main():
    # Force UTF-8 on stdout/stderr so the emoji/curly-quotes in the report can't crash print()
    # on a runner whose console encoding isn't UTF-8 (this caused a spurious exit-1 on the first run).
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    creds = _creds()
    parts = [f"# 🎾 Marketing digest — {TODAY}",
             "_Cross-brand organic growth (GA4 + Search Console). Auto-discovers whatever the "
             "`marketing-engine` service account can read._", ""]

    # GA4
    try:
        props = ga4_properties(creds)
    except Exception as e:
        props = []
        parts.append(f"⚠️ Could not list GA4 properties ({type(e).__name__}: {e}). "
                     "Has the service account been granted Viewer on the GA4 properties yet?")
    parts.append(f"## GA4 properties ({len(props)})")
    for pid, name in sorted(props, key=lambda x: x[1].lower()):
        try:
            parts.append(ga4_block(creds, pid, name))
        except Exception as e:
            parts.append(f"### {name}\n- ⚠️ failed ({type(e).__name__}: {e})")
        parts.append("")

    # GSC
    try:
        sites = gsc_sites(creds)
    except Exception as e:
        sites = []
        parts.append(f"⚠️ Could not list Search Console sites ({type(e).__name__}: {e}). "
                     "Has the service account been added as a user on the GSC properties yet?")
    parts.append(f"## Search Console sites ({len(sites)})")
    for site in sorted(sites):
        try:
            parts.append(gsc_block(creds, site))
        except Exception as e:
            parts.append(f"### {site}\n- ⚠️ failed ({type(e).__name__}: {e})")
        parts.append("")

    report = "\n".join(parts)

    # Write report files
    outdir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "latest.md"), "w", encoding="utf-8") as f:
        f.write(report)
    with open(os.path.join(outdir, f"{TODAY}.md"), "w", encoding="utf-8") as f:
        f.write(report)

    # GitHub Actions step summary
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(report)

    try:
        print(report)
    except Exception:
        pass  # report is already written to files + step summary; never fail the job on a log write
    return 0


if __name__ == "__main__":
    sys.exit(main())
