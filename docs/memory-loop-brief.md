# Substrate learning loop — persistent marketing memory — build brief (Phase 2 / multiplicative)

Hand this to Claude Code. Read CLAUDE.md and ALL build briefs in docs/ first (content-engine,
quality-loop, dashboard, product-layer, document-ingestion, ia-restructure, asset-library,
campaign-builder).

## Framing (this is the load-bearing philosophy — hold it)
This is NOT "AI optimization" or machine learning. It is a **persistent marketing memory
system**: it RETRIEVES historical performance signal, WEIGHTS it, detects EXPLAINABLE patterns,
scores CONFIDENCE honestly, and INJECTS it as evidence-backed context into future decisions.

The governing sentence, never violated:
> "Here's what has historically worked for this audience/channel/content combination" — always
> with the evidence — **never** "the AI has decided."

Everything is retrieval + deterministic weighting + honest confidence + contextual injection.
NO learned models, NO autonomous action, NO opaque scoring. If a recommendation can't show its
evidence, it doesn't ship.

The system already WRITES into the substrate (metric_points, UTMs, campaign metadata, uploaded
report telemetry) but nothing READS BACK to influence decisions. This build creates the first
meaningful **read-path**, as SHARED INFRASTRUCTURE both content and campaign agents call. It is
multiplicative: it raises the ceiling of every downstream system, now and later.

## The shared service: `query_memory()` (build this ONCE, both consumers call it)
Create a single module (e.g. `app/memory/query.py`) exposing:

```
query_memory(org_id, *, product_id=None, audience=None, channel=None,
             content_type=None, campaign_type=None, lookback_days=180) -> MemoryResult
```

It reads ONLY existing data via scoped(): `metric_points` (the performance telemetry),
artifacts' `utm_*` + `campaign_id` (what was produced and how it was tagged), `campaigns`
metadata (audience/channel/objective), and report_uploads. No new data sources.

`MemoryResult` is a list of **patterns**, each:
```
Pattern = {
  dimension,        # e.g. "channel", "channel x audience", "content_type"
  key,              # e.g. "linkedin", "linkedin x General Counsel", "email"
  observation,      # plain-language, evidence-first:
                    #   "LinkedIn drove 4.1 conversions per 100 clicks for General Counsel"
  metric_basis,     # the actual numbers behind it (so the UI/agent can show the 'because')
  sample_size,      # how many data points / campaigns / metric rows underpin it
  recency,          # how recent the supporting data is
  confidence,       # see honest-confidence rules below
  confidence_reason # WHY this confidence — esp. for thin data
}
```

### Retrieval + weighting (deterministic, explainable)
- Retrieve metric_points matching the requested dimensions (scoped, within lookback).
- Weight by **recency** (more recent data counts more — simple decay or recency tiers, not ML)
  and **volume** (more data points = more weight). Document the weighting; it must be
  inspectable and explainable in one sentence.
- Detect patterns by aggregating across the dimension combos requested (channel,
  channel×audience, content_type×channel, campaign_type). Pure aggregation + ranking — no
  learned model.

### Honest confidence (the anti-overreach guardrail)
- Confidence is a function of sample_size + recency + consistency. Tiers, not false precision:
  `high` | `moderate` | `low` | `insufficient`.
- THIN DATA (1–2 points) is **permissive but structurally subordinate**: surface it, but the
  observation must FRAME it as "not enough yet," NOT as a confident pattern with a small number.
  - Good: "Only 1 campaign so far used LinkedIn for GC — not enough to call a pattern yet."
  - Bad: "LinkedIn: 0.7 effectiveness (low confidence)" ← reads as a finding; forbidden.
- `insufficient` patterns are returned (so the system is honest that it's watching the
  dimension) but clearly marked and must never be allowed to reorder defaults (see influence
  rules). They inform "we'll know more as you add data," not a recommendation.

## How memory INFLUENCES the agents (advisory + default-shaping, human-overridable)
Memory is injected as CONTEXT, and may PRE-WEIGHT defaults, but never locks a choice and always
shows its evidence.

### Campaign proposal (`app/campaigns/planner.py`)
- The reserved `performance_context` seam (from the Campaign Builder build) is now POPULATED by
  `query_memory()` instead of None.
- Channel recommendations: memory may **reorder / re-weight** the recommended channel mix —
  e.g. move LinkedIn above Google for a GC audience — BUT the rationale string MUST cite the
  evidence ("moved up: 4.1 conv/100 clicks for GC in your data vs 1.5 for Google") and the
  user can still override. With `insufficient` data, memory does NOT reorder — it falls back to
  the existing best-practice rec and notes "no performance history yet for this combination."
- The proposal's cadence/sequencing stays best-practice (memory v1 informs channel + content
  weighting, not timing — timing is the deferred Strategy Layer).

### Content generation (`app/agents/content_engine.py`)
- When generating/suggesting content, inject relevant memory as grounding context: "content
  targeting {audience} on {channel} has historically performed best when {pattern}" — as
  background the model considers, surfaced to the user as evidence in provenance.
- Content IDEAS (the suggest path) may be **re-ranked** by memory (surface high-performing
  angles/topics first) — with the evidence shown. Generation itself uses memory as context, not
  as a directive that overrides the brand voice / framework grounding already in place.
- Respect the existing no-echo discipline: memory context goes in the system message as
  guidance, never echoed into output.

### Universal influence rules (hold across both)
- Every memory-influenced default carries its "because" (the metric_basis), visible to the user.
- `insufficient`-confidence patterns never reorder/override defaults — they're informational.
- Human override always wins and is always available.
- No autonomous action of any kind (no budget shifts, no auto-channel-selection without the
  human seeing and being able to change it).

## Inspectable memory (it's a memory system — you should be able to LOOK at the memory)
- `GET /api/memory?product_id=&audience=&channel=&...` — returns the MemoryResult patterns with
  evidence + confidence for the org (scoped). This is the read surface.
- A lightweight **"What the system has learned"** panel (in Insights, or its own small section):
  lists the current patterns, their evidence, sample size, and confidence — plainly. Empty
  state: "Not enough performance data yet — upload reports in Insights and the system will start
  surfacing what works." This makes the memory a visible, browsable asset, not just invisible
  nudges. Keep it minimal (Replit polishes later).

## Constraints (from CLAUDE.md)
- `scoped(Model, org_id)` for ALL reads. Memory is READ-ONLY over existing tables — it writes
  nothing, creates no new persistent tables (it's a query service + projection). Cross-org data
  must never appear (tested).
- `query_memory()` is the ONE shared entry point; content + campaign agents both call it through
  ctx or a thin accessor. Do NOT duplicate retrieval logic in two places — that's the whole
  point of it being infrastructure.
- Deterministic core: retrieval, weighting, pattern detection, and confidence are pure Python
  (no LLM, no ML). The LLM only consumes the resulting context to write copy / rationale — it
  never computes the patterns. This keeps it explainable and testable.
- Memory injection must degrade gracefully: no data → agents behave exactly as today
  (best-practice channels, brief-grounded content). Full backwards compat.
- No new dependencies. No schema change expected (read-only projection); if a tiny index helps
  performance, that's allowed (mirror in sql/schema.sql) but no new tables.
- Don't touch the TS app or .replit's [deployment] block.
- Keep `python -m scripts.smoke` green. ADD tests (hermetic — stub the LLM where used):
  (1) Retrieval + weighting: given seeded metric_points across channels/audiences, query_memory
      returns patterns; recent + higher-volume data is weighted above stale/thin (assert the
      ranking).
  (2) Honest confidence: a combination with 1–2 points returns an `insufficient`/thin pattern
      whose observation is framed as "not enough yet" (assert it does NOT read as a confident
      finding); a well-supported combination returns `high`/`moderate` with metric_basis.
  (3) Campaign influence: with performance data favoring LinkedIn for an audience, propose's
      channel rec reorders LinkedIn up WITH an evidence-citing rationale; with only insufficient
      data, the rec falls back to best-practice and says so (no reorder).
  (4) Content influence: suggest-ideas re-ranks by memory with evidence shown; generation
      injects memory as system-message context (assert it's present in the outbound context and
      NOT echoed into the body — reuse the no-echo assertion pattern).
  (5) Shared service: both content and campaign code paths call the SAME query_memory entry
      point (assert no duplicated retrieval logic — e.g. both import the one module).
  (6) Backwards compat: with NO metric_points, query_memory returns empty/insufficient, and both
      agents behave exactly as before (best-practice channels, brief-grounded content) — no
      regression.
  (7) Tenant isolation: query_memory for Acme never returns Onit's metric_points/patterns;
      GET /api/memory cross-org denied.
  (8) Inspectable endpoint: GET /api/memory returns patterns with evidence + confidence for the
      org; empty state when no data.

## Done looks like
- A single `query_memory()` service retrieves weighted, confidence-scored, evidence-backed
  patterns from existing telemetry — deterministic and explainable.
- Campaign proposal's channel mix is reordered by real performance history WHEN the data
  supports it (with visible evidence), and falls back to best-practice (saying so) when it
  doesn't. The `performance_context` seam is now live.
- Content suggestions are re-ranked by memory with evidence; generation is grounded in memory as
  context without echoing it.
- Thin data is surfaced honestly as "not enough yet," never as a confident small number.
- A "What the system has learned" read surface (API + minimal panel) lets you inspect the memory
  directly.
- Everything advisory + human-overridable; no autonomous action; full backwards compat; smoke
  green with the eight new tests; pushed.

## Explicitly out of scope (DO NOT build — this is where "memory" must not become "optimization")
- Any machine-learned model, embedding, or opaque scoring. Deterministic aggregation only.
- Autonomous action of any kind: no budget shifting, no auto-channel-selection without human
  confirmation, no auto-publishing, no auto-regeneration.
- Timing / sequencing optimization (that's the deferred Strategy Layer).
- Cross-org or benchmark "industry" data (memory is the ORG's own data only; no pooling).
- Predictive/forecasting claims ("this WILL convert at X") — memory describes what HAS happened,
  never predicts. Observations are past-tense and evidenced.
- A full memory-management UI — v1 is a minimal inspectable read surface.
- Writing new telemetry or new tables — read-only over what exists.
