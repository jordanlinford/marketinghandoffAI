"""
Persistent marketing memory — the substrate read-path.

This package is INFRASTRUCTURE for retrieval + deterministic weighting +
honest confidence + evidence-backed contextual injection. It is not AI
optimization and it is not machine learning. The deterministic core
(retrieval, weighting, pattern detection, confidence scoring) is pure
Python — the LLM only consumes the resulting context, it never computes
patterns.

Both the campaign planner and the content engine call the SAME shared
entry point (`query_memory`). No duplicated retrieval logic.

Memory is READ-ONLY over the existing telemetry tables (metric_points +
artifacts UTM columns + campaigns metadata). It writes nothing and
creates no new persistent tables. Tenant scoping is enforced via
scoped() at the only read site.

The governing sentence, never violated:

    "Here's what has historically worked for this audience/channel/
     content combination" — always with the evidence — NEVER "the AI
     has decided."
"""
from app.memory.query import query_memory, summarize_for_prompt  # noqa: F401
