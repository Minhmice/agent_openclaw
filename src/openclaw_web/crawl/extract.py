"""Bounded semantic extraction for crawled HTML pages."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from openclaw_web.crawl.safety import UnsafeTarget, normalize_url

_WHITESPACE = re.compile(r"\s+")
_CTA_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("quote", ("bao gia", "quote", "estimate", "pricing")),
    ("consultation", ("tu van", "consultation", "consult", "advice")),
    ("booking", ("dat lich", "dat hen", "book", "appointment", "reserve")),
    ("contact", ("lien he", "contact", "get in touch")),
    ("call", ("goi ngay", "goi cho", "call now", "call us", "hotline")),
    ("zalo", ("zalo",)),
    ("purchase", ("mua ngay", "dat hang", "buy now", "purchase", "order now")),
    ("catalog", ("catalogue", "catalog", "bang mau", "xem mau")),
)


def _text(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _cta_action(label: str) -> str | None:
    folded = _fold(label)
    for action, phrases in _CTA_PATTERNS:
        if any(phrase in folded for phrase in phrases):
            return action
    return None


def _bounded(value: str, maximum: int) -> str:
    normalized = _text(value)
    if len(normalized) <= maximum:
        return normalized
    return normalized[:maximum].rstrip()


def _safe_link(page_url: str, raw_href: object) -> str | None:
    if not isinstance(raw_href, str) or not raw_href.strip():
        return None
    try:
        joined = urljoin(page_url, raw_href)
        if urlsplit(joined).scheme not in {"http", "https"}:
            return None
        return normalize_url(joined)
    except (UnsafeTarget, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class Heading:
    """One normalized semantic heading."""

    level: int
    text: str


@dataclass(frozen=True, slots=True)
class CallToAction:
    """One recognized commercial action."""

    text: str
    action: str
    href: str | None


@dataclass(frozen=True, slots=True)
class FormSummary:
    """A non-interactive description of one HTML form."""

    action: str | None
    method: str
    field_count: int
    fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceExcerpt:
    """A bounded human-readable excerpt, never raw markup."""

    text: str


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    """Immutable semantic page record safe for downstream serialization."""

    url: str
    title: str | None
    description: str | None
    headings: tuple[Heading, ...]
    ctas: tuple[CallToAction, ...]
    forms: tuple[FormSummary, ...]
    links: tuple[str, ...]
    evidence: tuple[EvidenceExcerpt, ...]


def extract_page(
    url: str,
    html: str,
    *,
    max_headings: int = 64,
    max_ctas: int = 64,
    max_forms: int = 32,
    max_links: int = 256,
    max_evidence_excerpts: int = 64,
    max_excerpt_chars: int = 240,
) -> ExtractedPage:
    """Extract a deterministic, bounded semantic record from already-bounded HTML."""

    limits = (
        max_headings,
        max_ctas,
        max_forms,
        max_links,
        max_evidence_excerpts,
        max_excerpt_chars,
    )
    if any(isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0 for limit in limits):
        raise ValueError("extraction limits must be positive integers")
    canonical_url = normalize_url(url)
    soup = BeautifulSoup(html, "html.parser")
    for ignored in soup(["script", "style", "template", "noscript"]):
        ignored.decompose()

    title_node = soup.find("title")
    title = _bounded(title_node.get_text(" "), max_excerpt_chars) if title_node else None
    title = title or None
    description: str | None = None
    description_node = soup.find(
        "meta", attrs={"name": re.compile(r"^description$", re.IGNORECASE)}
    )
    if isinstance(description_node, Tag):
        raw_description = description_node.get("content")
        if isinstance(raw_description, str):
            description = _bounded(raw_description, max_excerpt_chars) or None

    headings: list[Heading] = []
    seen_headings: set[tuple[int, str]] = set()
    for node in soup.find_all(re.compile(r"^h[1-6]$"), limit=max_headings * 2):
        text = _bounded(node.get_text(" "), max_excerpt_chars)
        if not text:
            continue
        item = (int(node.name[1]), text)
        if item not in seen_headings:
            seen_headings.add(item)
            headings.append(Heading(*item))
        if len(headings) >= max_headings:
            break

    links: list[str] = []
    seen_links: set[str] = set()
    ctas: list[CallToAction] = []
    seen_ctas: set[tuple[str, str, str | None]] = set()
    for node in soup.find_all(["a", "button"], limit=max(max_links, max_ctas) * 4):
        label = _bounded(node.get_text(" "), max_excerpt_chars)
        href = _safe_link(canonical_url, node.get("href")) if node.name == "a" else None
        if href is not None and href not in seen_links and len(links) < max_links:
            seen_links.add(href)
            links.append(href)
        action = _cta_action(label)
        identity = (label, action or "", href)
        if action is not None and label and identity not in seen_ctas and len(ctas) < max_ctas:
            seen_ctas.add(identity)
            ctas.append(CallToAction(label, action, href))

    forms: list[FormSummary] = []
    for node in soup.find_all("form", limit=max_forms):
        fields: list[str] = []
        for field in node.find_all(["input", "select", "textarea"]):
            if field.name == "input" and str(field.get("type", "")).casefold() in {
                "button",
                "submit",
                "reset",
                "image",
            }:
                continue
            name = field.get("name")
            fields.append(_bounded(name, 128) if isinstance(name, str) and name.strip() else field.name)
        action = _safe_link(canonical_url, node.get("action"))
        method = str(node.get("method", "get")).strip().casefold() or "get"
        forms.append(FormSummary(action, method, len(fields), tuple(fields)))

    evidence: list[EvidenceExcerpt] = []
    seen_evidence: set[str] = set()
    for node in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"]):
        excerpt = _bounded(node.get_text(" "), max_excerpt_chars)
        if excerpt and excerpt not in seen_evidence:
            seen_evidence.add(excerpt)
            evidence.append(EvidenceExcerpt(excerpt))
        if len(evidence) >= max_evidence_excerpts:
            break

    return ExtractedPage(
        url=canonical_url,
        title=title,
        description=description,
        headings=tuple(headings),
        ctas=tuple(ctas),
        forms=tuple(forms),
        links=tuple(links),
        evidence=tuple(evidence),
    )
