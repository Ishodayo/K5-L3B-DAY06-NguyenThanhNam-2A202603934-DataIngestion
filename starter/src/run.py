"""Pipeline entry point.

Run from the starter/ folder:
    .venv\\Scripts\\python -m src.run chunk                 # GĐ3: read, normalize, chunk A/B, write stats
    .venv\\Scripts\\python -m src.run chunk --dump DIR      # also write chunks_A/B.jsonl to DIR for inspection
Later stages (ingest, eval) are added in GĐ4–5.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pypdf

from .chunking import chunk_pages
from .ingestion import load_corpus

STARTER = Path(__file__).resolve().parents[1]
REPO = STARTER.parent
RESULTS = STARTER / "results"

# Single source of truth for the three configurations (same corpus, model, benchmark).
STRATEGIES = {
    "A": {"chunking": "fixed_size", "params": {"size": 500, "overlap": 50}, "filter": False},
    "B": {"chunking": "by_paragraph", "params": {"max_size": 1200}, "filter": False},
    "C": {"chunking": "by_paragraph", "params": {"max_size": 1200}, "filter": True},  # = B + chapter_id filter
}
CHUNK_SOURCES = {"A": "A", "B": "B", "C": "B"}  # C reuses B's chunks


def _length_stats(lengths: list[int]) -> dict:
    return {
        "count": len(lengths), "min": min(lengths), "max": max(lengths),
        "mean": round(statistics.mean(lengths), 1), "median": statistics.median(lengths),
        "p10": sorted(lengths)[len(lengths) // 10], "p90": sorted(lengths)[len(lengths) * 9 // 10],
        "total_chars": sum(lengths),
    }


def build_chunks() -> tuple[dict, dict]:
    t0 = time.perf_counter()
    pages, report, _ = load_corpus(REPO)
    load_s = time.perf_counter() - t0
    chunks, timings = {}, {"load_and_normalize_s": round(load_s, 2)}
    for name in ("A", "B"):
        t = time.perf_counter()
        chunks[name] = chunk_pages(pages, name, **STRATEGIES[name]["params"])
        timings[f"chunk_{name}_s"] = round(time.perf_counter() - t, 3)
    return {"pages": pages, "report": report, "chunks": chunks}, timings


def cmd_chunk(args: argparse.Namespace) -> int:
    data, timings = build_chunks()
    report, chunks = data["report"], data["chunks"]
    RESULTS.mkdir(exist_ok=True)

    log_lines = [
        f"run_at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"pypdf: {pypdf.__version__}",
        f"physical_pages: {report.physical_pages}",
        f"counted_pages (manifest): {report.counted_pages}",
        f"loaded_pages: {report.loaded_pages}",
        f"empty_pages: {report.empty_pages}",
        f"error_pages: {report.error_pages}",
        f"normalized_chars_total: {report.chars_total}",
    ]
    (RESULTS / "ingest_log.txt").write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    stats = {"run_at": log_lines[0].split(": ", 1)[1], "pypdf": pypdf.__version__,
             "pages_loaded": report.loaded_pages, "timings": timings, "strategies": {}}
    for name, cs in chunks.items():
        ids = [c.chunk_id for c in cs]
        if len(ids) != len(set(ids)):
            raise SystemExit(f"Duplicate chunk_id in strategy {name}")
        s = _length_stats([len(c.text) for c in cs])
        s["config"] = STRATEGIES[name]
        s["pages_covered"] = len({c.page for c in cs})
        if name == "B":
            s["chunks_with_section"] = sum(1 for c in cs if c.metadata.get("section"))
        stats["strategies"][name] = s
    stats["strategies"]["C"] = {"config": STRATEGIES["C"], "note": "reuses strategy B chunks"}
    (RESULTS / "chunk_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.dump:
        out = Path(args.dump)
        out.mkdir(parents=True, exist_ok=True)
        for name, cs in chunks.items():
            with open(out / f"chunks_{name}.jsonl", "w", encoding="utf-8") as f:
                for c in cs:
                    f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")

    print(f"Trang đọc được: {report.loaded_pages}/{report.counted_pages} | rỗng: {len(report.empty_pages)} | lỗi: {len(report.error_pages)}")
    print(f"{'Strategy':<9}{'Chunks':>7}{'Min':>6}{'Median':>8}{'Mean':>8}{'Max':>6}{'Trang':>7}")
    for name in ("A", "B"):
        s = stats["strategies"][name]
        print(f"{name:<9}{s['count']:>7}{s['min']:>6}{s['median']:>8}{s['mean']:>8}{s['max']:>6}{s['pages_covered']:>7}")
    print(f"B có section: {stats['strategies']['B']['chunks_with_section']}/{stats['strategies']['B']['count']}")
    print(f"Đã ghi: {RESULTS / 'chunk_stats.json'} và {RESULTS / 'ingest_log.txt'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.run")
    sub = parser.add_subparsers(dest="stage", required=True)
    p_chunk = sub.add_parser("chunk", help="read, normalize and chunk (strategies A and B)")
    p_chunk.add_argument("--dump", help="folder to write chunks_A.jsonl / chunks_B.jsonl for inspection")
    for stage in ("ingest", "eval"):
        sub.add_parser(stage, help="implemented in a later stage")
    args = parser.parse_args(argv)
    if args.stage == "chunk":
        return cmd_chunk(args)
    raise NotImplementedError(f"Stage '{args.stage}' chưa được hiện thực (GĐ4–5).")


if __name__ == "__main__":
    sys.exit(main())
