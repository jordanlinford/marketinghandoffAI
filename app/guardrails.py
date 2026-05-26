"""
Guardrail evaluation. Every ProposedAction with a guardrail_scope is checked
against the org's rules BEFORE it can be executed. "passed" means it may be
auto-approved (within bands); "blocked" means it must escalate to a human.
"""
from __future__ import annotations

from app.schemas import ProposedAction


def evaluate(action: ProposedAction, rules_by_scope: dict[str, dict]) -> tuple[str, str]:
    """Return (status, detail) where status is 'passed' | 'blocked' | 'pending'."""
    scope = action.guardrail_scope
    if scope is None:
        return "passed", "No guardrail scope; informational action."

    rules = rules_by_scope.get(scope)
    if not rules:
        return "blocked", f"No '{scope}' guardrails configured — escalating to human."

    if scope == "spend":
        amount = float(action.payload.get("amount_usd", 0))
        cap = float(rules.get("max_change_usd", 0))
        if amount <= cap:
            return "passed", f"${amount:,.0f} within auto-approve cap ${cap:,.0f}."
        return "blocked", f"${amount:,.0f} exceeds cap ${cap:,.0f} — needs approval."

    if scope == "publish":
        if rules.get("require_human_review", True):
            return "blocked", "Publish requires human review by policy."
        return "passed", "Auto-publish enabled for this surface."

    if scope == "content":
        # Content-draft review. Trips if any of the org's banned_claims phrases
        # appears in the assembled text. The worker merges OrgProfile.banned_claims
        # into this scope's rules — the profile stays the single source of truth.
        text = (action.payload.get("text") or "").lower()
        banned = [str(c).strip().lower() for c in (rules.get("banned_claims") or [])
                  if str(c).strip()]
        hits = [c for c in banned if c and c in text]
        if hits:
            return "blocked", f"Banned-claim trip: {', '.join(hits)}"
        return "passed", "Draft contains no banned-claim phrases."

    return "blocked", f"Unknown scope '{scope}' — escalating."
