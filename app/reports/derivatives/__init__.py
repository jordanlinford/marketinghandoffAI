"""
Level-4 derivatives — short, channel-shaped re-expressions of Level-3
anchors. THIS PACKAGE IS TEXT-ONLY in this build. Visual derivatives
(carousel slide images, ad creatives) land on top of this once §7
containment proves enforceable on the text path.

Two surfaces co-live here:
  * `containment` — the §7 validator + findings classifier (load-
    bearing; mirrors the anchor's validate_evidence_binding shape).
  * Per-derivative renderers (exec_summary today) — read a source
    anchor artifact, emit content with INHERITED ⟦ev:id⟧ markers.

DERIVATIVE_RENDERERS is the dispatch table the derivative_composer
agent reads, mirroring RENDERERS for anchors. New derivative types
(linkedin_post, blog_excerpt, ...) register here.
"""
from app.reports.derivatives.containment import (  # noqa: F401
    derive_containment_findings,
    trust_checks_with_containment_findings,
    validate_containment,
)
from app.reports.derivatives.exec_summary import render_exec_summary  # noqa: F401


DERIVATIVE_RENDERERS = {
    "exec_summary": render_exec_summary,
}
