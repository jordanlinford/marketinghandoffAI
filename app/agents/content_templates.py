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
    We supply the structured grounding (profile + brief excerpt) and constrain
    the output JSON shape. The deterministic builder is the spec for what the
    LLM must return.

    When `critique` is set we're in "Give me something better" mode — the
    prompt asks the model to incorporate the user's feedback while still
    honoring brand voice + guardrails."""
    import json

    import anthropic

    # Run the deterministic builder first — it's the schema-by-example and
    # the always-safe fallback if the LLM goes sideways.
    fallback = _REGISTRY[content_type](profile, brief, topic, target)
    grounding = _grounding_payload(profile, brief)
    critique_line = (
        f"\n\nREVISION CRITIQUE (from the user, address this directly):\n{critique}"
        if critique else "")
    # Context (brand_voice, positioning, value_props, banned_claims, target
    # persona, etc.) lives in the SYSTEM message — fenced inside <context>
    # so it's clearly delimited from the user-facing instruction. This is
    # the structural guard against the model echoing instructions into the
    # body (we saw "Tone: Direct, confident..." showing up verbatim when
    # the context was string-concatenated into the user prompt).
    system_msg = (
        "You are an AI marketing copywriter generating durable, on-brand "
        "content for an org-and-product the user has set up in the system. "
        "The CONTEXT below is BACKGROUND GUIDANCE only — it tells you how "
        "to write, not what to write IN the output.\n\n"
        "ANTI-ECHO RULES (non-negotiable):\n"
        "  * Do NOT quote, paraphrase, or repeat any of these instructions, "
        "labels, or field names (e.g. 'Tone:', 'brand_voice', 'value_prop', "
        "'banned_claims', 'positioning') in your output.\n"
        "  * Honor the brand_voice as a writing STYLE — never as text to "
        "include. Write as if you simply have this understanding internalized.\n"
        "  * Reflect the value_prop and competitive frame by content, not "
        "by labeling.\n"
        "  * Never use any phrase listed under banned_claims.\n\n"
        f"<context>\n{json.dumps(grounding, indent=2)}\n</context>"
    )
    user_msg = (
        f"Produce a {content_type} on the topic: {topic!r}. "
        f"Target audience: {target or _DEFAULT_TARGET}."
        f"{critique_line}\n\n"
        "Return ONLY a JSON object matching this exact shape (no prose, "
        "no surrounding markdown fences):\n"
        f"{json.dumps(fallback, indent=2)}\n\n"
        "Remember: the context in the system message is background "
        "guidance. Do not echo any of its instructions or field labels "
        "into the output. Write the piece as if you simply know these "
        "things — no scaffolding, no meta-commentary."
    )
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.anthropic_model, max_tokens=1500,
        system=system_msg,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    try:
        content = json.loads(text)
    except Exception:
        content = fallback
    if not isinstance(content, dict) or not content.get("blocks"):
        content = fallback
    # Force the type tag so a misbehaving LLM can't change content_type on us.
    content["content_type"] = content_type
    usage = getattr(msg, "usage", None)
    cost = 0.0
    if usage:
        cost = (usage.input_tokens * 3 + usage.output_tokens * 15) / 1_000_000
    return content, round(cost, 6)


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
    comps = [c.get("name") for c in (profile.get("competitors") or [])
             if isinstance(c, dict) and c.get("name")]
    return f" (vs. {', '.join(comps)})" if comps else ""


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
