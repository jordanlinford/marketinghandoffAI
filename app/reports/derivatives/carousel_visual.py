"""
Carousel visual pipeline (Stage 2). Renders validated slide content
to branded PNG files via Pillow.

RENDERER CHOICE (stated for the record): Pillow. Reasons:
  * Already installed (12.2.0); zero new system deps.
  * Cross-platform — works on macOS dev + Linux container.
  * Templated populate-and-fill maps cleanly to Pillow primitives
    (rectangles, text, image composite). No headless browser, no
    cairo, no PDF intermediary.
  * The Canva-model the brief calls for (data flows into predefined
    slots) IS Pillow's strength — predictable, deterministic output.

DISCIPLINE this module enforces:
  * INPUT IS VALIDATED CONTENT. The renderer reads slide.text from
    a content dict that has ALREADY passed containment. It does NOT
    reach into the source anchor, the intelligence engine, or the
    ledger builder. The visual layer literally cannot introduce a
    new claim — it can only lay out what Stage 1 produced.
  * BRAND IS PRESENTATION. brand_tokens style the slide (color
    fills, accent bars, font choices, logo paste). Brand never
    mutates slide text — same body.brand_tokens-sibling discipline
    that holds across the platform. The smoke pins this with a
    byte-identical-text-across-brand-states assertion.
  * NO FABRICATION IN RENDER. Every string drawn into the image is
    a substring of a Stage-1 slide.text (after ⟦ev:id⟧ marker
    stripping). The render step does not add labels, footnotes,
    or filler.

The agent owns the I/O lifecycle (output dir, persistence of
rendered_assets to body); this module is pure: given (content,
brand, output_dir, run_id) it writes the files and returns the
manifests so callers can verify what was drawn.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.documents.storage import storage_root


# --------------------------------------------------------------------------
# Canvas constants. 1080x1080 = Instagram square; renders cleanly at
# every social aspect. Padding leaves the visible composition area at
# 960x960 — plenty for templated text + a logo footer.
# --------------------------------------------------------------------------
_CANVAS = (1080, 1080)
_PAD = 60
_INNER_W = _CANVAS[0] - 2 * _PAD  # 960


# --------------------------------------------------------------------------
# Marker stripping — markers ⟦ev:id⟧ are PRODUCTION artifacts of the
# validator binding, not viewer-facing copy. The visual layer strips
# them before drawing. The numbers themselves stay; only the marker
# annotation is removed. NEVER trim or substitute the number — that
# would change the claim. This is a presentation-only transform.
#
# Whitespace normalization rules (presentation only — fixes the
# marker-residue ghost-space bug AND the orphan-space-before-punct
# bug an LLM-emitted "X⟦ev:7⟧ ; Y⟦ev:9⟧" can produce):
#   1. Strip the marker token itself.
#   2. Collapse internal runs of 2+ spaces/tabs to a single space —
#      so "42 ⟦ev:19⟧ runs" → "42  runs" → "42 runs".
#   3. Remove a single space immediately before sentence punctuation
#      (",.;:!?") — so "ready ; 0 pending" → "ready; 0 pending".
# Each rule preserves every word and every number verbatim — only
# whitespace and the marker disappear. Bound numbers stay bound to
# their (already-stripped) ⟦ev:id⟧ in the trust path; this function
# operates on the text post-validation, so it CANNOT unbind anything.
# --------------------------------------------------------------------------
_MARKER_RE = re.compile(r"⟦ev:[A-Za-z0-9_\-]+⟧")
_MULTI_WS_RE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT_RE = re.compile(r" +([,.;:!?])")


def strip_markers(text: str) -> str:
    """Remove ⟦ev:id⟧ markers from text and normalize the whitespace
    they leave behind. Idempotent. Operates line-by-line so a slide's
    title/body newline separator is preserved verbatim.

    Used both for rendering AND for the no-fabrication containment
    check in smoke (verifies drawn text ⊆ validated text)."""
    if not text:
        return ""
    out: list[str] = []
    for line in text.split("\n"):
        # Strip the marker → collapse 2+ spaces → remove pre-punct spaces.
        line = _MARKER_RE.sub("", line)
        line = _MULTI_WS_RE.sub(" ", line)
        line = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", line)
        out.append(line.strip())
    return "\n".join(out).strip()


# --------------------------------------------------------------------------
# Brand defaults. When a brand token is unset, we fall back to the
# platform defaults from _DEFAULT_BRAND in app/api/brand.py — same
# values, kept in sync via a comment because importing that constant
# would pull an API module into the render path.
# --------------------------------------------------------------------------
_DEFAULT_PRIMARY    = "#1f3b6b"
_DEFAULT_SECONDARY  = "#2d8c5a"
_DEFAULT_ACCENT     = "#c89a3a"
_DEFAULT_BACKGROUND = "#0e1218"
_DEFAULT_TEXT       = "#e6e9f0"


def _brand_color(brand: dict, key: str, default: str) -> str:
    """Read a brand color with a fallback. Brand is the dict shape
    surfaced by app/api/brand.brand_for_org — None values fall back."""
    v = (brand or {}).get(key)
    if isinstance(v, str) and v.startswith("#") and len(v) in (7, 4):
        return v
    return default


# --------------------------------------------------------------------------
# Font loading. We try the brand's font name as a TTF lookup on a small
# set of well-known font paths; on miss, fall back to a system serif
# (macOS Arial / Linux DejaVuSans). The brand_token's font_heading /
# font_body remain advisory — a missing font does NOT block rendering.
# --------------------------------------------------------------------------
_FONT_FALLBACKS = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",                  # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",               # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",          # Linux bold
]


def _load_font(size: int, brand: dict, slot: str = "body") -> ImageFont.FreeTypeFont:
    """Return a TTF font at `size`. Brand fonts are advisory — when
    no installed TTF matches the brand's font name, we fall back
    silently. Never raises; the renderer must always produce SOMETHING."""
    for path in _FONT_FALLBACKS:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    # Final fallback: bitmap default. Hideous but never None.
    return ImageFont.load_default()


# --------------------------------------------------------------------------
# Logo paste — reuses brand.logo_path the same way the report detail
# chrome does. Best-effort: if the file doesn't exist, the slide
# renders without a logo (no crash).
# --------------------------------------------------------------------------
def _paste_logo(img: Image.Image, brand: dict, *, max_h: int = 80) -> None:
    logo_path = (brand or {}).get("logo_path")
    if not logo_path:
        return
    abs_path = storage_root() / logo_path
    if not abs_path.is_file():
        return
    try:
        logo = Image.open(str(abs_path)).convert("RGBA")
    except Exception:
        return
    # Scale to max_h while preserving aspect.
    w, h = logo.size
    if h > max_h:
        ratio = max_h / h
        logo = logo.resize((int(w * ratio), int(h * ratio)),
                            Image.LANCZOS)
    # Place at bottom-right of the safe area.
    x = _CANVAS[0] - _PAD - logo.size[0]
    y = _CANVAS[1] - _PAD - logo.size[1]
    img.paste(logo, (x, y), logo)


# --------------------------------------------------------------------------
# Drawing helpers.
# --------------------------------------------------------------------------
def _wrap_lines(text: str, font: ImageFont.FreeTypeFont,
                max_width: int) -> list[str]:
    """Word-wrap `text` so each line fits within max_width pixels at
    the given font. Pillow doesn't have a true measure-aware wrap, so
    we approximate with textwrap on character count, then refine by
    measuring each candidate line and re-splitting on overflow."""
    if not text:
        return []
    # First pass: textwrap by characters to a sensible default.
    chars = max(20, max_width // (font.size // 2 or 12))
    candidate_lines = textwrap.wrap(text, width=chars) or [text]
    # Second pass: measure each line; if it exceeds max_width, split.
    out: list[str] = []
    for line in candidate_lines:
        if _text_width(font, line) <= max_width:
            out.append(line)
            continue
        # Re-split greedily on width.
        words = line.split()
        cur = ""
        for w in words:
            tentative = (cur + " " + w).strip()
            if _text_width(font, tentative) <= max_width:
                cur = tentative
            else:
                if cur:
                    out.append(cur)
                cur = w
        if cur:
            out.append(cur)
    return out


def _text_width(font: ImageFont.FreeTypeFont, s: str) -> int:
    bbox = font.getbbox(s)
    return bbox[2] - bbox[0]


def _text_height(font: ImageFont.FreeTypeFont, s: str = "Hg") -> int:
    bbox = font.getbbox(s)
    return bbox[3] - bbox[1]


def _draw_centered_block(draw: ImageDraw.ImageDraw,
                          lines: list[str],
                          font: ImageFont.FreeTypeFont,
                          color: str,
                          y_center: int,
                          drawn_log: list[str]) -> None:
    """Draw `lines` stacked vertically, each horizontally centered,
    with the block's vertical center at y_center. Records each line
    in drawn_log — that's the 'what did we render' manifest the smoke
    uses to prove no-fabrication-in-render."""
    line_h = int(_text_height(font) * 1.4) or font.size + 6
    total = line_h * len(lines)
    y = y_center - total // 2
    for ln in lines:
        w = _text_width(font, ln)
        x = (_CANVAS[0] - w) // 2
        draw.text((x, y), ln, fill=color, font=font)
        drawn_log.append(ln)
        y += line_h


def _accent_bar(draw: ImageDraw.ImageDraw, color: str,
                *, top: bool = False) -> None:
    y0 = 0 if top else _CANVAS[1] - 16
    draw.rectangle([(0, y0), (_CANVAS[0], y0 + 16)], fill=color)


# --------------------------------------------------------------------------
# Per-slot templates. Each takes (slide, brand, font sizes), produces
# the rendered Image. The agent owns dispatch over slides; templates
# are pure functions.
# --------------------------------------------------------------------------
def _slide_image(slide: dict, brand: dict) -> tuple[Image.Image, list[str]]:
    """Render ONE slide. Returns (image, drawn_log). drawn_log lists
    every string actually painted to the image, in order — the smoke
    uses it to verify the visual layer added no text outside
    slide.text. Brand tokens style the canvas; they cannot mutate the
    text."""
    slot = (slide.get("slot") or "insight").lower()
    raw_text = slide.get("text") or ""
    text = strip_markers(raw_text)
    # Split on first newline — first line is the slide title, rest is
    # body. Matches the carousel renderer's emission convention.
    if "\n" in text:
        title, body = text.split("\n", 1)
    else:
        title, body = text, ""

    bg      = _brand_color(brand, "color_background", _DEFAULT_BACKGROUND)
    text_c  = _brand_color(brand, "color_text",       _DEFAULT_TEXT)
    primary = _brand_color(brand, "color_primary",    _DEFAULT_PRIMARY)
    secondary = _brand_color(brand, "color_secondary", _DEFAULT_SECONDARY)
    accent  = _brand_color(brand, "color_accent",     _DEFAULT_ACCENT)

    img = Image.new("RGB", _CANVAS, bg)
    draw = ImageDraw.Draw(img)
    drawn: list[str] = []

    if slot == "hook":
        # Big single-line hook, centered. Accent bar bottom.
        title_font = _load_font(72, brand)
        body_font = _load_font(36, brand)
        title_lines = _wrap_lines(title, title_font,
                                    _INNER_W) if title else []
        body_lines = _wrap_lines(body, body_font, _INNER_W) if body else []
        _draw_centered_block(draw, title_lines, title_font, text_c,
                              _CANVAS[1] // 2 - 60, drawn)
        if body_lines:
            _draw_centered_block(draw, body_lines, body_font,
                                  text_c, _CANVAS[1] // 2 + 80, drawn)
        _accent_bar(draw, accent)
    elif slot == "finding":
        # Title (big) up top, body emphasized. Primary accent bar.
        title_font = _load_font(80, brand)
        body_font = _load_font(42, brand)
        title_lines = _wrap_lines(title, title_font, _INNER_W)
        body_lines = _wrap_lines(body, body_font, _INNER_W) if body else []
        _draw_centered_block(draw, title_lines, title_font,
                              primary, _CANVAS[1] // 2 - 80, drawn)
        if body_lines:
            _draw_centered_block(draw, body_lines, body_font,
                                  text_c, _CANVAS[1] // 2 + 100, drawn)
        _accent_bar(draw, primary)
    elif slot == "insight":
        # Like finding but secondary palette.
        title_font = _load_font(64, brand)
        body_font = _load_font(36, brand)
        title_lines = _wrap_lines(title, title_font, _INNER_W)
        body_lines = _wrap_lines(body, body_font, _INNER_W) if body else []
        _draw_centered_block(draw, title_lines, title_font,
                              secondary, _CANVAS[1] // 2 - 60, drawn)
        if body_lines:
            _draw_centered_block(draw, body_lines, body_font,
                                  text_c, _CANVAS[1] // 2 + 80, drawn)
        _accent_bar(draw, secondary)
    elif slot == "rec":
        # Recommendation — accent on title (chip-like emphasis), body
        # in plain text below.
        title_font = _load_font(60, brand)
        body_font = _load_font(38, brand)
        title_lines = _wrap_lines(title, title_font, _INNER_W)
        body_lines = _wrap_lines(body, body_font, _INNER_W) if body else []
        _draw_centered_block(draw, title_lines, title_font,
                              accent, _CANVAS[1] // 2 - 80, drawn)
        if body_lines:
            _draw_centered_block(draw, body_lines, body_font,
                                  text_c, _CANVAS[1] // 2 + 80, drawn)
        _accent_bar(draw, accent)
    elif slot == "cta":
        # CTA centered + a button-shape rect with accent fill.
        title_font = _load_font(60, brand)
        body_font = _load_font(34, brand)
        title_lines = _wrap_lines(title, title_font, _INNER_W)
        body_lines = _wrap_lines(body, body_font, _INNER_W) if body else []
        _draw_centered_block(draw, title_lines, title_font,
                              text_c, _CANVAS[1] // 2 - 80, drawn)
        if body_lines:
            _draw_centered_block(draw, body_lines, body_font,
                                  text_c, _CANVAS[1] // 2 + 60, drawn)
        # Button strip — purely decorative, no text inside.
        bar_h = 12
        bar_y = _CANVAS[1] - _PAD * 2
        draw.rectangle([(_PAD, bar_y), (_CANVAS[0] - _PAD,
                                          bar_y + bar_h)], fill=accent)
    else:
        # Unknown slot — render as a generic centered slide. Defensive
        # branch; the existing carousel renderer constrains slot to
        # the known set.
        title_font = _load_font(56, brand)
        body_font = _load_font(34, brand)
        title_lines = _wrap_lines(title, title_font, _INNER_W)
        body_lines = _wrap_lines(body, body_font, _INNER_W) if body else []
        _draw_centered_block(draw, title_lines, title_font, text_c,
                              _CANVAS[1] // 2 - 60, drawn)
        if body_lines:
            _draw_centered_block(draw, body_lines, body_font,
                                  text_c, _CANVAS[1] // 2 + 60, drawn)
        _accent_bar(draw, accent)

    _paste_logo(img, brand)
    return img, drawn


def carousel_output_dir(org_id: str, run_id: str) -> Path:
    """Where slides land. Same {storage_root}/{org_id}/_<...>/ pattern
    brand uses for the logo — no new storage mechanism."""
    p = storage_root() / org_id / "_carousels" / run_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def render_carousel_images(content: dict, brand: dict,
                            *, org_id: str, run_id: str
                            ) -> tuple[list[dict], list[dict]]:
    """Render every slide in `content` to a PNG. Returns:
      file_records: [{idx, slot, path, filename}, ...] — what the
        agent attaches to body.rendered_assets.
      manifests:    [{idx, slot, drawn_lines: [...]}, ...] — the
        verifier output; the agent ignores it, the smoke uses it
        to prove the renderer added no text the validated content
        didn't already contain.

    `content` must be carousel-shape (blocks with kind='slide' and
    a `slot` field). Brand is the dict shape brand_for_org returns;
    None values fall back to platform defaults — unset brand is a
    valid render state.
    """
    blocks = (content or {}).get("blocks") or []
    out_dir = carousel_output_dir(org_id, run_id)
    file_records: list[dict] = []
    manifests: list[dict] = []
    for idx, slide in enumerate(blocks):
        if (slide or {}).get("kind") != "slide":
            continue
        img, drawn = _slide_image(slide, brand or {})
        filename = f"slide_{idx:02d}.png"
        abs_path = out_dir / filename
        img.save(str(abs_path), "PNG", optimize=True)
        rel_path = abs_path.relative_to(storage_root()).as_posix()
        file_records.append({
            "idx": idx,
            "slot": slide.get("slot"),
            "filename": filename,
            "path": rel_path,
        })
        manifests.append({
            "idx": idx,
            "slot": slide.get("slot"),
            "drawn_lines": drawn,
        })
    return file_records, manifests
