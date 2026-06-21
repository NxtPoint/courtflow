# Adspirer (Google Ads) via Claude Code — setup + run

**Why here, not Cowork:** Adspirer's MCP login uses a loopback redirect (`http://localhost:3118/callback`)
that only a local listener can catch. Claude Code (CLI) runs that listener; Cowork doesn't — so the
connector authenticates cleanly in Claude Code and not in Cowork. Adspirer is literally built for it.

Account context: **NextPoint Tennis Centre**, Google Ads ID **704‑275‑3564**, login
`info@nextpointtennis.com`. Adspirer account + Google Ads link already exist (free plan, 15 tool
calls/mo). Cyborg Digital no longer manages the account — we run it.

---

## 1. Add the Adspirer MCP server (run in your terminal / Claude Code)
```
claude mcp add --transport http adspirer https://mcp.adspirer.com/mcp
```
(Optional — make it available in every project, not just this one:)
```
claude mcp add --scope user --transport http adspirer https://mcp.adspirer.com/mcp
```

## 2. Authenticate (this is the step that fails in Cowork but works here)
1. Start Claude Code in this repo, then run the slash command: **`/mcp`**
2. Select **adspirer** → **Authenticate**. A browser window opens.
3. Approve (you're already logged into Adspirer + Google Ads, so it's one click).
4. The CLI's local listener catches the redirect → **Connected.** The real tools come online
   (`get_campaign_performance`, account list, keyword research, campaign create/edit, etc.).

> If `/mcp` shows adspirer as "needs auth", just pick Authenticate. If a browser doesn't open, copy the
> printed URL into the browser where you're logged in as `info@nextpointtennis.com`.

## 3. Verify the connection
Ask Claude Code:
```
List my connected ad accounts, then pull campaign performance for the last 30 days.
```
Expect: account "NextPoint Tennis Centre" and the PMax campaign "Aug – Cyborg Digital 2025".

---

## 4. Prompt pack — run these in order (mirrors the audit in `google-ads-audit-2025-08.md`)

**A. Full audit / confirm the diagnosis**
```
Read marketing/google-ads-audit-2025-08.md. Then pull the last 90 days for the NextPoint Tennis
Centre account: campaigns, spend, clicks, CTR, CPC, conversions, conversion rate, and the PMax
asset-group + search-theme insights. Confirm whether any conversion tracking exists. Summarise where
budget is being wasted and the top 5 fixes.
```

**B. Conversion tracking (the #1 fix)**
```
We have zero conversion tracking. List the conversion actions we should create for a tennis club:
booking-page completions, "Book a Court/Lesson" clicks, phone-call clicks on 076 990 7439, the
"Claim Free Lesson" form, and WhatsApp clicks. Tell me exactly how to set each up (Google tag / GA4
import) and which to mark Primary vs Secondary.
```

**C. Restructure: PMax → local Search**
```
Draft a new local Search campaign for NextPoint Tennis (Killarney/Houghton, Johannesburg) on a
~R2,160/mo budget: ad groups (Courts, Lessons, Juniors, Cardio Tennis, High Performance), 10-15
high-intent keywords each in phrase/exact, a negative-keyword list, 3 responsive search ads per ad
group, and all extensions (sitelinks, callouts, call on 076 990 7439, location linked to Google
Business Profile, structured snippets). Geo = presence within ~15km of Killarney Country Club.
Show it to me for approval BEFORE creating anything.
```

**D. Wind down the leak**
```
Once the Search campaign is approved and conversion tracking is live, recommend whether to pause or
cap the PMax campaign and over what timeframe, so we don't lose traffic while the Search campaign
ramps.
```

## 5. Guardrails (tell Claude Code)
- **Approve before spend changes:** never create/edit/pause a campaign or change budgets/bids without
  showing me the plan first.
- Free plan = **15 tool calls/month** — be economical; batch reads.
- Keep the human-readable change log in this folder.

---

## Division of labour
- **Cowork (this assistant):** strategy, audit, copy, keyword research via Ahrefs, landing-page +
  conversion-tracking design that ties into the new site build.
- **Claude Code + Adspirer:** executing the changes in the live Google Ads account.
Both read this repo, so the plan stays in one place.
