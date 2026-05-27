Goal
Build the content engine: the first action-taking agent. It generates marketing content
grounded in the org's confirmed OrgProfile + latest market brief, and routes every draft
through the existing guardrail + approval-queue path according to an org-level review-mode
setting. This is the build that finally exercises the proposal → guardrail → review →
approve/reject spine that the chassis was designed around (market_intel is read-only and has
never produced a proposal — this agent does).
v1 GENERATES and ROUTES content. It does NOT publish to any live channel (LinkedIn, blog,
etc.) — that's a future, separately-gated build. "Approved" here means "a finished draft,
ready for a human to use or for a future publish-agent to push." Producing platform-ready
drafts is the whole job.
Content object — structured, typed, block-based (important: not a freeform string)
Define a content object shape used by ALL content types now and later:
{ content_type: str, blocks: [ {kind, text, ...} ], metadata: {...} }

blocks is an ORDERED list (a short email = 1–2 blocks; a long report = many). Never store
generated content as a single string — block structure is what lets bigger pieces (reports)
and structured-visual pieces (carousels) slot in later with no rewrite.
content_type drives which template/prompt is used.
This is the one forward-looking decision; keep the v1 types tight (below) but make the
shape general.

v1 content types (FOUR — start tight)

email — personalized outbound email (subject + body blocks).
ad — short LinkedIn/paid ad copy (headline + body + CTA blocks).
social_post — a single social post.
blog_outline — section headings + bullet points (NOT a full blog draft).
Register these in a content_type → template map / registry, so adding a type later
("industry_report", "carousel", "infographic") is registering a template, not rearchitecting.
Leave a comment: visual rendering (carousels/infographics) is a FUTURE layer behind its own
seam — represent structure now, render later. Do NOT build rendering or any other type now.

Recommend what to create (grounded in profile + brief)
The engine should be able to SUGGEST content, not just generate on demand:

Read the org's confirmed OrgProfile (via the ctx accessor added in the profile-into-agents
build) AND the latest market_intel brief/artifact for the org (via scoped()).
From the brief's keyword gaps, high-intent accounts, and content angles + the profile's
ICP/competitors/voice, propose a short list of content ideas
(e.g. {content_type, topic, rationale, target}). The user picks one (or supplies their own
type+topic), and the engine generates it.
Generation MUST use the OrgProfile's brand_voice and respect banned_claims, and reflect the
product/value-prop/competitors. This is the payoff of Setup: content is grounded in real
org data, not a blank prompt.

The review gate — configurable, org-level (NEW setting)
Add content_review_mode to the OrgProfile (enum, default "guardrail"):

"all_through" — drafts auto-pass review (status ready), skip the queue.
"guardrail"  — clean drafts auto-pass; any draft that trips a guardrail goes to the queue.
"gate_all"   — every draft goes to the queue for human approve/reject.
CRITICAL: this setting governs the DRAFT-REVIEW step only. It does NOT govern publishing.
Publishing to any real channel is always a separate, always-gated action (and is out of
scope for v1). So even all_through never means "auto-published" — it means "draft marked
ready without manual review." Make this explicit in code + a comment.

The spine (the point of this build)
Each generation flows: generate (LLM, grounded) → build the structured content object →
run it through the EXISTING guardrail evaluation → route per content_review_mode:

trips a guardrail, or mode is gate_all → create a Proposal in the approval queue
(this is the first real use of the proposals table + queue UI).
clean and mode is guardrail/all_through → mark ready (no proposal needed), but still
recorded as an artifact the user can see.
Approve/Reject in the queue works as the existing UI already supports. Approving a content
proposal sets it ready (NOT published).

Cost (first real API-cost build — make spend visible)
Capture per-run LLM cost on the run (the cost field already exists) and show it in the UI as
it already does for market_intel. Content gen is the expensive, repeated call — surfacing
cost from day one is the spend visibility we want before a team leans on it. (No budget
enforcement in v1 — just visibility.)
Constraints (from CLAUDE.md — do not drift)

Agents take AgentContext, return AgentResult — nothing else. The content agent reads the
profile + brief THROUGH ctx, never querying the DB directly.
scoped(Model, org_id) is the ONLY way to read tenant tables (OrgProfile, prior artifacts,
proposals). No bare selects.
Guardrails gate proposed actions — reuse the existing guardrail framework; don't reinvent it.
The worker is a separate process; generation runs in the worker like market_intel.
Reuse the synthesis.py LLM-or-template fallback pattern (works with no key / on error).
Don't touch the TS app or .replit's [deployment] block.
Schema change (content_review_mode on org_profiles; possibly a content artifact/proposal
field). Mirror in sql/schema.sql + RLS; on SQLite, delete agenthq.db and re-seed so
create_all() rebuilds. Smoke uses its OWN db (don't rm while uvicorn runs — per CLAUDE.md).
Keep python -m scripts.smoke green. ADD tests:
(1) Generating content produces a structured block-based object of the right content_type,
grounded in the org's profile (e.g. brand_voice / competitors reflected), cost recorded.
(2) Gate routing: gate_all → a Proposal lands in the queue; all_through → no proposal,
artifact marked ready; guardrail → a draft that trips a banned_claim goes to the queue
while a clean one passes.
(3) Tenant isolation: the content agent only reads its own org's profile/brief/proposals via
scoped(); approving/rejecting is scoped to the org.
Keep these hermetic (stub the LLM call, like the existing tests).

UI (app/static/ui.html)

A way to trigger content generation (pick a suggested idea or choose type + topic).
Generated drafts render with their blocks; the source/grounding ("from your org profile +
latest brief") shown, like the brief's provenance.
The approval queue (already built) shows content proposals with Approve/Reject — this is the
first time it's populated. Approve → ready; Reject → dismissed (with optional note).
A visible content_review_mode selector (org setting) so the user can switch gate modes.

Done looks like

User triggers content gen; engine suggests ideas from the real Onit profile + brief; user
picks one; engine produces a structured, on-brand draft (email/ad/social/blog_outline).
Per content_review_mode, the draft either passes or lands in the approval queue; the queue
is populated for the first time and Approve/Reject works.
Content reflects the OrgProfile (voice, competitors, ICP) and the brief's gaps.
Per-run cost is visible. Tenant isolation holds. Smoke green with the new tests. Pushed.

Out of scope (do NOT build)

Publishing to any live channel (LinkedIn/blog/etc.) — future, separately gated.
Visual rendering (carousels, infographics) — represent structure later; no renderer now.
Bigger types (industry reports, whitepapers) — the block shape supports them; don't add them.
Budget enforcement / spend caps — visibility only in v1.
Live data sources (ZoomInfo/Apollo) — unrelated; the seam stays.
