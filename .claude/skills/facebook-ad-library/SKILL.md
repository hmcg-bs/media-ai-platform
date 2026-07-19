---
name: facebook-ad-library
description: >-
  Pull ads from the Meta (Facebook/Instagram) Ad Library via the official Graph
  API ads_archive endpoint, save them as JSON, and optionally download the
  creative media for downstream extraction. Use this whenever the user wants to
  fetch, pull, scrape, collect, or search competitor / political / brand ads
  from the Facebook or Meta Ad Library — phrases like "pull ads for <brand>",
  "what ads is <competitor> running", "get Facebook ads about X", "search the
  ad library", "download ad creatives", or "ingest ads into creatives/input".
  Also use when the user mentions ads_archive, ad_snapshot_url, search_page_ids,
  or asks why the Ad Library API returns no results for a non-EU commercial
  advertiser.
---

# Facebook / Meta Ad Library Pull

Fetch ads from Meta's public Ad Library through the official Graph API
(`/{version}/ads_archive`), write structured JSON, and (optionally) download the
creative images/videos so they can feed Step 2 extraction (`creatives/input/`).

This is **Step 1 (Ingestion)** territory — a standalone tool, deliberately
decoupled from the `pipeline/` package (which is Step 2). It does not touch
BigQuery, Vertex, or ADC; its only credential is a Meta access token.

## The one thing to understand first

The Ad Library API's coverage is **lopsided**, and most surprises trace back to
this. Tell the user plainly when it applies:

- **Political / issue ads** → global, 7-year archive, *rich* fields (spend,
  impressions, demographics, regional delivery). The API's strong suit.
- **All commercial ads** → only archived and queryable when the ad **targets the
  EU or UK**, and only for ~1 year. The transparency fields (spend/impressions)
  are still absent for these.
- **Commercial ads targeting elsewhere (US, etc.)** → effectively **not in the
  API**. A query for a US clothing brand will usually come back empty or
  political-only. This is expected, not a bug.

So if the user wants US/non-EU competitor creatives and the API returns nothing,
that's the boundary — say so, and note that the project's blueprint uses
**Apify → Meta Ad Library** for exactly this reason. Don't keep retrying
parameters hoping commercial US ads appear; they won't.

`ad_reached_countries` is **required** on every call. Using `ALL` with
non-political `ad_type` silently returns political content only unless EU
countries are in scope — another reason results can look wrong.

## Workflow

1. **Confirm the query shape** with the user if it's ambiguous — who/what
   (search terms or specific advertiser Page IDs), which countries, ad type
   (commercial vs political), active or all, and whether they want the media
   files downloaded. Use sensible defaults rather than interrogating: country
   `US`, `ad_type=ALL`, `ad_active_status=ALL`.

2. **Run the bundled script.** It handles pagination, retries, the JSON dump,
   and best-effort media download. Prefer it over hand-rolling `curl` — it
   already encodes the array-typed params correctly (a common failure point).

   ```bash
   python .claude/skills/facebook-ad-library/scripts/pull_ads.py \
     --search-terms "electric car" \
     --countries GB,DE \
     --ad-type ALL \
     --status ALL \
     --limit 100 \
     --download-media \
     --out creatives/ad-library
   ```

   Or target specific advertisers by Page ID (up to 10):

   ```bash
   python .claude/skills/facebook-ad-library/scripts/pull_ads.py \
     --page-ids 20531316728,1234567890 \
     --countries GB \
     --download-media
   ```

   The token is read from `META_ACCESS_TOKEN` (or `FB_ACCESS_TOKEN`) in the
   environment / `.env`, or pass `--access-token`. See "Token" below.

3. **Report what came back honestly.** State the count, where files landed, and
   — critically — whether media actually downloaded. If the count is 0 or media
   scraping failed, explain *why* using the coverage rules above rather than
   presenting an empty run as success.

4. **For ad-hoc / one-off shapes** the script doesn't cover (an unusual field
   set, a quick existence check), it's fine to construct a direct Graph API call
   instead. The full parameter and field catalogue, with which fields are
   available for which ad types, is in
   [references/api-reference.md](references/api-reference.md) — read it before
   building a custom query so you don't request EU-only fields on a US query and
   misread the empty result.

## Output layout

The script writes a timestamped run directory under `--out`:

```
creatives/ad-library/<UTC-timestamp>/
├── ads.json        # array of ad records (the API fields you requested)
├── query.json      # the exact parameters used + result count (provenance)
└── media/          # downloaded creatives, named <ad_id>.<ext> (if --download-media)
```

Point Step 2 at the `media/` folder to extract creative features from what you
pulled.

## Media download is best-effort — say so

The API never returns a direct image/video URL. The only handle is
`ad_snapshot_url`, an HTML preview page. The script fetches that page and parses
the CDN media URL out of it. This works often but is **inherently fragile**:
Meta changes the markup, some ads (carousels, video) don't yield a clean URL,
and the scraped URL can be expired. When a download fails the script records the
`ad_snapshot_url` in `ads.json` and logs a warning — it never silently drops the
ad. If the user needs guaranteed media, the snapshot URL opened in a browser is
the fallback.

## Token

Needs a User access token from a Meta developer account whose identity is
verified (Meta requires government ID + selfie for Ad Library access). Short-
lived tokens expire in ~1–2 hours; exchange for a long-lived (~60-day) one for
repeated pulls. No app review is required for public archive data. Store it as
`META_ACCESS_TOKEN` in `.env` (gitignored) — never paste it into committed code
or the `query.json` provenance file (the script redacts it there).

If a call fails with an auth/permission error (Graph error code 190 or an OAuth
message), the token has expired or lacks Ad Library access — surface that
specific cause rather than retrying.
