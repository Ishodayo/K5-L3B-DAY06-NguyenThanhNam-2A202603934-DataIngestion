"""Read and normalize source data (Think Python 2e PDF) with page-level traceability."""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

from .models import DocumentPage

logging.getLogger("pypdf").setLevel(logging.ERROR)  # hide fontTools warnings, not errors we log ourselves

# Running header on the first line of each body page, e.g.
#   "24 Chapter 1. The way of the program"   (even page)
#   "1.3. The first program 3"               (odd page)
#   "194 Appendix A. Debugging" / "A.2. Runtime errors 195"
HEADER_RE = re.compile(
    r"^(\d+\s+(Chapter|Appendix)\s+\w+\..*|[\dA-B]+(\.\d+)+\.\s.*\s\d+|(Index\s\d+|\d+\sIndex))$"
)
# Hyphenated line break inside a word: "lan-\nguage" -> "language"
SOFT_HYPHEN_RE = re.compile(r"(?<=[a-z])-\n(?=[a-z])")


def normalize_text(text: str) -> str:
    """NFC, drop running header, join hyphenated line breaks, collapse spaces.

    Line breaks are kept (needed to detect headings/paragraphs in chunking).
    """
    text = unicodedata.normalize("NFC", text)
    lines = [ln.rstrip() for ln in text.splitlines()]
    if lines and HEADER_RE.match(lines[0].strip()):
        lines = lines[1:]
    text = "\n".join(lines)
    text = SOFT_HYPHEN_RE.sub("", text)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_pdf(path: Path, document_id: str, source: str) -> list[DocumentPage]:
    """Extract every physical page (1-based PDF page number); empty/failed pages keep text=''.

    Errors are recorded in metadata['error'] instead of being silently skipped.
    """
    reader = PdfReader(path)
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        meta: dict = {}
        try:
            text = normalize_text(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 - logged per page
            text, meta["error"] = "", repr(exc)
        pages.append(DocumentPage(document_id, source, i, text, meta))
    return pages


@dataclass
class IngestReport:
    physical_pages: int = 0
    counted_pages: int = 0
    loaded_pages: int = 0
    empty_pages: list[int] = field(default_factory=list)
    error_pages: list[dict] = field(default_factory=list)
    chars_total: int = 0


def load_corpus(repo_root: Path) -> tuple[list[DocumentPage], IngestReport, dict]:
    """Load only the content pages listed in data/manifest.json and attach chapter metadata.

    chapter_id / chapter_title come from the manifest (built from the PDF outline), not guessed.
    """
    manifest = json.loads((repo_root / "data" / "manifest.json").read_text(encoding="utf-8"))
    src = manifest["sources"][0]
    page_to_chapter = {
        p: (cid, info["chapter_title"]) for cid, info in src["chapters"].items() for p in info["pages"]
    }
    counted = set(src["counted_pdf_pages"])
    all_pages = load_pdf(repo_root / src["local_path"], src["source_id"], src["source_path_or_url"])

    report = IngestReport(physical_pages=len(all_pages), counted_pages=len(counted))
    pages = []
    for p in all_pages:
        if p.page not in counted:
            continue
        if "error" in p.metadata:
            report.error_pages.append({"page": p.page, "error": p.metadata["error"]})
            continue
        if not p.text:
            report.empty_pages.append(p.page)
            continue
        cid, ctitle = page_to_chapter[p.page]
        p.metadata.update({"chapter_id": cid, "chapter_title": ctitle})
        pages.append(p)
    report.loaded_pages = len(pages)
    report.chars_total = sum(len(p.text) for p in pages)
    return pages, report, manifest
