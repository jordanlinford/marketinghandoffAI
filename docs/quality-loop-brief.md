# Content quality loop + UTM tagging — build brief

Hand this to Claude Code. Read CLAUDE.md and docs/content-engine-brief.md first.

## Goal
Two related additions to the existing content engine:
A) **Quality loop** — the engine grades each draft against a per-org rubric, shows the grade,
   and supports "give me something better": regenerate using the prior draft + the user's
   critique as input. This answers "I don't like this — improve it."
B) **UTM / campaign tagging at creation** — every content draft carries a structured campaign
   + UTM tag set, and the engine produces ready-to-use tagged URLs. This is the JOIN KEY a
   future analytics dashboard will use to attribute performance back to the content that drove
   it. Add the tags now; the dashboard is a later build.

Keep the existing generate → guardrail → route → queue spine intact. This extends it.

## Part A — quality loop

### Per-org rubric
- Add `content_rubric` to the OrgProfile (JSON: a list of named criteria, each
  {name, description, weight?}). Sensible default criteria seeded if empty, e.g.:
  on_brand (matches brand_voice), on_strategy (serves the stated goal/ICP),
  clarity (clear single CTA, no fluff), specificity (concrete, not generic),
  no_banned_claims (already enforced by guardrails — rubric notes it).
- Editable in Setup like the other profile fields. This is org-level "what good looks like."

### Self-grade
- After generating a draft, make a SEPARATE LLM call that scores the draft against the rubric:
  return structured JSON — overall score (0–100), per-criterion score + a one-line reason, and
  2–3 concrete improvement suggestions. Defensive parse; on failure or no key, fall back to a
  neutral "ungraded" result (don't block the draft).
- Grades are ADVISORY, not gating — a low grade is shown, never prevents approval. (Gating on a
  self-grade is a footgun; surface it, let the human decide.)
- Store the grade on the artifact so the UI can show it and it's auditable.

### "Give me something better" (regenerate with critique)
- An endpoint/action that takes: the existing draft + the user's free-text critique
  (e.g. "too long, more aggressive, lead with the cost angle") + the rubric + the original
  grounding (profile + brief), and regenerates an improved draft.
- The new draft is a new version — keep the prior one (don't destroy it); the user can compare.
  A simple version chain on the artifact is fine (parent_id or a versions list). Re-grade the
  new version automatically so the user sees if it actually improved.
- Reuse the synthesis.py LLM-or-template fallback pattern. Capture cost on each regen (it's
  another paid call — visibility matters).

## Part B — UTM / campaign tagging at creation

### The convention (encode strategy IN the tags)
Each content draft gets a structured tag set that doubles as both the machine join key and the
strategic organizer:
- `utm_campaign` — the campaign (e.g. "clm-comparison-q2")
- `utm_source`   — the channel (e.g. "linkedin", "google", "email", "organic")
- `utm_medium`   — the type (e.g. "paid-social", "cpc", "email", "organic-social")
- `utm_content`  — this specific piece (e.g. "comparison-email-v1")
These four dimensions encode channel + intent + piece identity, which is how GA/LinkedIn/etc.
reports already parse traffic — so a later dashboard can join performance rows back to content
on these exact keys. Store them on the content artifact.

### Generate the tagged URL
- Given a destination URL (the user provides a landing page, or use the profile's website_url +
  conversion path), the engine produces the fully-UTM'd link, ready to paste. Make it
  copy-obvious in the UI. This is what makes the tag actually flow into the reports later.
- Sensible auto-suggested UTM values from the content idea (campaign from the brief topic,
  source from the chosen content_type's typical channel) — editable by the user before they
  accept. Don't force; suggest and let them adjust.

### Honesty about the limit
- The join only works if the content is actually published under these UTMs. v1 generates and
  surfaces the tagged link; it does NOT publish. Make the UI clear: "publish using this tagged
  link so performance can be traced back here." The system enforces the tag at creation; the
  human carries it to the channel. (Same graceful-honesty pattern used elsewhere.)

## Constraints (from CLAUDE.md)
- Agents take AgentContext, return AgentResult; read profile/brief THROUGH ctx; no direct DB.
- `scoped(Model, org_id)` for all tenant reads/writes (rubric, artifacts, versions).
- Reuse the guardrail framework and the synthesis.py fallback pattern; don't reinvent.
- Schema change (content_rubric on org_profiles; grade + utm fields + version link on
  artifacts). Mirror in sql/schema.sql + RLS; on SQLite delete agenthq.db + re-seed so
  create_all() rebuilds; restart uvicorn AND worker. Smoke uses its own DB (don't rm while
  uvicorn runs).
- Don't touch the TS app or .replit's [deployment] block.
- Keep `python -m scripts.smoke` green. ADD tests (hermetic — stub the LLM):
  (1) A generated draft gets a structured grade (overall + per-criterion) stored on the
      artifact; on LLM failure it falls back to "ungraded" without blocking.
  (2) "Give me something better" with a critique produces a NEW version, preserves the prior,
      re-grades the new one; cost captured on the regen.
  (3) A draft carries the four UTM fields and the engine yields a correctly-formatted tagged
      URL; tenant isolation holds for rubric + versions.

## UI (app/static/ui.html)
- Draft view shows the grade: overall score + per-criterion reasons + improvement suggestions.
- A "Give me something better" control with a free-text critique box → shows the new version
  alongside/above the old, with the new grade (so improvement is visible).
- The tagged URL shown with a copy button + the UTM values (editable before accept).
- Rubric editable in Setup; UTM defaults suggested per draft.

## Done looks like
- Generate a draft → see a rubric-based grade + suggestions.
- Type a critique, hit "give me something better" → a new, improved, re-graded version; the old
  one is preserved for comparison; regen cost shown.
- Each draft carries campaign/UTM tags and a ready-to-paste tagged URL.
- Rubric lives on the OrgProfile and is editable. Smoke green with new tests. Pushed.

## Out of scope (do NOT build)
- The analytics dashboard / report ingestion — that's the NEXT build; this one just lays the
  tagging join key.
- Actually publishing or firing the tagged URL anywhere — manual, future, gated.
- Gating approval on the grade — advisory only.
