"""Manual-audit sheets (GUIDE §4.2–4.4) and their summary.

`make_sheets` writes CSV templates to results/audit/. Columns `score` / `confirmed` (+ `reason` /
`error_note`) hold the grader's labels. Columns prefixed `auto_` are mechanical checks only.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from pypdf import PdfReader

from .evaluation import match_key
from .ingestion import normalize_text

COHERENCE_N = 10


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig so Excel shows quotes correctly
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _anchor(page_text: str) -> str:
    """Fixed, label-independent position: first sentence that starts after the middle of the page."""
    mid = len(page_text) // 2
    m = re.search(r"(?<=[.!?:])\s+([A-Z][^\n]{40,})", page_text[mid:])
    start = mid + (m.start(1) if m else 0)
    return " ".join(page_text[start:start + 80].split())


def make_sheets(repo: Path, results: Path, chunks: dict[str, list]) -> dict[str, int]:
    manifest = json.loads((repo / "data" / "manifest.json").read_text(encoding="utf-8"))
    src = manifest["sources"][0]
    reader = PdfReader(repo / src["local_path"])
    page_text = {}

    def text_of(p: int) -> str:
        if p not in page_text:
            page_text[p] = normalize_text(reader.pages[p - 1].extract_text() or "")
        return page_text[p]

    page_chapter = {p: cid for cid, info in src["chapters"].items() for p in info["pages"]}
    out = results / "audit"

    # 1) Coherence: same 10 positions for every strategy = middle sentence of the first 10 counted pages.
    rows = []
    for pos, page in enumerate(sorted(src["counted_pdf_pages"])[:COHERENCE_N], start=1):
        anchor = _anchor(text_of(page))
        akey = match_key(anchor[:30])  # short prefix = the chunk that contains the start of the position
        for strat in ("A", "B", "C"):
            pool = chunks["B" if strat == "C" else strat]
            hit = next((c for c in pool if c.page == page and akey in match_key(c.text)), None)
            rows.append({"position_id": f"pos{pos:02d}", "page": page, "anchor_text": anchor, "strategy": strat,
                         "chunk_id": ("C:" + hit.chunk_id.split(":", 1)[1]) if (hit and strat == "C") else
                         (hit.chunk_id if hit else "NOT FOUND"),
                         "note": "same chunk text as B" if strat == "C" else "",
                         "chunk_text": hit.text if hit else "", "score": "", "reason": ""})
    _write_csv(out / "coherence.csv", rows)
    n_coh = len(rows)

    # 2) Metadata: test questions x top-3 x strategy; auto-check against the PDF page and manifest.
    rows = []
    for strat in ("A", "B", "C"):
        for t in _read_jsonl(results / f"traces_{strat}.jsonl"):
            if t["split"] != "test":
                continue
            for h in t["hits"]:
                on_page = match_key(h["text"][:200]) in match_key(text_of(h["page"]))
                rows.append({
                    "strategy": strat, "question_id": t["id"], "rank": h["rank"], "chunk_id": h["chunk_id"],
                    "source": h["source"], "page": h["page"], "chapter_id": h["chapter_id"],
                    "question_filter": json.dumps(t["question_filter"]) if t["question_filter"] else "",
                    "auto_source_ok": h["source"] == src["source_path_or_url"],
                    "auto_page_ok": on_page,
                    "auto_chapter_ok": page_chapter.get(h["page"]) == h["chapter_id"],
                    "auto_in_filter_scope": (all(h[k] == v for k, v in t["question_filter"].items())
                                             if t["question_filter"] else ""),
                    "confirmed": "", "error_note": ""})
    _write_csv(out / "metadata.csv", rows)
    n_meta = len(rows)

    # 3) Grounding: test questions x strategy; grader reads the top-3 texts.
    qs = {q["id"]: q for q in _read_jsonl(repo / "benchmark" / "questions.jsonl")}
    rows = []
    for strat in ("A", "B", "C"):
        for t in _read_jsonl(results / f"traces_{strat}.jsonl"):
            if t["split"] != "test":
                continue
            q = qs[t["id"]]
            row = {"strategy": strat, "question_id": t["id"], "question": q["question"],
                   "expected_answer": q["expected_answer"], "evidence_quote": q["evidence_quote"],
                   "applied_filter": json.dumps(t["applied_filter"]) if t["applied_filter"] else "",
                   "auto_evidence_hit_at_3": t["evidence_hit_at_3"]}
            for h in t["hits"]:
                row[f"top{h['rank']}"] = f"[p{h['page']} {h['chapter_id']} {h['score']}] {h['text']}"
            row.update({"score": "", "reason": ""})
            rows.append(row)
    _write_csv(out / "grounding.csv", rows)
    return {"coherence_rows": n_coh, "metadata_rows": n_meta, "grounding_rows": len(rows)}


def _read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def summarize_sheets(results: Path) -> dict:
    out = results / "audit"
    summary: dict = {}
    coh = _read_csv(out / "coherence.csv")
    for s in ("A", "B", "C"):
        sc = [int(r["score"]) for r in coh if r["strategy"] == s and r["score"].strip()]
        n = sum(1 for r in coh if r["strategy"] == s)
        summary.setdefault(s, {})["coherence"] = {"labelled": len(sc), "N": n,
                                                  "mean_of_2": round(sum(sc) / len(sc), 2) if sc else None}
    meta = _read_csv(out / "metadata.csv")
    for s in ("A", "B", "C"):
        rows = [r for r in meta if r["strategy"] == s]
        confirmed = [r for r in rows if r["confirmed"].strip()]
        ok = sum(1 for r in confirmed if r["confirmed"].strip().lower() in ("1", "yes", "true", "ok", "dung", "đúng"))
        filt_q = {}
        for r in rows:
            if r["question_filter"]:
                filt_q.setdefault(r["question_id"], []).append(r["auto_in_filter_scope"] == "True")
        summary[s]["metadata"] = {"confirmed": len(confirmed), "N": len(rows), "complete_and_correct": ok,
                                  "auto_all_ok": sum(1 for r in rows if r["auto_source_ok"] == "True"
                                                     and r["auto_page_ok"] == "True"
                                                     and r["auto_chapter_ok"] == "True"),
                                  "filter_compliant_questions": sum(all(v) for v in filt_q.values()),
                                  "filter_questions": len(filt_q)}
    gr = _read_csv(out / "grounding.csv")
    for s in ("A", "B", "C"):
        sc = [int(r["score"]) for r in gr if r["strategy"] == s and r["score"].strip()]
        n = sum(1 for r in gr if r["strategy"] == s)
        summary[s]["grounding"] = {"labelled": len(sc), "N": n, "count_0": sc.count(0), "count_1": sc.count(1),
                                   "count_2": sc.count(2), "mean_of_2": round(sum(sc) / len(sc), 2) if sc else None}
    return summary
