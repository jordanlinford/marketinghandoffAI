# Content Architecture

A standing reference for how marketing knowledge becomes marketing execution in Agent HQ.
Companion to `docs/cross-layer-disciplines.md`. Future build briefs in the content line
should open by naming which level(s) and which discipline(s) they touch — same convention
as the disciplines doc.

## What this doc is

The backbone model. Features will churn; this hierarchy probably won't. It exists so that
six weeks into implementation, the *shape* of the system is still legible and a new build
can locate itself ("this is a Level 4 derivative renderer, governed by §6 and §7") instead
of being reasoned about from scratch.

It is descriptive of a direction, not a build order. None of this is scheduled ahead of the
prototype. It is committed now because the model is sharp now.

---

## The core shift

Most AI marketing tools are:

    Prompt → Content

Agent HQ is becoming:

    Knowledge Sources → Intelligence → Anchor Asset → Derivative Asset → Campaign → Performance

The difference is that content is *derived from structured, evidence-bound intelligence*,
not generated fresh per prompt. That is what makes claims traceable, messaging consistent
across a fan-out, and trust inheritable downstream. The hierarchy is the product.

---

## The six levels

### Level 1 — Knowledge Sources
The raw inputs a human supplies or the system ingests.
- PMM documents (ingested today)
- User-provided facts
- Metrics
- Benchmarks
- Customer inputs / outcomes
- Sources / citations

**Reframe that matters:** this is not "upload a doc." It is *evidence collection*. An anchor
asset's trustworthiness is bounded by the evidence assembled here before generation. Treated
seriously, Level 1 is the beginning of a research workflow — provenance-tracked inputs, not a
form. See "Intake is a first-class surface" below.

### Level 2 — Intelligence Objects
The structured, queryable representation built from Level 1.
- Structured marketing intelligence (the existing intelligence-object shape)
- Evidence ledger (the existing ledger: entries with source, confidence tier, attributed-vs-
  backdrop flag)

This is the layer the renderers read. It already exists and is proven for reports.

### Level 3 — Anchor Assets
Long-lived, evidence-backed, substantial. *Marketing capital assets*, not throwaway content.
- Report (shipped)
- White Paper
- Buyer's Guide
- Solution Guide
- (candidate, edges — see below) Benchmark Report, Industry Trends Report, Research Brief,
  Webinar Deck, Launch Narrative, Solution Guide

**Anchors are renderers, not content types.** This is the central design decision. A
"White Paper" is not a new generator — it is a render strategy over one intelligence object,
exactly as board / CEO-weekly / sales-leadership are three renderers over one report
intelligence object today. Same intelligence, different packaging:

    Intelligence Object
            ↓
        Renderer  →  Report | Executive Summary | Buyer's Guide | Solution Guide | ...
            ↓
         Output

Consequence: anchor expansion is *new renderers on the existing engine*, not four new
subsystems. This is why "70% of the infrastructure exists" is, if anything, conservative.

### Level 4 — Derivative Assets
Atomized, channel-shaped outputs spawned from an anchor.
- Carousel
- Social post (LinkedIn, etc.)
- Blog
- Email / nurture / sales-enablement email
- Ad copy
- Executive summary
- Webinar promo, podcast outline, video script

Derivatives **derive, never regenerate** (see §7). The content side is cheap — selection and
condensation of already-validated anchor claims. The carousel additionally needs a templated
visual-render path (HTML/SVG template → PNG; populate-and-export, not generative design).

### Level 5 — Campaigns
- Asset orchestration (campaigns reference assets, do not own them — existing principle)
- CTA, audience, distribution

### Level 6 — Performance
- Attribution
- Learning
- Optimization

---

## Trust inheritance through the stack

The disciplines compose down the levels:

- **Level 2** is where evidence binding originates (§6): every quantitative claim ties to a
  ledger entry.
- **Level 3** anchors are gated at generation — an anchor cannot assert a number with no
  ledger source. (Qualitative claims await the claim-taxonomy extension; see below.)
- **Level 4** derivatives inherit the anchor's binding *by construction* via §7 — because a
  derivative introduces no net-new claim, every claim it carries was already bound upstream.
  No re-validation against raw sources is needed; only a check that the derivative did not
  exceed its source. **§7 is what makes §6 transitive.**

The payoff: one validated anchor fans out into many channel assets that are simultaneously
trust-bound *and* message-consistent, because they all re-express the same validated claim
set. That consistency-plus-trust in one mechanism is the compounding engine — and the thing
worth monetizing — not any single asset.

---

## Intake is a first-class surface

"Prompt the user for facts" is not a text box on the generator. It is the UI that populates
the evidence ledger for outward content — the moment honesty enters the system for anchor
assets. What the user supplies is what the asset is *allowed to claim*. A whitepaper generated
from "PMM doc + the facts the user vouches for" has a defined, bounded claim space, and the
renderer cannot exceed it. Build intake as evidence collection with provenance, treated as
its own Phase-1 surface.

---

## Honest edges (where the model may strain — test, don't assume)

The renderer-collapse is clean for the structurally similar anchors: report, white paper,
buyer's guide, solution guide are all evidence-backed long-form prose, and one renderer
interface plausibly serves them. It will strain at the edges of the broader anchor list:

- A **Webinar Deck** is visual/structural, not prose — likely a different renderer class.
- A **Launch Narrative** has a dramatic arc, not a section structure — may not fit the same
  selection-over-intelligence pattern.
- A **Case Study** is the trust outlier: its core claims are third-party *qualitative*
  outcomes ("Acme cut cycle time 40%"). It is the one anchor that genuinely *requires* the
  claim-taxonomy / qualitative-binding extension before it can be trustworthy. It does not
  block the other anchors.

Treat these as "test the abstraction here," not as proof the model is wrong. The renderer
model is the spine; do not oversell its reach to formats that aren't prose-over-intelligence.

---

## Rough phase order (post-prototype; not scheduled)

1. **Anchor expansion** — White Paper, Buyer's Guide, Solution Guide as renderers on the
   existing engine + the intake/evidence-collection surface.
2. **Derivative engine** — Carousel, LinkedIn, Email, Blog. Derive-don't-regenerate (§7).
   Carousel adds the templated visual-render path.
3. **Claim taxonomy / qualitative binding** — cross-cutting infrastructure, parallel track,
   NOT a blocker for (1) and (2). Lands → upgrades all prose assets → unlocks Case Study.
4. **Campaign builder → distribution → performance** — the rest of the operating system.

Disciplines that become load-bearing as this ships: §6 (every anchor), §7 (every derivative),
and §1/§3/§4 throughout. The claim-taxonomy extension (World vs Artifact claims, qualitative
binding) is the prerequisite specifically for Case Study and for upgrading derivative
validation from numeric to full-claim.
