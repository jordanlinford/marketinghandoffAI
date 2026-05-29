"""
build_trust_view — the presentation rollup for trust visibility surfaces.

LOAD-BEARING DISCIPLINE (the §6 Principle in renderer form):
This function is PURE. It rolls up already-computed trust_checks +
evidence_ledger + block inventory for display. It makes NO trust
decision, runs NO validation, invents NO finding, computes NO score
the data doesn't already contain. If the UI's notion of "blocked"
ever disagrees with trust_checks.approval_blocked, the rollup is
wrong. The single source of truth is the stored data; this is just
the shape the UI needs to render it.

Inputs:
  trust_checks       — the dict computed by validate_evidence_binding
                       + trust_checks_with_findings (carries
                       approval_blocked, findings, blocks inventory,
                       counts).
  evidence_ledger    — list of ledger entries (already serialized via
                       Ledger.to_list() at agent time).
  blocks             — content.blocks list (the rendered prose blocks).
                       Provides text content for the UI; the view-model
                       reads kind / label / idx from the trust_checks
                       blocks inventory, not from this list, so the two
                       can never disagree on structure.

Output: a render model dict the UI consumes verbatim. The fields are:
  state              — "blocked" | "passed_with_warnings" | "passed"
  state_label        — human-readable label for the banner
  state_reason       — one-line plain-English reason
  coverage           — markers + numbers + ledger counts (pass-through)
  findings_by_severity — {critical, warning, informational} → list[dict]
  ledger             — list of evidence entries (pass-through)
  block_indicators   — list of {block_idx, kind, label, indicator}
  trust_score        — ONLY present when trust_checks already has it
                       (we never synthesize a score)
"""
from __future__ import annotations

import re
from typing import Any


# Block-indicator precedence — FIXED, deterministic, no decisions:
#   blocked        > warning > low-confidence > evidence-backed > no-evidence
# The function below evaluates in that order and the FIRST condition
# that matches wins. Lower-tier conditions never override higher-tier
# ones; that's the invariant the smoke test pins.
_BELOW_THRESHOLD = ("low", "insufficient")

# Same marker shape the evidence module uses — kept here for the
# on-read fallback derivation. The fallback is presentation data
# shaping (NOT new validation): when trust_checks.blocks[i].
# resolved_marker_ids is absent (older reports written before this
# field landed in the inventory), we scan the corresponding block's
# text using the same regex and look up matches in the ledger. This
# is the SAME lookup the validator does — it does not make a new
# trust decision; it just routes existing data to the indicator.
_VIEW_MARKER_RE = re.compile(r"⟦ev:([A-Za-z0-9_\-]+)⟧")


def _block_indicator(inv: dict, findings_by_block_idx: dict[int, list[dict]],
                     ledger_by_id: dict[str, dict],
                     block_text: str = "") -> str:
    """Return the indicator for one block, following the fixed
    precedence above. Pure: only reads stored data.

    When the inventory entry lacks `resolved_marker_ids` (older
    reports persisted before the field landed), we fall back to
    scanning `block_text` for markers and resolving against the
    ledger. Same lookup the validator does — no new trust decision.
    """
    idx = inv.get("idx")
    block_findings = findings_by_block_idx.get(idx, [])
    if any(f.get("severity") == "critical" for f in block_findings):
        return "blocked"
    if any(f.get("severity") == "warning" for f in block_findings):
        return "warning"

    resolved_ids: list[str] = list(inv.get("resolved_marker_ids") or [])
    if not resolved_ids and block_text:
        # On-read fallback: scan the block's text for ⟦ev:N⟧ tokens.
        # We treat a marker as "resolving" iff the id exists in the
        # ledger — same condition the validator used.
        for match in _VIEW_MARKER_RE.finditer(block_text):
            mid = match.group(1)
            if mid in ledger_by_id:
                resolved_ids.append(mid)

    resolved_entries = [ledger_by_id[m] for m in resolved_ids
                        if m in ledger_by_id]
    if resolved_entries:
        confidences = {(e.get("confidence") or "n_a")
                       for e in resolved_entries}
        if confidences.issubset(set(_BELOW_THRESHOLD)):
            return "low-confidence"
        return "evidence-backed"
    return "no-evidence"


def build_trust_view(trust_checks: dict | None,
                     evidence_ledger: list[dict] | None,
                     blocks: list[dict] | None) -> dict:
    """Pure deterministic rollup of trust_checks for UI surfaces.

    NEVER recomputes pass/warn/block. NEVER invents a finding. NEVER
    synthesizes a trust score. Output is a function of input only.
    """
    tc = trust_checks or {}
    ledger = list(evidence_ledger or [])
    block_inventory: list[dict] = list(tc.get("blocks") or [])

    # On-read fallback for older reports that pre-date the block-aware
    # inventory (added in v2.1). Build a minimal inventory from the
    # rendered blocks parameter so block indicators still render. This
    # is presentation data shaping: we surface kind + label per block
    # so the UI can render indicators, NOT a new trust decision. The
    # _block_indicator helper does the actual marker-scan fallback.
    if not block_inventory and blocks:
        for i, b in enumerate(blocks):
            block_inventory.append({
                "idx": i,
                "kind": (b or {}).get("kind") or "",
                "label": (b or {}).get("kind") or "Block",
                "markers": 0,
                "numbers": 0,
                # resolved_marker_ids deliberately omitted — the
                # indicator helper will scan block text for them.
            })

    findings: list[dict] = list(tc.get("findings") or [])
    has_warning = any(f.get("severity") == "warning" for f in findings)
    has_informational = any(
        f.get("severity") == "informational" for f in findings)
    blocked = bool(tc.get("approval_blocked"))

    # ---- State derivation — the single-source rule ---------------------
    # blocked is the ONLY upstream input that flips state to "blocked".
    # We deliberately do NOT recompute from numbers_unbound /
    # markers_unresolved — those drive findings, findings drive
    # approval_blocked, and approval_blocked drives state. One arrow.
    critical_count = sum(1 for f in findings
                         if f.get("severity") == "critical")
    warning_count = sum(1 for f in findings
                        if f.get("severity") == "warning")
    informational_count = sum(1 for f in findings
                              if f.get("severity") == "informational")
    if blocked:
        state = "blocked"
        state_label = "Approval Blocked"
        state_reason = (f"{critical_count} critical evidence finding"
                        f"{'s' if critical_count != 1 else ''} — "
                        "approval blocked by the trust layer until "
                        "reviewed.")
    elif has_warning:
        state = "passed_with_warnings"
        state_label = "Passed with Warnings"
        state_reason = (f"{warning_count} thin-evidence warning"
                        f"{'s' if warning_count != 1 else ''} — checks "
                        "passed; approval permitted with caveats.")
    else:
        state = "passed"
        state_label = "Checks Passed"
        state_reason = ("All quantitative claims bind to evidence; no "
                        "critical or warning findings.")

    # ---- Findings grouped by severity — pass-through verbatim ----------
    by_sev: dict[str, list[dict]] = {
        "critical": [], "warning": [], "informational": []}
    for f in findings:
        sev = f.get("severity")
        if sev in by_sev:
            by_sev[sev].append(dict(f))  # shallow copy — safe to share

    # ---- Block indicators by FIXED precedence -------------------------
    ledger_by_id = {e["id"]: e for e in ledger
                    if isinstance(e, dict) and "id" in e}
    findings_by_block_idx: dict[int, list[dict]] = {}
    for f in findings:
        idx = f.get("block_idx")
        if idx is None:
            continue
        findings_by_block_idx.setdefault(idx, []).append(f)
    # Map content blocks by idx for the on-read fallback (used when
    # the inventory entry lacks resolved_marker_ids).
    block_text_by_idx = {i: ((b or {}).get("text") or "")
                          for i, b in enumerate(blocks or [])}
    block_indicators: list[dict] = []
    for inv in block_inventory:
        idx = inv.get("idx")
        block_indicators.append({
            "block_idx": idx,
            "kind": inv.get("kind") or "",
            "label": inv.get("label") or "",
            "indicator": _block_indicator(
                inv, findings_by_block_idx, ledger_by_id,
                block_text=block_text_by_idx.get(idx, "")),
        })

    # ---- Coverage — pass-through counts only --------------------------
    coverage = {
        "markers_found": tc.get("markers_found", 0),
        "markers_resolved": tc.get("markers_resolved", 0),
        "numbers_found": tc.get("numbers_found", 0),
        "numbers_bound": tc.get("numbers_bound", 0),
        "ledger_size": tc.get("ledger_size", 0),
    }

    view = {
        "state": state,
        "state_label": state_label,
        "state_reason": state_reason,
        "coverage": coverage,
        "findings_by_severity": by_sev,
        "findings_counts": {
            "critical": critical_count,
            "warning": warning_count,
            "informational": informational_count,
        },
        "ledger": ledger,
        "block_indicators": block_indicators,
    }
    # trust_score — passed through ONLY if the stored data already
    # carries it. We never synthesize one (the §6 Principle: don't
    # invent values to populate a surface).
    if "trust_score" in tc:
        view["trust_score"] = tc.get("trust_score")
    return view


def compact_trust_state(trust_checks: dict | None) -> str | None:
    """Tiny helper for the Library list trust pill. Returns the same
    `state` string the full view-model derives — same single source —
    or None when the artifact has no trust_checks attached (non-report,
    or a legacy report from before the trust layer).

    Mirrors `build_trust_view`'s state derivation EXACTLY:
      blocked iff approval_blocked
      passed_with_warnings iff any warning finding
      passed otherwise
    """
    if not trust_checks:
        return None
    if bool(trust_checks.get("approval_blocked")):
        return "blocked"
    findings = trust_checks.get("findings") or []
    if any(f.get("severity") == "warning" for f in findings):
        return "passed_with_warnings"
    return "passed"
