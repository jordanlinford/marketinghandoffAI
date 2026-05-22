# Additive Setup + per-field enrichment diff — build brief

Hand this to Claude Code. Read CLAUDE.md and docs/setup-stage-brief.md first.

## Goal
Today Setup's three inputs (manual / crawl+knowledge / customer-CSV) are EITHER/OR — each
overwrites the form. Make them **additive and layered** instead: each source fills the
fields it's strongest at, sources combine rather than clobber, and real data beats guesses.
Plus: when a user enriches an ALREADY-SAVED profile with a new input, show a per-field
accept/reject diff before changing saved truth. No new data model — this reworks how the
existing draft endpoints merge into the working profile and how saves apply.

## Two flows, one merge engine
1. **First-time setup (one sitting, nothing saved yet):** the user can stack inputs into the
   working draft before the single Save. Crawl, then upload a CSV, then hand-edit — each
   layers onto the current working draft. No per-change confirmation: nothing is committed
   until Save. (Drafts are free; only saved truth is gated.)
2. **Enrichment (a confirmed profile already exists):** a new input (new CSV, re-crawl)
   produces proposed changes against the SAVED profile. Before anything is written, show a
   **per-field diff** (old value → new value, with the source of the new value) and let the
   user **accept or reject each field independently**. Only accepted fields update the saved
   profile. This is the safety gate the user asked for.

## Merge rules (the heart of it — apply in both flows)
Each source is strongest at different fields; merging should reflect that:
- **Crawl / knowledge** → qualitative fields: product_summary, value_prop, competitors,
  keywords, conversion_goal, brand-adjacent positioning.
- **Customer CSV** → quantitative ICP: icp.industries, icp.min_employees,
  icp.min_revenue_usd (grounded in real customers).
- **Manual** → anything the user typed.

Precedence when sources disagree on a field:
**manual > csv (real data) > knowledge (guess) > blank.**
- Real customer data OVERRIDES a knowledge-guess for ICP quantitative fields (CSV's median
  size beats the model's guessed "min 500").
- A manually edited field is SACRED — never auto-overwritten by a later crawl/CSV. If a new
  source would change a manual field, that's a proposed change the user must accept (flow 2)
  or a clearly-marked conflict they resolve (flow 1), never a silent overwrite.
- Empty/blank from a source never overwrites an existing non-blank value (don't let a CSV
  that lacks a product_summary wipe the knowledge-drafted one).
- Every field keeps its `source` tag (manual / csv / knowledge) reflecting where its CURRENT
  value came from, so the UI shows provenance and the merge can reason about precedence.

Conflicts must be SURFACED, not hidden. When two sources disagree on a field, the user sees
both ("knowledge said 500; your customer data says 1,200 — using 1,200") rather than a value
silently changing. (Same honesty discipline as csv vs csv+intent.)

## Endpoints
- The existing draft endpoints (/crawl, /from-csv) stay, but instead of returning a draft
  that REPLACES the form, they return a draft the client MERGES into the current working
  state per the rules above. Keep them returning per-field source tags.
- For enrichment (flow 2), add a way to compute the diff against the saved profile. Simplest:
  a `POST /api/profile/preview-merge` that takes {new draft, against: "saved"} and returns a
  per-field changelist: [{field, old_value, old_source, new_value, new_source, conflict:bool}].
  The client renders accept/reject per row; on confirm, the client PUTs the merged result
  (only accepted fields applied) — PUT remains the single writer of confirmed truth.
  (If you prefer to compute the diff client-side from data already returned, that's fine too —
  but the accept/reject decision and the final PUT-of-accepted-fields is the required behavior.)
- All reads/writes stay tenant-scoped via `scoped()`.

## UI (app/static/ui.html)
- The three input cards no longer feel mutually exclusive. After running one, the form is
  populated; running another LAYERS on (doesn't wipe). Show a small per-field source chip
  (manual / csv / knowledge) — extend the existing "suggested" marking.
- **Flow 1 (no saved profile):** stacking inputs just updates the working draft; conflicts
  show inline (e.g. a small "knowledge: 500 → csv: 1,200" note on the field). Single Save.
- **Flow 2 (saved profile exists):** running a new input opens a **diff panel** listing only
  the fields that would change: each row shows field name, current saved value, proposed new
  value, the new value's source, and an Accept / Reject toggle (default: accept non-conflicts,
  flag conflicts for explicit choice). An "Apply N accepted changes" button PUTs the result.
  Make it unmistakable this updates the SAVED profile.

## Constraints (from CLAUDE.md)
- `scoped(Model, org_id)` everywhere. PUT stays the only writer of confirmed truth.
- No new data model / no schema change (org_profiles already exists). If you add a
  preview-merge endpoint it's read-only (computes a diff, writes nothing).
- Reuse existing draft/parse/source-tagging logic — don't duplicate.
- Don't touch the TS app or .replit's [deployment] block.
- Keep `python -m scripts.smoke` green. ADD tests:
  (1) Merge precedence: knowledge draft + CSV draft → CSV's icp.min_employees overrides the
      knowledge guess; knowledge's product_summary survives (CSV didn't provide one);
      a blank source value never overwrites a non-blank existing value.
  (2) Manual-sacred: a field marked source="manual" is NOT auto-overwritten by a later
      crawl/csv merge (it becomes a proposed change / conflict instead).
  (3) Enrichment diff: against a saved profile, preview-merge returns a per-field changelist;
      applying only the ACCEPTED fields updates the saved profile and leaves rejected fields
      unchanged; confirmed stays true; tenant isolation holds.

## Done looks like
- A user can crawl their site AND upload a customer list in one sitting and get ONE combined
  profile (qualitative from knowledge, real ICP from CSV), then Save once.
- Real customer data overrides knowledge guesses for ICP size/industries; manual edits never
  get silently overwritten; conflicts are shown.
- Re-uploading a new customer list against a saved profile shows a per-field accept/reject
  diff and only writes the accepted changes to the saved profile.
- Every field shows its source; smoke green with the three new tests; committed and pushed.

## Out of scope
- Web search grounding; wiring OrgProfile into agents; any new table.
