"""Evaluate retrieved evidence and preserve per-query traces.

Hit definition (GUIDE §4.1): a retrieved chunk is a hit when it comes from a gold page (main or
alternative) AND contains the whole evidence quote of that gold pair. Matching rule (benchmark/README.md):
NFC -> drop end-of-line hyphenation -> remove all whitespace -> lowercase -> substring.
Coherence and grounding are NOT inferred here; they are labelled manually (results/audit/).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any


def match_key(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"-\n", "", text)
    return re.sub(r"\s+", "", text).lower()


def gold_pairs(question: dict[str, Any]) -> list[tuple[str, int, str]]:
    pairs = [(question["gold_source_id"], question["gold_page"], question["evidence_quote"])]
    for alt in question.get("alternatives", []):
        pairs.append((alt.get("gold_source_id", question["gold_source_id"]), alt["gold_page"], alt["evidence_quote"]))
    return pairs


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]],
                   applied_filter: dict[str, Any] | None = None) -> dict[str, Any]:
    """Label each of the top-k hits and compute Hit@1 / Evidence Hit@3 for one question."""
    pairs = [(src, page, match_key(ev)) for src, page, ev in gold_pairs(question)]
    gold_pages = {(src, page) for src, page, _ in pairs}
    labelled = []
    for rank, h in enumerate(hits, start=1):
        key = match_key(h["text"])
        evidence_hit = any(h["document_id"] == src and h["page"] == page and ev in key for src, page, ev in pairs)
        labelled.append({
            "rank": rank, "chunk_id": h["chunk_id"], "score": round(h["score"], 4),
            "document_id": h["document_id"], "source": h["source"], "page": h["page"],
            "chapter_id": h["chapter_id"], "section": h.get("section"),
            "page_hit": (h["document_id"], h["page"]) in gold_pages,
            "evidence_hit": evidence_hit, "text": h["text"],
        })
    hit1 = bool(labelled) and labelled[0]["evidence_hit"]
    hit3 = any(x["evidence_hit"] for x in labelled[:3])
    page_hit3 = any(x["page_hit"] for x in labelled[:3])
    q_filter = question.get("filter")
    compliance = None
    if q_filter:  # does top-3 respect the user's scope? (measured for every strategy, filtered or not)
        compliance = all(all(x[k] == v for k, v in q_filter.items()) for x in labelled[:3])
    failure = None
    if not hit3:
        # Whether the evidence is split across chunks is checked separately against the full chunk list.
        failure = ("gold page in top-3 but no retrieved chunk contains the whole evidence"
                   if page_hit3 else "gold page not in top-3")
    return {
        "id": question["id"], "split": question["split"], "type": question.get("type"),
        "question": question["question"], "expected_answer": question["expected_answer"],
        "question_filter": q_filter, "applied_filter": applied_filter,
        "gold": [{"source_id": s, "page": p} for s, p, _ in gold_pairs(question)],
        "hit_at_1": hit1, "evidence_hit_at_3": hit3, "page_hit_at_3": page_hit3,
        "filter_compliant_top3": compliance, "failure": failure, "hits": labelled,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate Hit@1 and Evidence Hit@3 (as n/N) by strategy and split."""
    out: dict[str, Any] = {}
    for t in traces:
        key = (t["strategy"], t["split"])
        s = out.setdefault(key, {"strategy": t["strategy"], "split": t["split"], "N": 0, "hit1": 0, "hit3": 0,
                                 "page_hit3": 0, "filter_N": 0, "filter_compliant": 0,
                                 "nonfilter_N": 0, "nonfilter_hit3": 0})
        s["N"] += 1
        s["hit1"] += t["hit_at_1"]
        s["hit3"] += t["evidence_hit_at_3"]
        s["page_hit3"] += t["page_hit_at_3"]
        if t["question_filter"]:
            s["filter_N"] += 1
            s["filter_compliant"] += bool(t["filter_compliant_top3"])
        else:
            s["nonfilter_N"] += 1
            s["nonfilter_hit3"] += t["evidence_hit_at_3"]
    return {f"{k[0]}|{k[1]}": v for k, v in sorted(out.items())}
