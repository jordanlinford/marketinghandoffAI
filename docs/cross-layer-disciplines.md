# Cross-Layer Disciplines

A standing reference for every Agent HQ build brief from this point forward.

## What this doc is

A short, load-bearing rulebook. These are honesty disciplines that *cross layer boundaries* —
they originate at one layer (where the data is computed or the rule is naturally enforced) but
must be **re-asserted at every consumer boundary**, because the receiving layer can violate the
rule even when the originating layer didn't.

This was learned empirically across three live tests:
- **Brand voice echo** — instruction text leaked from system message into the rendered body
- **Untagged organic leakage** — baseline-vs-attributed discipline lived in the dashboard,
  didn't carry into the memory layer
- **Future-date confabulation** — Report Composer's engine returned thin intelligence honestly,
  but the renderer wrote it as if it described the period anyway

Each was the **same architectural pattern**: a discipline correctly enforced at its origin, not
re-enforced at the next consumer that read it.

## The core rule (one sentence)

> Cross-layer disciplines must be **explicitly named in the spec, independently enforced at
> every consumer boundary, and verified by a test at each consumer — not just at the origin**.

## How a spec author applies this

At the top of every new brief, include a short section:

```
## Cross-Layer Disciplines that apply
This build applies the following from docs/cross-layer-disciplines.md:
- §1 thin-data honesty (consumer: ...)
- §3 baseline vs. attributed separation (consumer: ...)
- §4 future-date honesty (consumer: ...)
```

For each listed rule, the spec must:
1. Name the consumer component(s) that must enforce it independently
2. Specify a smoke test asserting **both** the *presence* of the right framing **and** the
   *absence* of the forbidden phrasing/claim
3. If the rule's enforcement isn't obvious from the code structure, name the boundary
   explicitly ("at the renderer's input from intelligence", "at the planner's read from
   query_memory")

A spec that names cross-layer rules but doesn't specify consumer enforcement + tests has not
applied this discipline. Spec review should flag that.

## The rules

### §1 — Thin-data honesty

**Failure mode:** A combination with too few data points produces a *confident-sounding finding
with a low-confidence badge*. The badge is honest; the headline is not. Humans anchor on the
claim and skim the caveat.

**Receiving-component requirement:** When `confidence ∈ {insufficient, low}`, the consumer must
**defer in the headline**, never quote a rate. Numbers can live in inspectable detail
(metric_basis, evidence panel) but the one-liner says *"only N data points so far — not enough
to call a pattern yet"*, not *"X drove Y% conversions (low confidence)"*. Low/insufficient
patterns may **never** reorder defaults or be weighted above `watching`.

**Test pattern (presence + absence):**
- *Presence*: the observation/headline contains deferring phrasing (regex for "not enough",
  "watching", "too thin")
- *Absence*: the observation does NOT contain a rate string (regex against "X conv/100 clicks",
  "Y%", etc.) when confidence is insufficient
- *Behavior*: the recommendation system does not place this pattern at primary weight

**Originating layer:** `app/memory/query.py`
**Consumers to enforce:** Memory display, Campaign planner, Report Composer renderers,
Insights "What the system has learned", any future channel-recommendation surface

---

### §2 — No brand-voice / instruction echo

**Failure mode:** Instructions or style guidance from the system message (brand voice, tone
markers, "embody this voice — do not describe it", format requirements) leak verbatim into the
generated output, often as labeled JSON or quoted instructions.

**Receiving-component requirement:** Instruction content goes in the **system message** as
guidance, never echoed into the body. The placeholder schema shown to the user message is a
*schema*, not a *rendered fallback*. Banned phrases list does not contain the forbidden labels
themselves (don't-think-elephant rule). Every LLM-touching consumer that adds new system-message
content must apply the same discipline.

**Test pattern:**
- *Presence*: the output contains the requested content type
- *Absence*: a TONE_MARKER (or distinctive instruction substring) inserted into the system
  message does NOT appear anywhere in the rendered output
- *Behavior*: regenerate produces a new artifact without the marker either

**Originating layer:** `app/agents/content_engine.py`
**Consumers to enforce:** All renderers — content_engine, report_composer renderers, campaign
planner LLM calls, any future agent that takes a system message + user message and produces
prose

---

### §3 — Baseline vs. attributed separation

**Failure mode:** Untagged volume (no campaign UTM) is treated as marketing-attributed signal.
This inflates apparent channel performance, makes "organic" surface as a confident
recommendation off backdrop volume, and lets the system claim credit for activity it didn't
drive.

**Receiving-component requirement:** Rows without `utm_campaign` are **funnel backdrop only**.
They may appear in funnel totals (clearly labeled). They must NOT:
- Generate channel/audience/content_type confidence scores
- Appear in actionable pattern lists
- Be cited in a report as something marketing did or drove
- Influence channel recommendations or campaign proposals

If a report mentions untagged volume at all, it does so as *backdrop context*, explicitly
separated from attributed numbers.

**Test pattern:**
- *Presence*: attributed funnel numbers excluded untagged volume in their totals
- *Absence*: untagged channels (e.g. "organic" with no UTMs) do NOT appear in
  channel_recommendations / memory patterns / report attribution claims
- *Behavior*: a mix of tagged + untagged rows produces patterns only from the tagged subset

**Originating layer:** Dashboard attribution logic in `app/api/dashboard.py`
**Consumers to enforce:** Memory query, Campaign planner, Report Composer engine (especially
`top_content` and `period_summary`), Insights suggestions

---

### §4 — Future-date honesty

**Failure mode:** A consumer asked about a period outside available data (future, or pre-data)
produces a plausible narrative by pulling related patterns and writing them as if they describe
the requested period. "Email was the clear leader in July" off May data is the canonical example.

**Receiving-component requirement:** Any consumer that accepts a date scope must check
`scope.end > today` (and `scope.start > today`) at its input boundary. When the scope is
out-of-range:
- Period-specific aggregations (period_summary, deltas, top_content, attribution claims) are
  **omitted entirely**, not stubbed-and-rendered
- Memory or reference data, if shown, is **explicitly labeled "as of today, not for the
  requested period"** — every place it appears, not just once at the top
- The output names the scope explicitly ("No data exists for [period]") so the user can't miss
  the gap

**Test pattern:**
- *Presence*: output explicitly says "no data exists" / "in the future" / names the scope
- *Absence*: no delta language (regex: `up|down|rose|fell|grew|declined.*\d+%`), no
  attribution language (regex: `drove|led to|was the clear|highest-converting`) about the
  requested period
- *Behavior*: across all renderers/consumers, the out-of-scope payload triggers the stub path

**Originating layer:** Report Composer engine (`app/reports/intelligence.py`)
**Consumers to enforce:** All three renderers, any future renderer (slide outline, Slack),
campaign analysis if it ever takes a date scope, Insights suggestion engine

---

### §5 — Unknown vs. zero

**Failure mode:** The system silently conflates "we didn't measure it" with "the value is zero."
"Email had 0 conversions" might mean *measured zero* or *not measured* — radically different
claims with the same surface form.

**Receiving-component requirement:** Every metric carrying a number must also carry its
**measurement status** — was this *observed* (a real measurement, possibly zero), *missing*
(not measured, no data), or *partial* (some sources reported, others didn't)? Consumers
displaying or claiming numbers must distinguish:
- *Observed zero* → "0 conversions" is a finding, can be quoted
- *Missing* → "not measured" / "no data" — NOT "0", NOT silence (silence is the bug)
- *Partial* → numbers + the gap named ("X attributed; Y untagged volume in scope, excluded
  from this total")

Reports especially must not let a missing measurement render as a confident zero.

**Test pattern:**
- *Presence*: where data is missing, the output says so explicitly
- *Absence*: a missing measurement does NOT appear as "0" without qualification, and is NOT
  silently omitted (which reads as "not noteworthy")
- *Behavior*: the difference between an observed zero and a missing measurement is visible to
  the user in at least one place per claim

**Originating layer:** Substrate ingest (metric_points + funnel aggregation)
**Consumers to enforce:** Dashboard, Report Composer (top_content, period_summary,
campaigns), Memory display, any future analytics surface. **This is new and not yet enforced
everywhere; treat it as the active discipline to add as builds touch each consumer.**

---

### §6 — Generated vs. observed claims

**Failure mode:** LLM-produced prose around weak data sounds authoritative and indistinguishable
from measured fact. "Email is your best channel" might be a *measured finding* (high-confidence
memory pattern with evidence) or *LLM prose* around thin data — and the user can't tell which.

**Receiving-component requirement:** Output that mixes *measured facts* with *LLM-generated
narrative* must let the user trace each claim back to its basis. In Report Composer this looks
like the Evidence Panel: every quantitative claim in the prose links to its underlying number,
data source, and confidence tier. In content generation, factual claims that aren't grounded in
the product profile / extracted insights / memory should be either flagged or removed (not
fabricated).

The standing rule: **if the system can't say *what is this claim based on*, it shouldn't make
the claim**. This is the discipline that turns Report Composer from "generated text" into
"generated text whose every claim is auditable."

**Test pattern:**
- *Presence*: each quantitative claim in the output is traceable to an intelligence-object
  field, a metric_point, or a memory pattern
- *Absence*: the output does NOT contain quantitative claims with no underlying support (regex
  for percentages / counts / "X%" / "N conversions" not present in the intelligence object)
- *Behavior*: an Evidence Panel (or equivalent surface) can render the basis for any cited
  number on demand

**Originating layer:** Report Composer renderers, content_engine
**Consumers to enforce:** Report Composer (especially Evidence Panel when v2 ships), content
generation for long-form types (whitepaper, case study, report), any LLM-touching surface that
quotes numbers. **This is new and the most ambitious of the rules; it becomes load-bearing
when Report Composer v2 and Enhanced Content Types ship.**

**§6 Principle — Evidence describes knowledge, not inventory.**
Never create an evidence-ledger record solely to satisfy the validator. If a ledger entry
exists only because a check demanded a source for a number, the entry is wrong and the check
is mis-scoped. The ledger records claims about reality; it must never be padded with
implementation/inventory facts (counts of ads, emails, sections, recommendations) to turn a
finding green. A validator that can be satisfied by manufacturing its own inputs is not
enforcing honesty — it is training the renderer to fabricate sources. When a number won't
bind, the question is "is this a claim about the world?" — not "what entry would make this
pass?"

**§6 Extension — Claim Taxonomy & Qualitative Binding.** (Supersedes the earlier
qualitative-binding note — they are the same problem; this is the sharper framing.)

The current validator's definition of a bindable claim is "any number." That is too broad on
one axis and too narrow on another:
- *too broad*: it flags **Artifact Claims** (numbers describing the report's own contents)
  which cannot and must not bind — caught live: *"2 ads, 1 social post"* in a sales report
  fired CRITICAL §6 with nothing to honestly bind to.
- *too narrow*: it ignores non-numeric **World Claims** (a fabricated *"Acme renewed after our
  campaign"* emits no number and passes today).

Both are the same underlying question: **what constitutes a claim about reality?**
Current answer: any number. Correct answer: any statement about the world.

The taxonomy the future validator should encode:

```
World Claim        → MUST bind to evidence
  ├─ Quantitative   (a metric / outcome / rate)
  ├─ Qualitative    (a stated fact about an entity or event)
  └─ Comparative    (a relative claim: more/less/best/fastest)
Artifact Claim     → self-describing, NEVER binds
  ├─ Asset Count
  ├─ Section Count
  ├─ Generated-Deliverable Count
  └─ Structural Metadata
```

Only World Claims require binding. Artifact Claims describe the document itself and are
self-evidencing. The distinction is by REFERENCE (world vs. artifact), not by the number's
size or type — *"ignore small integers"* and *"bind everything"* are both wrong for that
reason.

Scope when built: classify each candidate claim World vs. Artifact before requiring a binding;
extend binding to qualitative + comparative World Claims (a larger renderer change — every
factual claim, not just numeric, carries a marker). Governed by the §6 Principle above:
the fix is sharper claim classification, NEVER a looser scanner and NEVER manufactured
ledger entries.

Status: BACKLOG. Not scheduled. Named so the next spec author finds it before reaching for
the easy wrong fix (binding the artifact counts).

---

### §7 — Derivation Discipline

**Failure mode:** a derivative asset (carousel, social post, email, blog, exec summary)
introduces a factual claim — a statistic, a customer outcome, a market assertion — that does
not exist in its source anchor. The derivative looks consistent and on-brand while quietly
fabricating, and because it ships to channels, the fabrication goes straight out the door.
Independently, uncontrolled derivatives drift from each other and from the anchor, destroying
message consistency across a fan-out.

**Receiving-component requirement:** a derivative renderer may ONLY select, summarize,
condense, reorder, reframe, or reformat claims present in its source anchor. It may NOT
invent, expand beyond source claims, or introduce new statistics, customer outcomes, or
market assertions. The derivative validator checks the derivative's claims against the
ANCHOR's validated claim set / evidence ledger — not against raw sources.

**Why it composes with §6:** a derivative that introduces no net-new claim inherits the
anchor's evidence binding transitively. Every claim it carries was already bound when the
anchor passed §6. So derivative validation reduces to a containment check (derivative claims
⊆ anchor claims), not a re-binding against Level-1 sources. §7 is what makes §6 transitive
down the content stack.

**Test pattern:**
- *Presence:* every factual claim in the derivative traces to a claim in the source anchor.
- *Absence:* the derivative contains no statistic / outcome / assertion absent from the anchor
  (the load-bearing check — same absence-assertion discipline as §6's bare-number test, now
  scoped to "claim not in source").
- *Behavior:* a derivative that smuggles in a net-new number/outcome FAILS validation against
  the anchor; a derivative that only re-expresses anchor claims passes without re-validating
  raw sources.

**Originating layer:** Level 4 derivative renderers.
**Consumers to enforce:** every derivative renderer (carousel, social, email, blog, exec
summary, ad copy). Becomes load-bearing when the Derivative Engine ships. See
docs/content-architecture.md for the full content hierarchy this sits in.

---

## How tests should be written

A consumer-boundary test is **not** "the function returned the right shape." It's:

1. **Presence** — assert the right framing appears (regex / substring / structural check)
2. **Absence** — assert the forbidden phrasing or claim does NOT appear (regex against the
   classic violation patterns: "X% up", "drove most", "was the clear winner", raw rate strings
   on thin data, etc.)
3. **Behavior** — assert the downstream system *acts on* the discipline (e.g. a watching
   pattern doesn't reorder defaults; an untagged row doesn't appear in patterns; a future scope
   triggers stub path)

A test that only checks #1 is the kind of test that goes green while live use catches the bug.
Three live-test escapes this project hit (brand voice, organic, future-date) all had passing
smoke at the structural layer. The lesson: **for cross-layer disciplines, every smoke test
needs both presence and absence assertions.**

## What this doc is NOT

- It's not a manifesto. It's a checklist a spec author runs.
- It's not exhaustive. New rules will be added as the system catches new failure modes. When
  that happens, add the rule here with the same shape (failure mode / receiver requirement /
  test pattern / originating layer / consumers) before writing the build that introduces it.
- It's not a substitute for live testing. It catches *known* failure modes earlier. Live
  testing remains the highest-leverage way to find the next one.

## Standing application going forward

Every brief from this point opens with the Cross-Layer Disciplines section naming applicable
rules and their consumer boundaries. Every consumer that handles intelligence/memory/metric
data writes its tests with presence + absence + behavior assertions. If a brief skips this,
that's a spec defect to flag on review, not a stylistic choice.

If two consecutive builds reference this doc and it doesn't change how they're specced or
tested, the doc isn't working — revise it.
