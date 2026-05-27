"""
Live (real-LLM) no-echo verification.

Hermetic smoke catches prompt-structure regressions and template-fallback
leaks. It does NOT catch the BEHAVIOR of the real Anthropic model — that's
the gap that let the previous fix ship while the bug remained live.

This script makes ONE real Anthropic call against a SimpleLegal-CLM-like
product profile (with a sentinel inside the brand_voice) and prints the
generated blocks for human inspection. It also asserts the obvious leak
patterns are absent. Costs a few cents per run.

Usage:
    .venv/bin/python -m scripts.verify_no_echo_live

Requirements:
    * ANTHROPIC_API_KEY in the environment (or in .env).
    * Network access.

Exit codes:
    0 — no echo detected; output looks clean.
    1 — at least one assertion fired (echo or parenthesized list).
"""
from __future__ import annotations

import json
import os
import re
import sys


def main() -> int:
    # Build a self-contained profile with the marker — no DB required.
    # Mirrors what the resolver would hand the agent for SimpleLegal CLM.
    TONE_MARKER = "TONE_MARKER_DO_NOT_ECHO_xyzzy42"
    profile = {
        "product_summary": "Onit — legal-ops software for in-house teams.",
        "value_prop": "Cut hours of manual work from matter management.",
        "brand_voice": "Direct, confident, practitioner-first. No legal-tech "
                       "jargon. Speak to General Counsel and legal ops "
                       "leads as peers. " + TONE_MARKER,
        "banned_claims": ["#1 in the world"],
        "icp": {"titles": ["General Counsel", "Head of Legal Ops"]},
        "competitors": [{"name": "SimpleLegal"}],
        "conversion_goal": "book a demo",
        "product": {
            "id": "test", "name": "SimpleLegal CLM",
            "slug": "simplelegal-clm",
            "positioning": "Contract lifecycle management built for in-house "
                           "teams who live in matters, not in legal-tech "
                           "jargon.",
            "value_props": ["One source of truth for contracts and approvals.",
                            "Cuts contract turnaround time in half within 90 days."],
            "product_competitors": [{"name": "Ironclad"},
                                    {"name": "Agiloft"},
                                    {"name": "BrightFlag"},
                                    {"name": "ContractWorks"}],
        },
    }

    # Import after env is loaded so the API key is read.
    from app.agents.content_templates import build as build_content
    from app.config import get_settings

    if not get_settings().anthropic_api_key:
        print("ERROR: ANTHROPIC_API_KEY not set. Cannot run live verification.")
        return 1

    print(f"Live model: {get_settings().anthropic_model}")
    print("Making one real Anthropic call (will cost ~$0.01–0.03)...\n")

    content, cost = build_content(
        "email", profile, brief=None,
        topic="Cut matter-management overhead", target="GC", critique="")

    print(f"--- Generated email (cost ${cost:.4f}) ---")
    for block in content.get("blocks", []):
        print(f"\n[{block.get('kind')}]")
        print(block.get("text", ""))
    print(f"\nMetadata: {json.dumps(content.get('metadata', {}), indent=2)}")
    print("-" * 60)

    joined = " ".join(b.get("text", "") for b in content.get("blocks", []))
    failures: list[str] = []

    # 1. The sentinel must not appear anywhere.
    if TONE_MARKER in joined:
        failures.append(
            f"BREACH: TONE_MARKER ({TONE_MARKER!r}) leaked into body text.")

    # 2. No "Tone:" or similar instruction labels.
    for label in ("Tone:", "Voice:", "Style:", "brand_voice", "value_prop:",
                  "banned_claims", "positioning:"):
        if label in joined:
            failures.append(
                f"BREACH: instruction label {label!r} appears in body text.")

    # 3. No parenthesized comma-separated competitor list — the specific
    #    pattern the user flagged: "(Ironclad, Agiloft, BrightFlag, ContractWorks)".
    if re.search(r"\([^)]*,[^)]*,[^)]*\)", joined):
        failures.append(
            "BREACH: parenthesized comma-list of 3+ items found in body "
            "(matches the competitor-list leak pattern).")
    if re.search(r"\(vs\.\s*[^)]*,[^)]*\)", joined, re.IGNORECASE):
        failures.append("BREACH: '(vs. X, Y, ...)' list pattern in body.")

    # 4. No banned-claim phrase.
    for banned in profile.get("banned_claims") or []:
        if banned.lower() in joined.lower():
            failures.append(f"BREACH: banned phrase {banned!r} appears in body.")

    if failures:
        print("\nFAILED — live model still echoes / leaks:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nPASS — live model output is clean:")
    print("  • TONE_MARKER not present")
    print("  • No 'Tone:' / 'Voice:' / 'brand_voice' / etc. labels")
    print("  • No parenthesized competitor list")
    print("  • No banned phrases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
