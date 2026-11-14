"""Fixed-size (strategy A) and structure-aware (strategy B) chunking.

Both strategies chunk inside a single page so every chunk keeps an exact `page`.
"""
from __future__ import annotations

import hashlib
import re

from .models import Chunk, DocumentPage

# Section heading of the book, e.g. "5.1 Floor division and modulus", "A.2.4 I added so many ..."
SECTION_RE = re.compile(r"^(?:\d{1,2}|[AB])(?:\.\d{1,2})+ [A-Z].{0,120}$")
# First lines of a chapter/appendix opening page: "Chapter 5" / "Appendix A"
CHAPTER_START_RE = re.compile(r"^(Chapter \d+|Appendix [AB])$")
SENTENCE_END_RE = re.compile(r"(?<=[.!?:])\s+(?=[A-Z(“\"'>])")
LINE_WIDTH = 90  # approx. characters of a full text line in this PDF


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _make_chunk(strategy: str, page: DocumentPage, index: int, text: str, extra: dict) -> Chunk:
    meta = {**page.metadata, **extra, "strategy": strategy, "chunk_index": index,
            "char_len": len(text), "content_hash": _hash(text)}
    return Chunk(
        chunk_id=f"{strategy}:{page.document_id}:p{page.page:03d}:{index:03d}",
        document_id=page.document_id, source=page.source, page=page.page, text=text, metadata=meta,
    )


def fixed_size(page: DocumentPage, size: int = 500, overlap: int = 50, strategy: str = "A") -> list[Chunk]:
    """Sliding character window (size, overlap) over the page text; ignores structure on purpose."""
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    text = re.sub(r"\s+", " ", page.text).strip()
    chunks, start, idx, step = [], 0, 0, size - overlap
    while start < len(text):
        piece = text[start:start + size].strip()
        if piece:
            chunks.append(_make_chunk(strategy, page, idx, piece, {"char_start": start}))
            idx += 1
        if start + size >= len(text):
            break
        start += step
    return chunks


def _blocks(text: str, chapter_title: str | None = None) -> list[tuple[str, str | None]]:
    """Split a page into (block_text, heading) blocks.

    A heading line starts a new block. A paragraph ends when a line ends a sentence and is
    clearly shorter than a full line (the PDF has no blank lines between paragraphs).
    """
    blocks: list[tuple[list[str], str | None]] = []
    cur: list[str] = []
    cur_heading: str | None = None
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if CHAPTER_START_RE.match(line) and i + 1 < len(lines):
            # title may wrap over 2 lines in the PDF; prefer the full title from the manifest
            heading = f"{line} {chapter_title or lines[i + 1]}"
            if cur:
                blocks.append((cur, cur_heading))
            cur, cur_heading = [line, lines[i + 1]], heading
            i += 2
            continue
        if SECTION_RE.match(line):
            if cur:
                blocks.append((cur, cur_heading))
            cur, cur_heading = [line], line
            i += 1
            continue
        cur.append(line)
        if re.search(r"[.!?:]$", line) and len(line) < LINE_WIDTH * 0.8:
            blocks.append((cur, cur_heading))
            cur, cur_heading = [], None
        i += 1
    if cur:
        blocks.append((cur, cur_heading))
    return [("\n".join(b).strip(), h) for b, h in blocks if "\n".join(b).strip()]


def _split_long(text: str, max_size: int) -> list[str]:
    """Split an oversized block at sentence boundaries (fallback: hard cut)."""
    parts, cur = [], ""
    for sent in SENTENCE_END_RE.split(text):
        while len(sent) > max_size:  # a single huge "sentence" (e.g. code)
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(sent[:max_size])
            sent = sent[max_size:]
        if cur and len(cur) + 1 + len(sent) > max_size:
            parts.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip() if cur else sent
    if cur:
        parts.append(cur)
    return parts


def by_paragraph(page: DocumentPage, max_size: int = 1200, strategy: str = "B",
                 section_in: str | None = None) -> tuple[list[Chunk], str | None]:
    """Group paragraphs into chunks <= max_size, starting a new chunk at every section heading.

    `section_in` is the section that continues from the previous page; returns (chunks, section_out)
    so the caller can carry the current section title across pages.
    """
    section = section_in
    groups: list[tuple[str, str | None]] = []  # (text, section)
    buf = ""
    for block, heading in _blocks(page.text, page.metadata.get("chapter_title")):
        if heading is not None:
            if buf:
                groups.append((buf, section))
                buf = ""
            section = heading
        for piece in (_split_long(block, max_size) if len(block) > max_size else [block]):
            if buf and len(buf) + 1 + len(piece) > max_size:
                groups.append((buf, section))
                buf = piece
            else:
                buf = f"{buf}\n{piece}" if buf else piece
    if buf:
        groups.append((buf, section))
    chunks = [_make_chunk(strategy, page, i, text, {"section": sec or ""})
              for i, (text, sec) in enumerate(groups)]
    return chunks, section


def chunk_pages(pages: list[DocumentPage], strategy: str, **params) -> list[Chunk]:
    """Chunk all pages (in page order) with strategy 'A' (fixed) or 'B' (paragraph)."""
    out: list[Chunk] = []
    section = None
    prev_chapter = None
    for page in sorted(pages, key=lambda p: p.page):
        if strategy == "A":
            out.extend(fixed_size(page, params.get("size", 500), params.get("overlap", 50), "A"))
        elif strategy == "B":
            if page.metadata.get("chapter_id") != prev_chapter:
                section = None  # do not carry a section across chapters
            chunks, section = by_paragraph(page, params.get("max_size", 1200), "B", section)
            out.extend(chunks)
        else:
            raise ValueError(f"unknown strategy {strategy}")
        prev_chapter = page.metadata.get("chapter_id")
    return out
