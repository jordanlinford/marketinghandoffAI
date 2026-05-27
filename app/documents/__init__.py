"""
Document ingestion + extraction pipeline.

Three responsibilities live in this package:
  * storage.py    — tenant-scoped on-disk paths for raw uploads.
  * normalize.py  — per-MIME text extraction (PDF/DOCX/PPTX/TXT/MD/image).
  * extract.py    — LLM call that produces candidate insights.
  * promote.py    — additive merge of accepted insights into ProductProfile.

The pipeline is async: API upload returns immediately, the worker normalizes
and extracts on a queued job. Agents NEVER read from this package directly —
they consume the resolved profile, which is richer thanks to accepted
promotions. See docs/document-ingestion-brief.md.
"""
