"""
Content templates — the registry the content_engine renders through.

Each registered type produces the same shape: a content object whose `blocks`
is an ORDERED list. A short email is 1–2 blocks; a long report would be many.
The list is the single forward-looking decision — never store generated copy
as a single string.

    {content_type, blocks: [ {kind, text, ...} ], metadata: {...}}

v1 ships four types: email, ad, social_post, blog_outline. Adding a fifth
("industry_report", "carousel", "infographic") is registering a builder here —
not a chassis rewrite.

VISUAL RENDERING (carousels, infographics, decks) is intentionally NOT here.
This module owns STRUCTURE only. A future render-layer will sit behind its own
seam and consume these blocks; don't put renderer code here.

LLM-or-template fallback follows the synthesis.py pattern: if a key is
configured, ask Claude to fill the blocks; if not (or the call fails), a
deterministic template still produces grounded copy. Both paths honor
brand_voice / product / value_prop / competitors and avoid banned_claims —
banned phrases are ALSO enforced at the guardrail layer (defense in depth).
"""
from __future__ import annotations

from typing import Callable

from app.config import get_settings

# A builder takes (profile, brief, topic, target) and returns the content
# object dict. Profile is the confirmed OrgProfile dict from ctx; brief is the
# latest market_brief artifact body (or None); topic / target are the user's
# pick from the suggestion list (or supplied manually).
Builder = Callable[[dict, dict | None, str, str], dict]

_REGISTRY: dict[str, Builder] = {}

_DEFAULT_TARGET = "the org's ICP"


def register(content_type: str):
    def deco(fn: Builder):
        _REGISTRY[content_type] = fn
        return fn
    return deco


def available_types() -> list[str]:
    return sorted(_REGISTRY.keys())


def build(content_type: str, profile: dict, brief: dict | None,
          topic: str, target: str, critique: str = "") -> tuple[dict, float]:
    """Build a content object of `content_type`. Returns (content, cost_usd).
    Cost is non-zero only on the LLM path; the template fallback is free.

    `critique` is the optional free-text guidance from "Give me something
    better" — empty on first generation. Passed through to the LLM in the
    prompt; the deterministic fallback folds it into the topic so the
    revision is visibly different from the original."""
    if content_type not in _REGISTRY:
        raise ValueError(f"Unknown content_type '{content_type}'. "
                         f"Available: {available_types()}")
    settings = get_settings()
    if settings.anthropic_api_key:
        try:
            return _llm_build(content_type, profile, brief, topic, target,
                              settings, critique=critique)
        except Exception:
            pass  # never let synthesis crash a run; fall back
    # Deterministic fallback: when a critique is present, surface it in the
    # topic so the rendered output reflects the revision request — even
    # without an LLM. The original topic is still used by the caller for
    # UTM tagging (so utm_campaign stays stable across versions).
    topic_for_template = (f"{topic} — addressing: {critique}"
                          if critique else topic)
    return _REGISTRY[content_type](profile, brief, topic_for_template, target), 0.0


# ---- LLM path -------------------------------------------------------------
def _llm_build(content_type: str, profile: dict, brief: dict | None,
               topic: str, target: str, settings,
               critique: str = "") -> tuple[dict, float]:
    """Ask Claude to produce the same shape the template fallback produces.

    PROMPT DESIGN (the no-echo redesign — see history). The pre-fix prompt
    dumped the full profile as labeled JSON (`{"brand_voice": "<text>", ...}`)
    inside <context>. The model interpreted the labels as fields-to-narrate
    and produced bodies like "...changes that: Tone: <brand_voice value>"
    and parenthesized competitor lists "(Ironclad, Agiloft, ...)". Telling
    it "don't echo 'Tone:'" only put that string in its working context.

    The fix: NO labeled fields anywhere in the prompt the model sees.
    Context is written as natural-language instruction (voice woven into
    a sentence about HOW to write, not as a "brand_voice" key). The user
    message shows a PLACEHOLDER schema, not the fallback's already-written
    text, so the model fills in real content instead of mimicking the
    fallback verbatim. Single source of truth for both generate and the
    regenerate-with-critique path.

    When `critique` is set we're in "Give me something better" mode — the
    prompt asks the model to incorporate the user's feedback while still
    honoring voice + guardrails."""
    import json

    import anthropic

    # Run the deterministic builder so we have the JSON shape to constrain
    # the model with — but we'll send a PLACEHOLDER version of it, not the
    # rendered fallback text, to avoid the model copying our fallback prose.
    fallback = _REGISTRY[content_type](profile, brief, topic, target)
    schema_example = _placeholder_schema(content_type, fallback)
    style_brief = _compose_style_brief(profile, brief)

    critique_line = (
        f"\n\nUser is asking for a revision — they want this changed: "
        f"{critique}"
        if critique else "")

    # System message — instruction-shaped, no labeled fields. The voice +
    # banned phrases + audience + competitive frame are woven into prose
    # so the model receives them as guidance, not as content to quote.
    system_msg = (
        "You are a senior B2B marketing copywriter. Produce polished, "
        "ready-to-publish marketing copy — natural finished prose a "
        "marketer can paste directly into a campaign.\n\n"
        f"{style_brief}\n\n"
        "HARD RULES (non-negotiable):\n"
        "  * Produce only the finished copy. No commentary, no scaffolding, "
        "no labels inside the block text. The JSON shape already labels the "
        "blocks (subject, body, cta, etc.); the text inside each block is "
        "JUST the polished content — never a prefix like \"Subject:\", "
        "\"Body:\", \"Voice:\", \"Style:\", or any other meta-tag.\n"
        "  * Do not quote or paraphrase any of these instructions back. "
        "Write as if you have this understanding internalized.\n"
        "  * Do not produce parenthesized comma-separated lists of "
        "competitors. If you reference a competitor at all, do it naturally "
        "in prose — by name, at most one or two, where it actually helps "
        "the message.\n"
        "  * Do not invent statistics, customer quotes, or proof points "
        "that aren't in the style brief above."
    )

    user_msg = (
        f"Write a {content_type} on the topic: {topic!r}. "
        f"Target audience: {target or _DEFAULT_TARGET}."
        f"{critique_line}\n\n"
        "Return ONLY a JSON object matching this exact shape (no prose, "
        "no markdown fences). Each block's `text` field below contains a "
        "DESCRIPTION of what to put there — replace it with the actual "
        "polished content; never copy the placeholder text or its angle "
        "brackets into your output.\n\n"
        f"{json.dumps(schema_example, indent=2)}"
    )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.anthropic_model, max_tokens=1500,
        system=system_msg,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    # Defensive parse — real models sometimes wrap JSON in ```json fences
    # or sneak a leading sentence in. We try strict JSON first, then
    # fence-strip, then "first {...} substring". Fall through to the
    # deterministic template only when none of those work.
    content = _parse_json_envelope(text) or fallback
    if not isinstance(content, dict) or not content.get("blocks"):
        content = fallback
    # Force the type tag so a misbehaving LLM can't change content_type on us.
    content["content_type"] = content_type
    usage = getattr(msg, "usage", None)
    cost = 0.0
    if usage:
        cost = (usage.input_tokens * 3 + usage.output_tokens * 15) / 1_000_000
    return content, round(cost, 6)


def _parse_json_envelope(raw: str) -> dict | None:
    """Try several common LLM response shapes before giving up:
       1. Strict JSON.
       2. ```json ... ``` markdown fence.
       3. The largest balanced { ... } substring (handles "Sure, here:
          { ... }" prefixes).
    Returns None when none works — the caller falls back to the
    deterministic template."""
    import json as _json

    s = (raw or "").strip()
    if not s:
        return None
    # 1. Strict.
    try:
        v = _json.loads(s)
        return v if isinstance(v, dict) else None
    except Exception:
        pass
    # 2. Markdown fence.
    if s.startswith("```"):
        inner = s.strip("`").strip()
        # Strip optional language tag on first line.
        if "\n" in inner and inner.split("\n", 1)[0].strip().lower() in (
                "json", "jsonc"):
            inner = inner.split("\n", 1)[1]
        try:
            v = _json.loads(inner.strip())
            return v if isinstance(v, dict) else None
        except Exception:
            pass
    # 3. First {...} substring — handles "Here's the JSON: { ... }".
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            v = _json.loads(s[start:end + 1])
            return v if isinstance(v, dict) else None
        except Exception:
            pass
    return None


def _compose_style_brief(profile: dict, brief: dict | None) -> str:
    """Compose a natural-language style brief the LLM reads as
    instruction — NOT a labeled JSON object the model could mistake for
    "fields to narrate". Each piece of profile data is woven into a
    sentence about HOW to write, so the model takes it as guidance.

    Critical: do NOT include the words "brand_voice", "Tone", "value_prop",
    "banned_claims", "positioning", or "competitors" as labels. The model
    saw those words in the old prompt and produced "Tone: <voice>" leaks.
    """
    parts: list[str] = []
    product_summary = (profile.get("product_summary") or "").strip()
    if product_summary:
        parts.append(f"You write for: {product_summary}")
    value_prop = (profile.get("value_prop") or "").strip()
    if value_prop:
        parts.append(f"The core value you're conveying — what makes a buyer "
                     f"pick this over the alternative: {value_prop}")
    voice = (profile.get("brand_voice") or "").strip()
    if voice:
        # Voice is GUIDANCE about HOW to write — woven into instruction,
        # never presented as a labeled field. The model embodies the
        # style; it does not narrate it.
        parts.append(f"Voice you write in (embody this — DO NOT describe "
                     f"or label the voice in your output): {voice}")
    icp = profile.get("icp") or {}
    titles = icp.get("titles") or []
    industries = icp.get("industries") or []
    if titles or industries:
        aud_bits = []
        if titles:
            aud_bits.append(", ".join(titles[:3]))
        if industries:
            aud_bits.append("in " + ", ".join(industries[:3]))
        parts.append("Audience: " + " ".join(aud_bits))
    conversion_goal = (profile.get("conversion_goal") or "").strip()
    if conversion_goal:
        parts.append(f"The action you want the reader to take: {conversion_goal}")
    banned = [str(b).strip() for b in (profile.get("banned_claims") or [])
              if str(b).strip()]
    if banned:
        quoted = "; ".join(f'"{b}"' for b in banned)
        parts.append(f"These exact phrases would reject the draft — never "
                     f"use any of them: {quoted}")
    # Competitors: spelled out as a comma-separated list ONLY in
    # instructional prose, and explicitly framed as "context for shaping,
    # never raw list output". The HARD RULES in the system message also
    # forbid the model from echoing them parenthetically.
    comps = [c.get("name") for c in (profile.get("competitors") or [])
             if isinstance(c, dict) and c.get("name")]
    if comps:
        named = ", ".join(comps[:5])
        parts.append(
            f"The offering competes against {named}. Reference at most one "
            "or two by name in the copy when it serves the message; never "
            "produce a parenthesized list.")
    # Product layer extras (when a product is in scope).
    product = profile.get("product") or {}
    if isinstance(product, dict):
        positioning = (product.get("positioning") or "").strip()
        if positioning:
            parts.append(f"How this specific product is positioned: {positioning}")
        product_vps = product.get("value_props") or []
        if product_vps:
            parts.append("Specific value props for this product (use these "
                         "for substance; do not list them as bullets): "
                         + "; ".join(product_vps[:5]))
        prod_comps = product.get("product_competitors") or []
        prod_comp_names = [c.get("name") for c in prod_comps
                           if isinstance(c, dict) and c.get("name")]
        if prod_comp_names:
            named = ", ".join(prod_comp_names[:5])
            parts.append(
                f"Direct competitors for this product: {named}. Same rule: "
                "name at most one or two in prose when useful; never list.")
    # Light brief context (just the bottom-line takeaways, not labels).
    brief_struct = ((brief or {}).get("body") or {}).get("structured") or {}
    if brief_struct:
        recs = brief_struct.get("recommendations") or []
        if recs:
            parts.append("Recent market intel takeaway(s) you can lean on: "
                         + "; ".join(str(r) for r in recs[:3]))
    return "\n".join(f"- {p}" for p in parts) if parts else \
        "- No additional profile context available; rely on the topic alone."


def _placeholder_schema(content_type: str, fallback: dict) -> dict:
    """Return the same JSON shape the fallback produces, but with each
    block's text replaced by a SHORT description of what to write. The
    model sees the shape (kinds + order) without being tempted to copy
    real prose from the fallback into its own output."""
    placeholders = {
        "subject": "<the subject line — short, specific, no labels>",
        "headline": "<the headline — punchy, no labels>",
        "body": "<the main body — polished finished prose, no labels>",
        "cta": "<the call to action — one short verb-first phrase>",
        "section": "<a section heading>",
        "bullet": "<a single bullet point — one sentence>",
    }
    out = {**fallback, "blocks": []}
    for b in fallback.get("blocks", []) or []:
        kind = b.get("kind", "body")
        out["blocks"].append({
            "kind": kind,
            "text": placeholders.get(kind, f"<{kind} content>"),
        })
    return out


def _grounding_payload(profile: dict, brief: dict | None) -> dict:
    """The minimum slice of profile + brief the LLM needs. Keeps prompts tight."""
    brief_struct = ((brief or {}).get("body") or {}).get("structured") or {}
    # messaging_notes (objection_handling, launch_messaging) flow in
    # through the resolved profile from document-extraction promotions.
    # The LLM treats them as "consider these objections / launch themes
    # the framework calls out" rather than as hard inputs — lightweight,
    # no new agent architecture (see docs/document-ingestion-brief.md).
    notes = profile.get("messaging_notes") or {}
    return {
        "profile": {
            "product_summary": profile.get("product_summary", ""),
            "value_prop": profile.get("value_prop", ""),
            "brand_voice": profile.get("brand_voice", ""),
            "banned_claims": profile.get("banned_claims") or [],
            "icp": profile.get("icp") or {},
            "competitors": profile.get("competitors") or [],
            "keywords": profile.get("keywords") or [],
            "conversion_goal": profile.get("conversion_goal", ""),
            "messaging_notes": {
                "objection_handling": notes.get("objection_handling") or [],
                "launch_messaging": notes.get("launch_messaging") or [],
            },
        },
        "brief": {
            "inputs": brief_struct.get("inputs", {}),
            "top_targets": (brief_struct.get("top_targets") or [])[:5],
            "recommendations": brief_struct.get("recommendations", []),
        } if brief_struct else None,
    }


# ---- Deterministic builders (the spec + the always-works fallback) --------
# IMPORTANT (do not drift): brand_voice is GUIDANCE about how to write, not
# text to include in the body. The deterministic templates intentionally
# do NOT echo the brand_voice string — the user reported drafts with
# "Tone: Direct, confident, practitioner-first." leaking into the email
# body. Templates surface positioning, value_prop, and competitors as
# CONTENT; brand_voice is for the LLM path's system message + (in fallback)
# implicit influence only.
def _competitor_clause(profile: dict) -> str:
    """Natural-prose competitor mention for the deterministic fallback.
    NEVER produces a parenthesized comma-separated list — that's exactly
    the leak pattern the LLM started copying. We mention at most ONE
    competitor, woven into prose ("a cleaner option than X"). Anything
    fancier is the LLM's job; the deterministic template stays honest."""
    comps = [c.get("name") for c in (profile.get("competitors") or [])
             if isinstance(c, dict) and c.get("name")]
    if not comps:
        return ""
    return f" — a cleaner option than {comps[0]}"


def _cta(profile: dict) -> str:
    goal = (profile.get("conversion_goal") or "book a demo").strip()
    return f"{goal[:1].upper()}{goal[1:]} →"


@register("email")
def _email(profile: dict, brief: dict | None, topic: str, target: str) -> dict:
    product = profile.get("product_summary") or "our platform"
    value = profile.get("value_prop") or "Cut hours of manual work."
    aud = target or "your team"
    return {
        "content_type": "email",
        "blocks": [
            {"kind": "subject",
             "text": f"{topic} — a faster way for {aud}"},
            {"kind": "body",
             "text": (f"Hi —\n\n{aud.title()} teams keep telling us {topic.lower()} "
                      f"is the biggest drag on the week. {product} changes "
                      f"that — {value}{_competitor_clause(profile)}.\n\n"
                      f"Worth 15 minutes to see if it fits?")},
            {"kind": "cta", "text": _cta(profile)},
        ],
        "metadata": {"topic": topic, "target": aud},
    }


@register("ad")
def _ad(profile: dict, brief: dict | None, topic: str, target: str) -> dict:
    value = profile.get("value_prop") or "Cut hours of manual work."
    aud = target or "in-house teams"
    return {
        "content_type": "ad",
        "blocks": [
            {"kind": "headline", "text": f"{topic} without the busywork."},
            {"kind": "body",
             "text": f"For {aud}: {value}{_competitor_clause(profile)}."},
            {"kind": "cta", "text": _cta(profile)},
        ],
        "metadata": {"topic": topic, "target": aud, "platform": "linkedin"},
    }


@register("social_post")
def _social(profile: dict, brief: dict | None, topic: str, target: str) -> dict:
    value = profile.get("value_prop") or "Cut hours of manual work."
    return {
        "content_type": "social_post",
        "blocks": [
            {"kind": "body",
             "text": (f"{topic}: most teams treat this as inevitable. "
                      f"It isn't. {value}{_competitor_clause(profile)}.")},
            {"kind": "cta", "text": _cta(profile)},
        ],
        "metadata": {"topic": topic, "target": target or "professional network"},
    }


@register("carousel")
def _carousel(profile: dict, brief: dict | None, topic: str, target: str) -> dict:
    # Structure-only — the brief calls this out explicitly: generate the
    # per-slide block structure, do NOT render visuals. A future "render
    # layer" turns this list into PNGs / a deck. Until then the human ships
    # it to a designer or uses the copy as-is in a swipe carousel tool.
    value = profile.get("value_prop") or "Cut hours of manual work."
    aud = target or "your audience"
    cc = _competitor_clause(profile)
    slides = [
        {"kind": "slide_cover", "title": topic,
         "subtitle": f"For {aud}"},
        {"kind": "slide", "headline": "The default isn't working",
         "body": f"Most {aud} teams treat slow {topic.lower()} as inevitable."},
        {"kind": "slide", "headline": "What good looks like",
         "body": f"{value}{cc}."},
        {"kind": "slide", "headline": "Three signals you'd see",
         "body": "Cycle time falls. Hand-offs shrink. Approvals close in days, not weeks."},
        {"kind": "slide_cta", "text": _cta(profile)},
    ]
    return {
        "content_type": "carousel",
        "blocks": slides,
        "metadata": {
            "topic": topic, "target": aud, "platform": "linkedin",
            "structure_only": True,
            "note": ("Structure-only — visual rendering is a future layer. "
                     "Each slide is a content block; ship to a designer or "
                     "paste into a swipe-carousel tool."),
        },
    }


@register("blog_outline")
def _blog_outline(profile: dict, brief: dict | None, topic: str, target: str) -> dict:
    aud = target or "buyers"
    value = profile.get("value_prop") or "the value we deliver"
    sections = [
        {"kind": "section", "text": f"Why {topic} is the wrong default"},
        {"kind": "bullet",  "text": f"What {aud} are actually trying to do"},
        {"kind": "bullet",  "text": f"Where today's tools fall short{_competitor_clause(profile)}"},
        {"kind": "section", "text": "A better pattern"},
        {"kind": "bullet",  "text": value},
        {"kind": "bullet",  "text": "Three concrete examples from our customers"},
        {"kind": "section", "text": "How to get started"},
        {"kind": "bullet",  "text": _cta(profile)},
    ]
    return {
        "content_type": "blog_outline",
        "blocks": sections,
        "metadata": {"topic": topic, "target": aud,
                     "note": "Outline only — full draft is a future content type."},
    }
