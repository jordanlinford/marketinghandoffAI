"""
Content grader — a SEPARATE LLM call that scores a draft against the org's
content rubric. Returns structured JSON; on any failure (no key, parse error,
API error) it falls back to a neutral "ungraded" result so the draft itself
is never blocked.

Honesty rule (from the brief): grades are ADVISORY, never gating. A low score
is shown to the human; they decide. Gating on a self-grade is a footgun —
the model is grading its own output, and false negatives would block work
silently. Surface it, let the human read it.

Cost: each grade is its own paid LLM call; the agent adds it to the run's
total so a regen+grade+grade chain is fully accounted for.
"""
from __future__ import annotations

import json

from app.config import get_settings

# Built-in rubric used when an org's content_rubric is empty. Kept in code
# (not seeded into the DB) so updating it flows through to every org without
# a migration. Each entry: {name, description, weight?}.
DEFAULT_RUBRIC: list[dict] = [
    {"name": "on_brand",
     "description": "Matches the org's brand_voice (tone, register, vocabulary).",
     "weight": 1.0},
    {"name": "on_strategy",
     "description": "Serves the stated conversion goal and addresses the ICP.",
     "weight": 1.0},
    {"name": "clarity",
     "description": "One clear CTA. No fluff, hedging, or buzzword stacks.",
     "weight": 1.0},
    {"name": "specificity",
     "description": "Concrete claims grounded in the value prop — not generic.",
     "weight": 1.0},
    {"name": "no_banned_claims",
     "description": "Avoids any banned_claims phrase (also enforced by "
                    "the content guardrail).",
     "weight": 1.0},
]


def resolve_rubric(profile: dict | None) -> list[dict]:
    """Return the rubric the grader should use. Falls back to DEFAULT_RUBRIC
    when the profile is missing one or saved an empty list."""
    if profile and profile.get("content_rubric"):
        return list(profile["content_rubric"])
    return list(DEFAULT_RUBRIC)


def _parse_json_envelope(raw: str):
    # Same shape as the propose path's parser: tolerate raw JSON, a
    # ```json``` fence, or JSON embedded in surrounding prose. Anthropic
    # frequently wraps structured output in markdown fences even when the
    # prompt asks for JSON; that should not silently drop a grade.
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        pass
    if s.startswith("```"):
        inner = s.strip("`").strip()
        if "\n" in inner and inner.split("\n", 1)[0].strip().lower() in (
                "json", "jsonc"):
            inner = inner.split("\n", 1)[1]
        try:
            return json.loads(inner.strip())
        except Exception:
            pass
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(s[start:end + 1])
        except Exception:
            pass
    return None


def _ungraded(rubric: list[dict], reason: str) -> dict:
    """Neutral, honest "we didn't grade this" placeholder. The UI renders the
    reason; the artifact is still usable. Returns the same shape as a real
    grade so downstream code never needs to special-case None."""
    return {
        "status": "ungraded",
        "reason": reason,
        "overall": None,
        "per_criterion": [{"name": c.get("name", ""), "score": None,
                           "reason": "Not graded"} for c in rubric],
        "suggestions": [],
    }


def grade_content(content: dict, profile: dict | None,
                  rubric: list[dict] | None = None) -> tuple[dict, float]:
    """Grade a content object against the rubric. Returns (grade, cost_usd).

    Honors the same LLM-or-fallback pattern as synthesis.py: with a key, ask
    Claude; without a key OR on any failure (parse, network, etc.), return
    an ungraded placeholder. The caller (the agent) treats the grade as
    advisory — never gating.
    """
    rubric = rubric if rubric is not None else (profile or {}).get("content_rubric") or []
    if not rubric:
        rubric = DEFAULT_RUBRIC
    settings = get_settings()
    if not settings.anthropic_api_key:
        return _ungraded(rubric, "No Anthropic API key configured."), 0.0
    try:
        return _llm_grade(content, profile or {}, rubric, settings)
    except Exception as exc:
        # Defensive: never let grading crash a run. Surface the type so the
        # UI can show a friendly "ungraded: <reason>" line.
        return _ungraded(rubric, f"{type(exc).__name__}: {exc}"), 0.0


def _llm_grade(content: dict, profile: dict, rubric: list[dict],
               settings) -> tuple[dict, float]:
    """Ask Claude to score the draft. Returns the SAME shape as _ungraded so
    downstream code is uniform. Defensive parse: if the model returns
    something that doesn't match, fall back to ungraded with the raw text
    captured in `reason`."""
    import anthropic

    flat_text = "\n".join((b.get("text") or "") for b in content.get("blocks", []))
    criteria_lines = "\n".join(
        f"- {c['name']}: {c.get('description', '')}" for c in rubric)
    grounding = {
        "brand_voice": profile.get("brand_voice", ""),
        "value_prop": profile.get("value_prop", ""),
        "conversion_goal": profile.get("conversion_goal", ""),
        "icp": profile.get("icp") or {},
        "competitors": [c.get("name") for c in (profile.get("competitors") or [])
                        if isinstance(c, dict) and c.get("name")],
        "banned_claims": profile.get("banned_claims") or [],
    }
    prompt = (
        "Grade the marketing draft below against the org's rubric. Be honest "
        "and concrete — this is for an in-house team that needs signal, not "
        "applause. Return ONLY a JSON object with this exact shape:\n\n"
        "{\n"
        '  "status": "graded",\n'
        '  "overall": <int 0-100>,\n'
        '  "per_criterion": [{"name": "<criterion_name>", "score": <int 0-100>, '
        '"reason": "<one short sentence>"}, ...],\n'
        '  "suggestions": ["<concrete improvement>", "<another>"]\n'
        "}\n\n"
        f"CRITERIA:\n{criteria_lines}\n\n"
        f"PROFILE GROUNDING:\n{json.dumps(grounding, indent=2)}\n\n"
        f"DRAFT ({content.get('content_type', 'content')}):\n{flat_text}"
    )
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.anthropic_model, max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    parsed = _parse_json_envelope(raw)
    if parsed is None:
        return _ungraded(rubric, f"Grader returned non-JSON: {raw[:200]!r}"), 0.0
    if not isinstance(parsed, dict) or "overall" not in parsed:
        return _ungraded(rubric, f"Grader JSON missing fields: {raw[:200]!r}"), 0.0
    # Normalize: clamp overall to int 0-100, ensure list fields exist.
    try:
        overall = max(0, min(100, int(parsed.get("overall") or 0)))
    except (TypeError, ValueError):
        overall = 0
    grade = {
        "status": "graded",
        "overall": overall,
        "per_criterion": parsed.get("per_criterion") or [],
        "suggestions": parsed.get("suggestions") or [],
    }
    usage = getattr(msg, "usage", None)
    cost = 0.0
    if usage:
        cost = (usage.input_tokens * 3 + usage.output_tokens * 15) / 1_000_000
    return grade, round(cost, 6)
