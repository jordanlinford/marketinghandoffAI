# Document ingestion + extraction + candidate review — build brief (Phase 1, Build B)

Hand this to Claude Code. Read CLAUDE.md, docs/content-engine-brief.md,
docs/quality-loop-brief.md, docs/dashboard-brief.md, and docs/product-layer-brief.md first.

## North star
**"Upload a PMM framework and generate campaign content that actually sounds like the
product."** Not "successfully parsed documents." Parsing is the means; durable, structured,
operationalizable product intelligence is the end. The user-visible milestone for this build:
upload a real PMM doc to a product, accept the extracted insights, generate content scoped to
that product, see the draft reflect the framework's positioning / value props / proof points /
competitive frame.

## What this build IS
- Document ingestion (PDF, DOCX, TXT, MD, PPT/PPTX, images) → durable stored documents.
- Extraction pipeline → structured candidate insights with confidence + source passage.
- Candidate review queue → per-field accept / edit / reject.
- Promotion → accepted candidates populate ProductProfile fields via the existing additive merge
  pattern, with status-versioned history.
- A flexible `messaging_notes` field on the product for lighter-weight sections
  (objection handling, launch messaging) that aren't worth normalizing yet.
- A nullable `dimensions` JSON column on candidate + accepted records, designed for future
  variant scoping but NOT consulted by any logic in this build.

## What this build IS NOT (do NOT build)
- A messaging ontology / `MessagingVariant` entity / dimension-aware resolution.
- A multi-layer resolver v2 (org → product → segment → persona → ...). The existing org→product
  resolver from Build A stays unchanged.
- A generic RAG / vector-chat over uploaded documents. We extract durable structured fields, we
  do not build a chatbot over the corpus.
- Live document collaboration, comment threads, in-app editing of source documents.
- Auto-extraction without review (every extracted value goes through candidate review).
- Brand asset extraction (logos, colors). Future build.
- Touching Figma APIs or any third-party docs platform integrations.

## The data model

### `product_documents` (new table)
- `id`, `org_id` (FK, scoped), `product_id` (FK, scoped) — required
- `filename` (str), `mime_type` (str), `size_bytes` (int), `sha256` (str, for dedupe)
- `storage_path` (str) — where the raw doc lives on disk (under a tenant-scoped path)
- `kind` (enum: messaging_framework | one_pager | launch_doc | sales_enablement | other) —
  user-tagged at upload, drives extraction emphasis
- `version_label` (str, optional) — user-supplied (e.g. "v2.0", "Q3 2026 launch"); for their
  own organization, no semver inference
- `status` (enum: ingesting | extracted | failed | superseded) — `superseded` set when a newer
  doc of the same `kind` for the same product is uploaded; the doc + insights remain, but
  active extraction moves on
- `extracted_text` (text, nullable) — the normalized text we extracted from the raw doc
  (used for re-extraction without re-uploading)
- `extraction_error` (text, nullable) — populated if ingestion/extraction failed; surfaces in UI
- `created_at`, `updated_at`

### `extracted_insights` (new table — candidates)
One row per (field × document × extraction-run). The raw output of extraction.
- `id`, `org_id`, `product_id`, `product_document_id` (FK, scoped)
- `field_name` (str) — must be one of the canonical ProductProfile target fields, see below.
- `value` (JSON) — the extracted value (string for positioning, list for value_props, etc.)
- `confidence` (float, 0–1) — model-reported confidence; calibrated weakly, treat as a hint
- `source_passage` (text) — the exact passage from the document the value came from
- `source_location` (JSON, nullable) — e.g. `{page: 4}` for PDF, `{slide: 7}` for PPTX,
  `{paragraph: 12}` for DOCX. Best-effort, may be null for OCR.
- `dimensions` (JSON, nullable) — reserved for future variant scoping (persona/industry/segment/
  competitor). v1: always null. Build the column; do NOT branch on it.
- `status` (enum: pending | accepted | edited | rejected | superseded) — workflow state
- `accepted_value` (JSON, nullable) — populated when status=accepted/edited; if edited, this is
  the user's value, not the original
- `created_at`, `updated_at`

### `messaging_notes` (new JSON column on `product_profiles`)
- Shape: `{ "objection_handling": [ {note, source_passage?, source_doc_id?} ], "launch_messaging": [...], ... }`
- Searchable (text-search the values) but not deeply modeled. We'll learn the shape from use
  before we normalize. Promotion from candidates lands here for any "messaging note" extracted
  insight (see field list below).

### Status versioning on accepted product knowledge
When an extracted_insight is accepted and writes into a ProductProfile field, the *prior* value
of that field doesn't disappear:
- Add `field_history` (JSON column) on `product_profiles` — append-only log:
  `[{field, value, accepted_from_insight_id, accepted_at, status: active|superseded|historical}]`
- When a new value is accepted, the prior entry for that field flips active → superseded; the
  new entry is active. Nothing is deleted.
- This gives rollback ("re-activate the prior positioning"), audit ("what changed when"),
  and correlation hooks for later, with one column and zero new tables.

## The canonical target fields (extraction writes candidates against these)
These are the existing ProductProfile fields — extraction targets them directly:
- `positioning` (string)
- `target_persona` (JSON — role, seniority, pain points; same shape as Build A)
- `value_props` (list of strings)
- `proof_points` (list of strings / structured objects)
- `differentiators` (list of strings)
- `key_features` (list of strings)
- `use_cases` (list of strings)
- `product_competitors` (list of strings)

And two flexible/light-weight sections (write into `messaging_notes`, not normalized fields):
- `objection_handling` — list of {objection, response} pairs the doc surfaces
- `launch_messaging` — temporary launch-specific copy/themes from launch docs

**Do NOT extract into inheritable-override fields** (brand_voice_override, banned_claims_override,
conversion_goal_override, rubric_override). Extraction can surface them as *messaging notes the
user can manually consider*, but never as direct overrides — overriding inheritance is a
deliberate human decision, not an automatic one. (Same discipline as Build A's "extraction
fills product-only fields.")

## Pipeline (the ingestion → extraction flow)

### Stage 1 — ingest
Endpoint: `POST /api/products/{id}/documents` (scoped, multipart/form-data)
- Accept: pdf, docx, txt, md, ppt, pptx, png, jpg, jpeg, tiff
- Reject other types with a clear error.
- Persist raw file under `storage/{org_id}/{product_id}/{doc_id}_{filename}` (tenant-scoped path).
- Create `product_document` row, status=`ingesting`, kind from user-supplied form field
  (default `messaging_framework`).
- Enqueue an extraction run on the worker (see Stage 2). Return the document record immediately
  so the UI can poll.

### Stage 2 — text normalization (worker job)
For each MIME type, normalize to plain text + best-effort source-location anchors:
- **PDF**: `pypdf` (or `pdfminer.six` — pick the lighter dep) for text; per-page text retained
  for `source_location.page`. If a PDF returns near-empty text (scanned PDF), route to OCR.
- **DOCX**: `python-docx` for paragraph text; paragraph index → `source_location.paragraph`.
- **PPTX**: `python-pptx` for slide text (title + body of each slide); slide index →
  `source_location.slide`. Slide notes included.
- **TXT / MD**: read directly; for MD, light parsing to retain headings so extraction can use them.
- **Images (PNG/JPG/TIFF) and scanned PDFs**: OCR via `pytesseract` (Tesseract). NOTE: this is the
  noisiest path; surface lower confidence on OCR-derived candidates and tag
  `source_location.via=ocr`. If Tesseract is not installed, surface a clear runtime error
  instructing how to install (don't crash the worker; the doc lands with status=failed +
  extraction_error explaining what's missing).
Store the result on `product_document.extracted_text`.

### Stage 3 — extraction (LLM call, on the worker)
Single structured-output LLM call per document (cheap-by-default; chunk only if needed):
- Prompt the model with: the org profile + the current product profile (so it knows context) +
  the normalized doc text + the canonical target-field schema + the messaging-notes target list.
- Ask for a JSON object: per target field, a candidate `value` + `confidence` (0–1) + the
  `source_passage` (verbatim quote from the doc, ≤ ~30 words) that motivated the extraction.
  Allow the model to RETURN BLANK for any field the doc is silent on — this is critical. No
  confabulation. (Same discipline as the org-profile knowledge draft.)
- For docs longer than a single-shot fits, chunk by section/page and run extraction per chunk,
  then merge candidates by field (deduping near-identical values, preferring higher confidence).
- Reuse `synthesis.py`'s LLM fallback pattern: on no key / parse failure / API error, the
  document lands with status=`failed` and a clear `extraction_error` ("LLM unavailable"). Do
  NOT fabricate candidates without an LLM.
- Capture extraction cost on the run.

### Stage 4 — candidate persistence
- For each non-empty field in the LLM's structured output, create an `extracted_insight` row
  with status=`pending`.
- Flip the `product_document.status` to `extracted` (or `failed` with the error).
- Source passages are persisted verbatim (no paraphrasing) so the user can verify what the
  model read.

### Stage 5 — review queue
Endpoints (all scoped):
- `GET /api/products/{id}/insights?status=pending` — the queue for this product
- `GET /api/insights/{id}` — one insight
- `PATCH /api/insights/{id}` — accept | edit | reject. On accept/edit, the value promotes into
  the ProductProfile field via the additive merge (see Stage 6). On edit, the user's value
  wins, the source passage is retained for audit.
- Bulk accept: `POST /api/products/{id}/insights/bulk_accept` with a list of insight ids
  (for "accept all the obvious ones, review the rest" workflow).

### Stage 6 — promotion to ProductProfile (the additive merge)
When an insight is accepted/edited:
- Apply the same precedence pattern as the org-profile Setup additive merge:
  **manual edits > most-recently-accepted extracted > earlier-accepted extracted**.
- Single-value fields (positioning): the new accepted value becomes active; the prior active
  value flips to superseded in `field_history`.
- List-valued fields (value_props, proof_points, differentiators, key_features, use_cases,
  product_competitors): merge by deduping (case-insensitive); new items append; existing items
  retained. Removal is an explicit user action via PATCH to the product, not implicit.
- Messaging notes (`objection_handling`, `launch_messaging`): append into the corresponding
  list in `messaging_notes`, retaining the source passage + doc reference.
- Every promotion writes an entry into `field_history` with the source insight id, so audit/
  rollback works end-to-end.
- A subsequent upload that supersedes a doc does NOT auto-rewrite the product: the user must
  still accept the new candidates. Supersession is about the *document's* status, not silent
  re-extraction.

## Agents (no architectural changes — only inputs get richer)
- `content_engine` and `market_intel` continue to read `ctx.profile` (the ResolvedProfile from
  Build A). Because ProductProfile fields are now richly populated via accepted insights, the
  drafts will reflect real framework messaging without any agent code changes. This is the
  payoff — the existing engine quality jumps purely from better inputs.
- `messaging_notes` becomes a new input available on the resolved profile (just expose it as
  `resolved.messaging_notes`). The content engine should consult `objection_handling` notes when
  the user's content idea touches a competitor or pricing — append them to the prompt as
  "consider these objections the framework calls out." Lightweight, not a new architecture.

## Constraints (from CLAUDE.md)
- `scoped(Model, org_id)` for ALL reads/writes on every new table. Cross-org reads must fail
  closed in tests.
- Agents never read ProductDocument / ExtractedInsight directly — those are review-layer
  concerns. Agents read the resolved product profile, which is now richer thanks to promotions.
- Reuse the additive merge precedence from Setup; don't reinvent. Reuse `synthesis.py`'s LLM
  fallback pattern; do not fabricate without a key.
- Worker handles extraction as a queued job (uvicorn returns the document record immediately;
  extraction runs in the background — same async pattern as runs).
- Schema change. Mirror in sql/schema.sql with RLS for product_documents and
  extracted_insights; ensure the JSON additions on product_profiles are migrated.
- File storage: a tenant-scoped directory under `storage/{org_id}/{product_id}/`. Add it to
  .gitignore. Do not commit uploaded docs.
- New dependencies (add only what's needed): `pypdf` (or `pdfminer.six`), `python-docx`,
  `python-pptx`, `pytesseract`. Tesseract itself is a system dependency — document that in the
  README; the worker should surface a clear error if it's missing rather than crashing.
- On SQLite: delete agenthq.db + re-run scripts/seed.py; restart uvicorn AND worker. Smoke uses
  its own DB.
- Seed: leave one example accepted insight on the existing SimpleLegal CLM product so the dev
  DB demonstrates the promotion result, without requiring an actual doc upload to test.
- Don't touch the TS app or .replit's [deployment] block.

## Tests (hermetic — stub the LLM and the OCR)
Add the following to `scripts/smoke.py`. Keep them deterministic:
1. **Upload + ingest**: uploading a small TXT doc creates a product_document row; status flips
   from `ingesting` to `extracted`; a `extracted_text` is populated.
2. **Extraction stub**: with a stubbed LLM returning a fixed JSON, candidate insights persist
   with the correct field_name, value, confidence, and source_passage; blank fields in the LLM
   output produce NO candidate (no confabulation).
3. **Extraction failure**: with no LLM key, the document lands with status=`failed` and a clear
   extraction_error; no candidates are fabricated.
4. **Accept promotion**: accepting a single-value candidate writes into ProductProfile; prior
   value's entry in field_history flips to `superseded`; new entry is `active`.
5. **Accept promotion — list field**: accepting a value_props candidate appends without
   duplicating an existing value_prop (case-insensitive dedupe); rejected candidates do not
   promote.
6. **Messaging notes**: an objection_handling candidate, when accepted, appends into
   `messaging_notes.objection_handling`, retaining the source passage.
7. **No-override discipline**: extraction MUST NOT produce candidates for any
   `*_override` field; assert no insight ever has a field_name from the override list.
8. **Tenant isolation**: Acme cannot list/read/accept Onit's documents or insights via the API
   (403); Acme's product cannot be the target of an Onit insight.
9. **Dimensions reserved**: the `dimensions` column is null on every candidate produced in v1
   (no logic depending on it; confirms we built the column without branching on it).
10. **Content agent uses promoted knowledge**: with an accepted positioning + value_props on a
    product, a content_engine run for that product reflects them in the draft (string match on
    a unique fragment of the seeded positioning). This is the north-star test in mechanical form.

## UI (minimal — guts only, polish deferred to Replit)
Inside the Setup → Products → (product) view:
- **Documents** section: upload control (accepts the file types above), kind dropdown,
  optional version_label, an Upload button. List existing docs with status, kind, version,
  uploaded_at, and a Re-extract button (re-runs Stage 3 against the stored extracted_text
  without re-uploading the file).
- **Review queue**: a list of pending candidates grouped by field, each showing the extracted
  value, confidence, the source passage in italics, and Accept / Edit / Reject controls. Bulk
  "Accept all in this field" button. After accepting/rejecting, the candidate disappears from
  pending and the field's current value updates inline.
- **Field history**: a small "history" toggle next to each canonical field showing the prior
  values from `field_history` with their accepted_at timestamps and a "restore" button on
  superseded entries. (Cheap to implement on top of the column.)
- No styling investment. Functional. Replit will handle polish.

## Done looks like
- A user uploads a PMM framework PDF (or DOCX/MD/PPTX/image) against a product. Within a few
  seconds the document moves to `extracted`. A pending review queue appears with candidate
  values, each carrying confidence + source passage.
- The user accepts the obvious candidates (bulk), edits one or two, rejects the noise. The
  ProductProfile fields populate via the additive merge; the prior values (if any) are kept
  in field_history as superseded.
- The user generates content scoped to that product. The draft sounds like the framework —
  positioning, value props, and competitive frame from the doc come through; brand voice from
  the org inherits unchanged. **This is the north-star demonstration.**
- Smoke green with the ten new tests (LLM + OCR stubbed); pushed; uvicorn + worker restarted.
- No new public-facing UI polish; no ontology added; the dimensions column is in place but
  unused by any logic.

## Explicitly deferred (signposted for later)
- `MessagingVariant` + dimension-aware resolution (the variant model from the architecture
  discussion). Reserved by the `dimensions` JSON columns; not implemented.
- A multi-layer resolver v2 (org → product → segment → persona → campaign). The Build A
  resolver stays unchanged; v2 comes when variants come.
- Normalization of `objection_handling` / `launch_messaging` into structured tables. They live
  in `messaging_notes` (JSON) until usage patterns earn the normalization.
- Brand asset extraction (logos, colors).
- Live integrations with Figma / Notion / Google Docs.
- Per-doc collaborative review workflows; the v1 review queue is single-user.
