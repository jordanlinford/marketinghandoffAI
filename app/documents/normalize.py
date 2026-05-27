"""
Per-MIME text normalization. Each extractor returns:

  {"text": "<normalized text>", "locations": [ {anchor_key: value, ...}, ... ]}

`locations` is a list of structured anchors the worker stamps onto candidate
insights' source_location field. For PDF/PPTX/DOCX this maps to page / slide
/ paragraph indices; for OCR-derived text it's tagged via=ocr.

All extractors:
  * Lazy-import their underlying lib so a missing dep only breaks that
    MIME path, not the whole worker.
  * Never raise on a bad/empty file — return ("", []) or a clear error
    message. The worker translates failures into product_document.status
    = "failed" + extraction_error.

OCR (images + scanned PDFs) is the noisiest path and requires the system
Tesseract binary. We surface a friendly error if it's missing rather than
crashing — see _ocr_image / _ocr_bytes.
"""
from __future__ import annotations

import io
from typing import Callable


# Public sentinel for "couldn't normalize" — the worker reads this to set
# extraction_error WITHOUT marking the doc successful.
class NormalizationError(RuntimeError):
    """Raised when a recognized MIME type's extractor cannot produce text.
    Always carries a human-readable, actionable message."""


# Friendly error if tesseract is missing — surfaces the install command
# rather than the upstream TesseractNotFoundError ImportError noise.
_TESSERACT_MISSING_MSG = (
    "Tesseract OCR is not installed on this system. Install it before "
    "uploading images or scanned PDFs. On macOS: `brew install tesseract`. "
    "On Debian/Ubuntu: `sudo apt-get install tesseract-ocr`.")


_PDF_EMPTY_THRESHOLD = 40   # chars of native PDF text below which we OCR


def normalize(content: bytes, filename: str, mime_type: str) -> dict:
    """Dispatch to the right extractor. Returns:
       {"text": str, "locations": list[dict], "ocr_used": bool}
    Raises NormalizationError when this MIME path can't produce text."""
    mt = (mime_type or "").lower()
    name = (filename or "").lower()
    if mt.startswith("text/markdown") or name.endswith(".md"):
        return _from_text(content, kind="md")
    if mt.startswith("text/") or name.endswith((".txt",)):
        return _from_text(content, kind="txt")
    if mt == "application/pdf" or name.endswith(".pdf"):
        return _from_pdf(content)
    if mt in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              "application/msword") or name.endswith(".docx"):
        return _from_docx(content)
    if (mt in ("application/vnd.openxmlformats-officedocument.presentationml.presentation",
               "application/vnd.ms-powerpoint")
            or name.endswith((".pptx", ".ppt"))):
        return _from_pptx(content)
    if mt.startswith("image/") or name.endswith((".png", ".jpg", ".jpeg", ".tiff")):
        return _from_image(content)
    raise NormalizationError(
        f"Unsupported MIME type {mime_type!r} (filename={filename!r}). "
        f"Accepted: pdf, docx, pptx, txt, md, png, jpg, jpeg, tiff.")


# ---- Plain text (TXT / MD) -----------------------------------------------
def _from_text(content: bytes, *, kind: str) -> dict:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Fall back to latin-1 — strictly lossless for bytes ≤ 0xFF, so we
        # never crash on a non-UTF8 export, and downstream LLM gets readable
        # text. We don't claim source-location anchors for plain text.
        text = content.decode("latin-1", errors="replace")
    # Markdown gets a tiny pre-pass that retains heading lines verbatim,
    # so the extractor can see "## Positioning" as a structural cue.
    if kind == "md":
        text = text.strip()
    return {"text": text.strip(), "locations": [], "ocr_used": False}


# ---- PDF ------------------------------------------------------------------
def _from_pdf(content: bytes) -> dict:
    try:
        import pypdf
    except ImportError as exc:
        raise NormalizationError(f"pypdf not installed: {exc}") from exc
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
    except Exception as exc:
        raise NormalizationError(f"Could not parse PDF: {exc}") from exc
    pages: list[str] = []
    locations: list[dict] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        pages.append(t)
        if t.strip():
            locations.append({"page": i, "chars": len(t)})
    text = "\n\n".join(pages).strip()
    # Scanned PDFs return near-empty native text — route to OCR.
    if len(text) < _PDF_EMPTY_THRESHOLD:
        return _ocr_bytes(content, hint="scanned-pdf")
    return {"text": text, "locations": locations, "ocr_used": False}


# ---- DOCX -----------------------------------------------------------------
def _from_docx(content: bytes) -> dict:
    try:
        from docx import Document
    except ImportError as exc:
        raise NormalizationError(f"python-docx not installed: {exc}") from exc
    try:
        doc = Document(io.BytesIO(content))
    except Exception as exc:
        raise NormalizationError(f"Could not parse DOCX: {exc}") from exc
    paragraphs: list[str] = []
    locations: list[dict] = []
    for idx, para in enumerate(doc.paragraphs):
        t = (para.text or "").strip()
        if not t:
            continue
        paragraphs.append(t)
        locations.append({"paragraph": idx, "chars": len(t)})
    return {"text": "\n".join(paragraphs).strip(),
            "locations": locations, "ocr_used": False}


# ---- PPTX -----------------------------------------------------------------
def _from_pptx(content: bytes) -> dict:
    try:
        from pptx import Presentation
    except ImportError as exc:
        raise NormalizationError(f"python-pptx not installed: {exc}") from exc
    try:
        pres = Presentation(io.BytesIO(content))
    except Exception as exc:
        raise NormalizationError(f"Could not parse PPTX: {exc}") from exc
    chunks: list[str] = []
    locations: list[dict] = []
    for i, slide in enumerate(pres.slides, start=1):
        slide_text: list[str] = []
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            for para in shape.text_frame.paragraphs:
                t = "".join(run.text or "" for run in para.runs).strip()
                if t:
                    slide_text.append(t)
        # Slide notes — frequently where the real messaging lives.
        notes = ""
        try:
            if slide.has_notes_slide and slide.notes_slide is not None:
                notes_frame = slide.notes_slide.notes_text_frame
                if notes_frame is not None:
                    notes = (notes_frame.text or "").strip()
        except Exception:
            notes = ""
        body = "\n".join(slide_text)
        if notes:
            body = f"{body}\n[Notes] {notes}" if body else f"[Notes] {notes}"
        if body:
            chunks.append(f"[Slide {i}]\n{body}")
            locations.append({"slide": i, "chars": len(body)})
    return {"text": "\n\n".join(chunks).strip(),
            "locations": locations, "ocr_used": False}


# ---- Images + scanned PDFs (OCR via Tesseract) ---------------------------
def _from_image(content: bytes) -> dict:
    return _ocr_bytes(content, hint="image")


def _ocr_bytes(content: bytes, *, hint: str) -> dict:
    """OCR a byte blob. Raises NormalizationError with a friendly install
    hint when Tesseract is missing — the worker translates that into a
    failed-doc state rather than a worker crash."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise NormalizationError(
            f"OCR dependency missing ({exc}). Install pytesseract + Pillow."
        ) from exc
    try:
        # For "scanned-pdf" hint we still try Pillow open; pdf2image isn't
        # listed as a dep so we don't support multi-page rasterization in
        # v1. Single-image upload is the primary OCR path. If a "scanned"
        # PDF lands here we'll try once and either succeed or surface a
        # clear error.
        image = Image.open(io.BytesIO(content))
    except Exception as exc:
        if hint == "scanned-pdf":
            raise NormalizationError(
                "PDF has no extractable text and OCR of scanned multi-page "
                "PDFs isn't supported in v1. Convert pages to images and "
                "upload them individually, or run OCR locally first.") from exc
        raise NormalizationError(f"Could not open image for OCR: {exc}") from exc
    try:
        text = pytesseract.image_to_string(image)
    except pytesseract.TesseractNotFoundError as exc:
        raise NormalizationError(_TESSERACT_MISSING_MSG) from exc
    except Exception as exc:
        raise NormalizationError(f"OCR failed: {exc}") from exc
    return {"text": (text or "").strip(),
            "locations": [{"via": "ocr"}], "ocr_used": True}
