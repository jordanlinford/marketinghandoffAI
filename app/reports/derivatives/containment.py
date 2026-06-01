"""
§7 Containment Validator — the deterministic enforcement layer for
derivative assets.

THE LOAD-BEARING PRINCIPLE (§7 Derivation Discipline, from
docs/cross-layer-disciplines.md): a derivative may select, condense,
reorder, reframe, or reformat claims present in its source anchor —
NEVER introduce a statistic, outcome, or assertion absent from it.
Containment is the check that makes that real.

  - Every marker ⟦ev:id⟧ in the derivative resolves to an entry in
    the SOURCE ANCHOR's evidence ledger. A marker citing an id that
    isn't in that anchor is a §7 critical (the derivative invented or
    mis-cited a source).
  - Every number-shaped token in the derivative is bound by a
    resolving marker. A bare number with no marker is a §7 critical
    (the derivative either invented a number or failed to inherit
    its source's binding).

Why §7 makes §6 transitive (the elegant consequence): the source
anchor already passed §6 — every quantitative claim in it ties to a
ledger entry built from real intelligence. A derivative that passes
containment can ONLY contain claims that map to those already-bound
anchor claims. So the derivative inherits §6 by construction; no
re-binding against raw sources is needed, only the containment check.

This module reuses the existing block-aware scanner from
`validate_evidence_binding` — the (markers, numbers, proximity)
detection layer is the same machinery. Only the SEMANTIC
interpretation of the failure modes is different: a §6 "unsourced
number" in an anchor becomes a §7 "number absent from source" in a
derivative. The findings classifier below maps these to discipline=§7.

Future tightening (BACKLOG, NOT this build): value-equality
containment — assert the bound marker's ledger entry value MATCHES
the number-shaped token's value (today, "5%⟦ev:3⟧" is accepted even
if entry 3 is 5,000 conversions). This catches re-expression drift.
Out of scope here because formatting variance is genuine ("5.0%" vs
"5%", "1,200" vs "1200") and v1 already catches the two largest
failure modes the brief calls out.
"""
from __future__ import annotations

from app.reports.evidence import (Ledger, _block_label,
                                   validate_evidence_binding)


def validate_containment(derivative_content: dict,
                         source_anchor_ledger: Ledger) -> dict:
    """Return a trust_checks dict for a derivative checked against its
    source anchor's ledger. Shape is IDENTICAL to
    `validate_evidence_binding`'s output — same fields, same per-block
    inventory, same `passed` semantics — so the trust_view-model and
    every downstream surface read it without branching.

    The semantic flip: `markers_unresolved` here means "marker cites
    an id not in the source anchor's ledger" (§7 containment breach),
    not "the renderer minted a fake id" (§6 marker fabrication). The
    findings classifier in this module assigns the correct discipline.
    """
    base = validate_evidence_binding(derivative_content, source_anchor_ledger)
    # Marker the downstream consumer can read off to distinguish a
    # containment-check trust_checks from an evidence-binding one.
    # The shape is the same; the SEMANTICS of failure differ (§7 vs §6).
    base["validator"] = "containment"
    return base


def derive_containment_findings(trust_checks: dict,
                                source_anchor_ledger_entries: list[dict],
                                derivative_content: dict) -> list[dict]:
    """Classify containment-validator trust_checks into §7 findings.
    Pure deterministic — mirrors `derive_findings` but every fired
    finding is discipline=§7 critical (containment is a binary check;
    there is no warning tier here — either the claim is in source or
    it isn't).

    Same finding shape as the anchor classifier so the existing
    severity ladder, trust_view, and gate path consume it without a
    new branch. The `recommended_action` text is rewritten to fit the
    derivative context (cite a source-anchor id, or remove the claim).
    """
    findings: list[dict] = []
    blocks = (derivative_content or {}).get("blocks") or []

    # ---- CRITICAL §7 — number in derivative absent from source ----
    # The number-shaped token in the derivative has no resolving
    # marker in proximity. Either the derivative invented a number
    # or it failed to inherit its source's marker — both are
    # §7 containment breaches (the derivative is claiming something
    # the anchor doesn't say).
    for u in (trust_checks or {}).get("numbers_unbound") or []:
        if not isinstance(u, dict):
            continue
        findings.append({
            "severity": "critical",
            "discipline": "§7",
            "claim": str(u.get("text", "")),
            "location": u.get("block_label")
                        or _block_label(blocks, u.get("block_idx", -1)),
            "block_idx": u.get("block_idx"),
            "issue": ("Derivative introduces a number absent from "
                      "the source anchor's evidence ledger."),
            "recommended_action": ("Remove the number, or cite a "
                                    "ledger id from the source anchor "
                                    "that contains this value. "
                                    "Derivatives may not introduce "
                                    "claims the anchor didn't already "
                                    "make."),
        })

    # ---- CRITICAL §7 — marker cites an id not in the source -----
    # The marker resolves syntactically but the id doesn't exist in
    # THIS anchor's ledger. Either the derivative cited the wrong
    # anchor's ids or fabricated an id entirely — both are §7.
    for m in (trust_checks or {}).get("markers_unresolved") or []:
        if isinstance(m, dict):
            ev_id = m.get("id", "")
            location = (m.get("block_label")
                        or _block_label(blocks, m.get("block_idx", -1)))
            block_idx_finding = m.get("block_idx")
        else:
            ev_id = str(m)
            location = "Unknown"
            block_idx_finding = None
        findings.append({
            "severity": "critical",
            "discipline": "§7",
            "claim": f"⟦ev:{ev_id}⟧",
            "location": location,
            "block_idx": block_idx_finding,
            "issue": ("Marker cites an evidence-ledger id that does "
                      "not exist in the source anchor's ledger. "
                      "Derivatives may only cite ids the anchor "
                      "already established."),
            "recommended_action": ("Cite a ledger id present in the "
                                    "source anchor's evidence ledger, "
                                    "or remove the marker and its "
                                    "claim from the derivative."),
        })

    return findings


def trust_checks_with_containment_findings(
        trust_checks: dict,
        source_anchor_ledger_entries: list[dict],
        derivative_content: dict) -> dict:
    """Decorate a containment-validator trust_checks dict with derived
    findings + approval_blocked, mutating in place and returning it.
    Same role as `trust_checks_with_findings` (anchor path) — single
    output shape for the gate + trust_view to consume."""
    findings = derive_containment_findings(
        trust_checks, source_anchor_ledger_entries, derivative_content)
    critical = [f for f in findings if f["severity"] == "critical"]
    trust_checks["findings"] = findings
    # Containment has no warning or informational tier — the check is
    # binary. Keep the same key shape so the trust_view's "0 warnings,
    # 0 informational" rendering still works without branching.
    trust_checks["findings_by_severity"] = {
        "critical": len(critical),
        "warning": 0,
        "informational": 0,
    }
    trust_checks["approval_blocked"] = bool(critical)
    return trust_checks
