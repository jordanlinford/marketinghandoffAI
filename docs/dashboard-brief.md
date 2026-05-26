# Analytics dashboard — build brief

Hand this to Claude Code. Read CLAUDE.md, docs/content-engine-brief.md, and
docs/quality-loop-brief.md first.

## Goal
Build the **dashboard**: the other end of the content engine. It answers the question a
demand-gen leader asks every Monday — "what did we create, where did it go, and how is it
doing?" — organized as a funnel. It is a RECEPTACLE: rather than connecting to GA/SEMrush/
LinkedIn APIs (gated, out of scope), the user UPLOADS the reports those tools already export;
the system normalizes them to a generic time-series store and renders the state-of-the-union.
It joins uploaded performance back to the content the system produced via the UTM tags added
in the quality-loop build, and it surfaces evidence-backed suggestions that feed back into the
content engine — closing the loop.

## Core principle: generic time-series receptacle, curated funnel view on top
Do NOT hardcode a fixed metric schema — different tools export wildly different metrics and you
can't enumerate them. Store metrics generically:
  metric_point = { org_id, source, metric_name, value, date, segment, campaign?, utm? , raw_ref }
- `source` = which tool/report ("ga", "semrush", "linkedin_ads", "manual", ...).
- `segment` = optional dimension (channel, page, keyword, account, etc.).
- `campaign`/`utm_*` = the join key to content (present when the report carries it).
Render whatever's been ingested; the schema is the generic point, the VIEW is curated (below).
All tenant-scoped via scoped(). Mirror the table in sql/schema.sql + RLS.

## Two ingest modes (both reuse the forgiving CSV approach)
1. **Baseline / historical upload** — past months of reports to establish the trend backdrop.
   These are typically UNTAGGED (the system didn't make that old content), so they power the
   funnel TREND but are NOT attributed to specific content. Mark them as baseline.
2. **Ongoing performance upload** — recent reports; where rows carry a campaign/UTM that
   matches content the system produced, JOIN them so performance is attributed to that content.
Both go through a forgiving column-mapping step (like the existing CSV ingest): the user maps
"which uploaded columns are which metric / date / segment / campaign," accept common headers
case-insensitively, show a preview, never crash on a missing column. Reuse the existing CSV
parsing infrastructure where possible.

## Honesty: baseline vs attributed (do not let untagged data masquerade as driven)
- Baseline/historical data is the BACKDROP (faded trend, "where we were"). It is never claimed
  as something the system drove.
- Attributed data (tagged, UTM-joined to produced content) is the SIGNAL ("what we changed").
- The dashboard story is "trend before us → what we produced → trend after," with attribution
  ONLY where the UTM join actually connects. Same honesty discipline as csv vs csv+intent.

## The funnel view (how a marketer reads it)
Three stages, each answering "how many" AND "what drove the change":
- **Top — Leads created:** impressions, clicks, reach, awareness/visibility metrics.
- **Middle — Engagement:** site visits, downloads, content consumed, time-on-site.
- **Bottom — Opportunities:** demo requests / MQLs / SQLs / pipeline (the OrgProfile's
  conversion goal/event anchors this).
Each stage shows the metric over time (with baseline backdrop) and, where attributable, the
content/campaigns that drove it. Plus a **production lane**: what the system itself generated
(drafts produced, approved, published-intended, cost) on the SAME timeline — this is the unique
"here's what we fed the engines" axis no off-the-shelf tool has.

## Suggestions (the cockpit layer — evidence-backed, loops to the engine)
Surface recommendations, but every one carries its evidence and an honest source label:
- **Trend-based (grounded — the real feature):** reason over the org's OWN funnel + baseline +
  tagged production. e.g. "Mid-funnel downloads rose 40% but opportunities stayed flat — the
  sales handoff is leaking; consider bottom-funnel assets for the CLM cluster." Each suggestion =
  { recommendation, evidence (the specific data that motivates it), confidence }.
- **Industry-perspective (clearly labeled — NOT verified data):** the LLM's general read of the
  space, explicitly marked "general industry perspective — verify before acting." This is the
  model's training-knowledge, carries confabulation risk, and must never be presented as current
  data. (Path to ground it later via web search / market feed = a future upgrade, not now.)
- **Every suggestion shows its "because."** A recommendation without its evidence is not allowed.
- **Loop back:** where a suggestion implies content ("produce more bottom-funnel CLM assets"),
  give a one-click route to the content engine pre-filled with that idea (type/topic/target),
  so dashboard → suggestion → generate (tagged, graded) → publish → next report closes the loop.

## Setup additions
- Onboarding gains "upload your last 6–12 months of reports to establish your baseline" — makes
  the tool valuable on first run (shows the user their own funnel history before they've produced
  anything).
- The column-mapping conventions and which headline metrics matter can live on the OrgProfile.

## Constraints (from CLAUDE.md)
- `scoped(Model, org_id)` for ALL reads/writes (metric points, mappings, suggestions). No bare selects.
- Reuse the forgiving CSV parsing (app/data_sources/csv.py) for report ingest — don't duplicate.
- Suggestions use the synthesis.py LLM-or-template fallback pattern; on no key/failure, fall back
  to deterministic rule-based trend suggestions (e.g. "stage X flat while Y rose") so the feature
  degrades gracefully and stays honest.
- Reads org profile + prior artifacts/runs THROUGH the existing ctx / scoped accessors.
- Schema change (a metric_points table; possibly a report-upload + suggestion record). Mirror in
  sql/schema.sql + RLS; on SQLite delete agenthq.db + re-seed; restart uvicorn AND worker. Smoke
  uses its own DB (don't rm while uvicorn runs).
- Don't touch the TS app or .replit's [deployment] block.
- Keep `python -m scripts.smoke` green. ADD tests (hermetic — stub the LLM):
  (1) Ingest: a report CSV normalizes into metric_points (generic shape); a baseline upload is
      marked baseline; forgiving header mapping works; missing columns don't crash.
  (2) Attribution join: a performance row carrying a UTM/campaign that matches produced content
      is joined to that content; an untagged/baseline row is NOT attributed (stays backdrop).
  (3) Funnel view: metric_points aggregate into the three stages over time; the production lane
      reflects the org's runs/artifacts.
  (4) Suggestions: trend-based suggestion includes its evidence; industry-perspective suggestion
      is labeled non-data; the deterministic fallback works with no LLM key.
  (5) Tenant isolation: an org only ever sees its own metric_points / suggestions via scoped().

## UI (app/static/ui.html)
- A **Dashboard tab**: the three funnel stages over time (baseline backdrop faded; attributed
  signal solid), the production lane on the same timeline, and a state-of-the-union summary.
- Upload controls for baseline and ongoing reports, with the column-mapping/preview step.
- A **Suggestions panel**: each suggestion with its evidence + source label, and a "Generate this"
  button that routes to the content engine pre-filled.
- Keep the existing visual style; minimal, scannable, marketer-readable (not a data dump).

## Done looks like
- Upload historical reports → see your funnel trend backdrop immediately (baseline).
- Upload recent reports → funnel updates; where UTMs match produced content, the dashboard shows
  what drove each stage; production lane shows what the system made, on the same timeline.
- Suggestions appear with evidence; trend ones grounded in your data, industry ones clearly
  labeled as perspective; one-click routes a suggestion into the content engine.
- Everything tenant-scoped; honest baseline-vs-attributed distinction; smoke green; pushed.

## Out of scope (do NOT build)
- Live API connections to GA/SEMrush/LinkedIn/etc. — upload-only; APIs are a future build.
- Grounded industry data via web search / market feed — industry suggestions stay labeled
  perspective for now; grounding is a deliberate later upgrade.
- Publishing / firing anything — the loop's "act" step still routes to the (manual) content engine.
- Causal attribution claims beyond what the UTM join supports — correlation + honest labeling only.
