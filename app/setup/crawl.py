"""
Polite website crawler for the Setup stage. ONE external dependency in the
whole flow — kept small, time-bounded, same-domain only, and (most importantly)
NEVER raises. The setup form is the always-works fallback, so crawl failures
must surface as a status the UI can render ("couldn't read the site, fill it
in manually") instead of a 500.

Scope on purpose:
  * Max ~5 same-domain pages (homepage + a tiny allowlist of "tell me about
    yourself" paths discovered from homepage links).
  * Short per-request timeout. The whole crawl is bounded in wall-time so a
    slow site can't hang the API.
  * Strip script/style/nav/header/footer/aside chrome; keep visible text and
    a few structural hints (title, meta description, headings).
  * Hard cap on total extracted characters before we hand off to the LLM.
"""
from __future__ import annotations

import logging
import re
import traceback
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "AgentHQ-SetupCrawler/0.1 (+https://github.com/agenthq)"
PAGE_TIMEOUT_S = 8.0
MAX_PAGES = 5
TEXT_CAP = 30_000  # characters fed to the LLM

# The "tell me about yourselves" allowlist. Same-domain only; we don't recurse
# beyond these and we only follow them if the homepage actually links to them.
_DISCOVERY_HINTS = (
    "/about", "/about-us", "/company",
    "/product", "/products", "/platform",
    "/pricing", "/plans",
    "/solutions", "/use-cases", "/customers",
)


class _TextExtractor(HTMLParser):
    """Extract visible text + structural hints from HTML. Skips script/style
    and the most common chrome regions (nav/header/footer/aside) so the LLM
    sees the content the page is actually about."""

    SKIP_CONTENT = {"script", "style", "noscript", "template", "svg"}
    SKIP_CHROME = {"nav", "header", "footer", "aside"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self.title = ""
        self.meta_description = ""
        self.headings: list[str] = []
        self._heading_tag: str | None = None
        self._heading_buf: list[str] = []
        self._text_parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_CONTENT or tag in self.SKIP_CHROME:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            ad = dict(attrs)
            if ad.get("name", "").lower() == "description" and ad.get("content"):
                self.meta_description = ad["content"].strip()
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        elif tag in ("h1", "h2", "h3"):
            if self._skip_depth == 0:
                self._heading_tag = tag
                self._heading_buf = []

    def handle_endtag(self, tag):
        if tag in self.SKIP_CONTENT or tag in self.SKIP_CHROME:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        elif tag == self._heading_tag:
            text = " ".join("".join(self._heading_buf).split()).strip()
            if text:
                self.headings.append(text)
            self._heading_tag = None
            self._heading_buf = []

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title = (self.title + data).strip()
            return
        if self._heading_tag:
            self._heading_buf.append(data)
        self._text_parts.append(data)

    @property
    def text(self) -> str:
        # Collapse runs of whitespace to keep token budget tight.
        joined = " ".join(self._text_parts)
        return re.sub(r"\s+", " ", joined).strip()


def _normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw


def _same_domain(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.netloc or "").lower() == (pb.netloc or "").lower()


def _discover(root_url: str, homepage_links: list[str]) -> list[str]:
    """From the homepage's links, pick up to MAX_PAGES-1 same-domain pages
    whose paths look like 'about/product/pricing/solutions'. We do NOT recurse
    further; this is one shallow layer."""
    seen: set[str] = set()
    out: list[str] = []
    for href in homepage_links:
        abs_url = urljoin(root_url, href).split("#", 1)[0]
        if not abs_url.startswith(("http://", "https://")):
            continue
        if not _same_domain(root_url, abs_url):
            continue
        path = urlparse(abs_url).path.rstrip("/").lower()
        if not path:
            continue
        if not any(path == h or path.startswith(h + "/") for h in _DISCOVERY_HINTS):
            continue
        if abs_url in seen:
            continue
        seen.add(abs_url)
        out.append(abs_url)
        if len(out) >= MAX_PAGES - 1:
            break
    return out


def _fetch(client: httpx.Client, url: str) -> tuple[str, dict | None]:
    """Return (html, error). On success `error` is None and `html` is content.
    On failure `html` is "" and `error` is a structured dict so callers (and
    the UI) can see the real cause instead of a generic label:
        {"kind": "timeout"|"transport"|"http_status"|"not_html",
         "exception_type": str,      # python class name, if any
         "exception_message": str,   # str(exc) — the actual reason
         "http_status": int | None,  # set when the server responded
         "url": str}
    Never raises. We also log the traceback for transport errors so the dev
    seeing the UI message can grep the worker/uvicorn logs for the cause."""
    try:
        resp = client.get(url, follow_redirects=True, timeout=PAGE_TIMEOUT_S)
    except httpx.TimeoutException as exc:
        log.warning("crawl timeout url=%s: %s", url, exc)
        return "", {"kind": "timeout", "exception_type": exc.__class__.__name__,
                    "exception_message": str(exc), "http_status": None, "url": url}
    except httpx.HTTPError as exc:
        log.warning("crawl httpx error url=%s: %s: %s\n%s",
                    url, exc.__class__.__name__, exc, traceback.format_exc())
        return "", {"kind": "transport", "exception_type": exc.__class__.__name__,
                    "exception_message": str(exc), "http_status": None, "url": url}
    except Exception as exc:  # any other transport error — still must not raise
        log.warning("crawl unexpected error url=%s: %s: %s\n%s",
                    url, exc.__class__.__name__, exc, traceback.format_exc())
        return "", {"kind": "transport", "exception_type": exc.__class__.__name__,
                    "exception_message": str(exc), "http_status": None, "url": url}
    if resp.status_code != 200:
        # Capture a snippet of the body too — Cloudflare-style challenges
        # respond with HTTP 403 + an HTML "Just a moment..." page, and seeing
        # that snippet is what tells you "bot-protected, not a code bug."
        body_snip = (resp.text or "")[:200].strip()
        return "", {"kind": "http_status", "exception_type": None,
                    "exception_message": f"HTTP {resp.status_code}: {body_snip!r}",
                    "http_status": resp.status_code, "url": url}
    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype.lower() and "<html" not in resp.text.lower()[:200]:
        return "", {"kind": "not_html", "exception_type": None,
                    "exception_message": f"content-type={ctype!r}, body did not look like HTML",
                    "http_status": resp.status_code, "url": url}
    return resp.text, None


def _extract(html: str) -> _TextExtractor:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        # malformed HTML — keep whatever we got before the parser tripped
        pass
    return p


def crawl(url: str, *, client: httpx.Client | None = None) -> dict:
    """Crawl one site. Returns a structured result the caller can render:
      {
        "status":  "ok" | "timeout" | "http_error" | "empty" | "invalid_url" | "error",
        "url":     normalized start url,
        "message": short human-readable explanation when status != "ok",
        "pages":   [{url, title, meta_description, headings, text}, ...],
        "title":   homepage <title>,
        "meta_description": homepage meta description,
        "headings": homepage H1/H2/H3 list,
        "text":    capped, concatenated visible text across crawled pages,
      }
    Never raises. The Setup form path must work even when this returns a
    non-"ok" status — the UI presents that as "couldn't read the site,
    fill it in manually."
    """
    norm = _normalize_url(url)
    if not norm or not urlparse(norm).netloc:
        return {"status": "invalid_url", "url": url, "message": "URL is empty or malformed",
                "pages": [], "title": "", "meta_description": "", "headings": [], "text": ""}

    owns_client = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    try:
        home_html, err = _fetch(client, norm)
        if err:
            # `err` is now a structured dict (see _fetch) — pass its detail
            # through to the API result so the UI / dev logs can show the
            # real cause (exception type + message + status) instead of a
            # generic label.
            status = "timeout" if err["kind"] == "timeout" else "http_error"
            return {"status": status, "url": norm,
                    "message": _format_err("Homepage fetch failed", err),
                    "error": err,
                    "pages": [], "title": "", "meta_description": "",
                    "headings": [], "text": ""}
        home = _extract(home_html)
        pages = [_page_payload(norm, home)]

        for sub_url in _discover(norm, home.links):
            html, err = _fetch(client, sub_url)
            if err:
                continue  # one bad subpage doesn't kill the crawl
            pages.append(_page_payload(sub_url, _extract(html)))

        combined = _combine(pages)
        if not combined.strip():
            return {"status": "empty", "url": norm,
                    "message": "Site returned 200 but contained no extractable text "
                               "(JavaScript-only page?)",
                    "pages": pages, "title": home.title,
                    "meta_description": home.meta_description,
                    "headings": home.headings, "text": ""}
        return {"status": "ok", "url": norm, "pages": pages,
                "title": home.title, "meta_description": home.meta_description,
                "headings": home.headings, "text": combined}
    except Exception as exc:  # absolute last-resort net — must not 500 callers
        return {"status": "error", "url": norm,
                "message": f"Unexpected crawl error: {exc.__class__.__name__}",
                "pages": [], "title": "", "meta_description": "",
                "headings": [], "text": ""}
    finally:
        if owns_client:
            client.close()


def _format_err(prefix: str, err: dict) -> str:
    bits = [err.get("kind") or "?"]
    if err.get("exception_type"):
        bits.append(err["exception_type"])
    if err.get("http_status"):
        bits.append(f"HTTP {err['http_status']}")
    msg = err.get("exception_message") or ""
    head = ", ".join(bits)
    return f"{prefix} ({head}): {msg}" if msg else f"{prefix} ({head})"


def _page_payload(url: str, p: _TextExtractor) -> dict:
    return {
        "url": url,
        "title": p.title,
        "meta_description": p.meta_description,
        "headings": p.headings[:20],
        "text": p.text[:TEXT_CAP],
    }


def _combine(pages: list[dict]) -> str:
    """Concatenate page text with a small header line per page, capped overall
    so we don't hand the LLM more than ~TEXT_CAP characters total."""
    parts: list[str] = []
    used = 0
    for pg in pages:
        header = f"\n\n### {pg['title'] or pg['url']}\n"
        body = pg["text"] or ""
        chunk = header + body
        if used + len(chunk) > TEXT_CAP:
            chunk = chunk[: max(0, TEXT_CAP - used)]
        parts.append(chunk)
        used += len(chunk)
        if used >= TEXT_CAP:
            break
    return "".join(parts).strip()
