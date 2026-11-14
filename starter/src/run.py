"""Pipeline entry point.

Run from the starter/ folder:
    .venv\\Scripts\\python -m src.run chunk                 # GĐ3: read, normalize, chunk A/B, write stats
    .venv\\Scripts\\python -m src.run chunk --dump DIR      # also write chunks_A/B.jsonl to DIR for inspection
    .venv\\Scripts\\python -m src.run ingest                # GĐ4: embed + upsert A, B, C into pgvector
    .venv\\Scripts\\python -m src.run ingest --force-reembed   # re-embed all chunks to measure full time
    .venv\\Scripts\\python -m src.run search "QUERY" [--chapter ch05]   # GĐ4: top-3 from all three indexes
Stage eval is added in GĐ5.
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

import os

import pypdf
from dotenv import load_dotenv

from .chunking import chunk_pages
from .embeddings import DEFAULT_MODEL, MyEmbedder
from .ingestion import load_corpus
from .models import Chunk
from .store import PgVectorStore

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


def _env() -> tuple[str, str]:
    load_dotenv(STARTER / ".env")
    return os.getenv("DATABASE_URL", ""), os.getenv("EMBEDDING_MODEL") or DEFAULT_MODEL


def _as_strategy_c(chunks_b: list[Chunk]) -> list[Chunk]:
    """Index C = same chunks/text as B under its own ids (strategy differs only at query time)."""
    out = []
    for c in chunks_b:
        meta = {**c.metadata, "strategy": "C", "derived_from": c.chunk_id}
        out.append(Chunk("C:" + c.chunk_id.split(":", 1)[1], c.document_id, c.source, c.page, c.text, meta))
    return out


def cmd_ingest(args: argparse.Namespace) -> int:
    db_url, model_name = _env()
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t_all = time.perf_counter()
    data, timings = build_chunks()
    chunks = {"A": data["chunks"]["A"], "B": data["chunks"]["B"]}
    chunks["C"] = _as_strategy_c(chunks["B"])

    t = time.perf_counter()
    embedder = MyEmbedder(model_name)
    timings["load_model_s"] = round(time.perf_counter() - t, 2)
    store = PgVectorStore(db_url, embedder.dimensions)
    versions = store.versions()
    store.create_schema()

    per_strategy, b_vectors = {}, {}
    for name in ("A", "B", "C"):
        cs = chunks[name]
        doc_id = cs[0].document_id
        existing = store.existing_hashes(name, doc_id)
        if name == "C":  # reuse B's vectors: same model + same text => identical embeddings
            vectors = [b_vectors["B:" + c.chunk_id.split(":", 1)[1]] for c in cs]
            embedded, embed_s = 0, 0.0
        else:
            todo = [i for i, c in enumerate(cs)
                    if args.force_reembed or existing.get(c.chunk_id) != c.metadata["content_hash"]]
            reuse = store.get_vectors([cs[i].chunk_id for i in range(len(cs)) if i not in set(todo)])
            t = time.perf_counter()
            new_vecs = embedder.embed_documents([cs[i].text for i in todo]) if todo else []
            embed_s = round(time.perf_counter() - t, 2)
            fresh = dict(zip(todo, new_vecs))
            vectors = [fresh[i] if i in fresh else reuse[c.chunk_id] for i, c in enumerate(cs)]
            embedded = len(todo)
        if name == "B":
            b_vectors = {c.chunk_id: v for c, v in zip(cs, vectors)}
        t = time.perf_counter()
        counts = store.upsert(name, cs, vectors)
        tokens = [embedder.count_tokens(c.text) for c in cs] if name != "C" else None
        per_strategy[name] = {
            "chunks": len(cs), "embedded_now": embedded, "embed_s": embed_s,
            "upsert_s": round(time.perf_counter() - t, 2), **counts,
            **({"tokens_max": max(tokens), "chunks_over_max_seq": sum(x > embedder.max_seq_length for x in tokens)}
               if tokens else {"note": "reuses B vectors"}),
        }
    timings["total_s"] = round(time.perf_counter() - t_all, 2)
    db_counts = store.count()

    run = {"run_at": run_at, "force_reembed": args.force_reembed, "timings": timings,
           "strategies": per_strategy, "db_counts": db_counts, "cost_usd": 0.0}
    RESULTS.mkdir(exist_ok=True)
    stats_path = RESULTS / "ingest_stats.json"
    history = []
    if stats_path.exists():
        old = json.loads(stats_path.read_text(encoding="utf-8"))
        history = old.get("runs", [])
    stats = {"embedding": embedder.info(), "vector_store": {
                "backend": "PostgreSQL + pgvector (Docker pgvector/pgvector:0.8.7-pg16)", **versions,
                "table": "chunks", "metric": "cosine (<=> operator)", "search": "exact (no ANN index)"},
             "runs": history + [run]}  # every ingest run is kept, newest last
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    line = (f"{run_at} | " + " | ".join(
        f"{s}: rows={db_counts[s]['rows']} distinct={db_counts[s]['distinct_ids']} "
        f"ins={per_strategy[s]['inserted']} upd={per_strategy[s]['updated']} "
        f"same={per_strategy[s]['unchanged']} del={per_strategy[s]['deleted']}" for s in ("A", "B", "C")))
    with open(RESULTS / "reingest_check.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")

    print(f"Model: {embedder.model_name} ({embedder.dimensions} chiều, max {embedder.max_seq_length} token) | "
          f"PostgreSQL {versions['postgresql'].split()[0]} | pgvector {versions['pgvector']}")
    print(f"{'Strategy':<9}{'Chunks':>7}{'Embed':>7}{'Ins':>6}{'Upd':>6}{'Same':>6}{'Del':>6}{'Rows DB':>9}{'Distinct':>10}")
    for s in ("A", "B", "C"):
        p = per_strategy[s]
        print(f"{s:<9}{p['chunks']:>7}{p['embedded_now']:>7}{p['inserted']:>6}{p['updated']:>6}{p['unchanged']:>6}"
              f"{p['deleted']:>6}{db_counts[s]['rows']:>9}{db_counts[s]['distinct_ids']:>10}")
    print(f"Chunk vượt {embedder.max_seq_length} token: A={per_strategy['A']['chunks_over_max_seq']}, "
          f"B={per_strategy['B']['chunks_over_max_seq']}")
    print(f"Thời gian tổng: {timings['total_s']} s | chi phí: 0 USD")
    print(f"Đã ghi: {RESULTS / 'ingest_stats.json'} và thêm 1 dòng vào {RESULTS / 'reingest_check.txt'}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    db_url, model_name = _env()
    embedder = MyEmbedder(model_name)
    store = PgVectorStore(db_url, embedder.dimensions)
    qv = embedder.embed_query(args.query)
    for name in ("A", "B", "C"):
        filters = {"chapter_id": args.chapter} if (STRATEGIES[name]["filter"] and args.chapter) else None
        print(f"=== {name} | filter={filters}")
        for r in store.search(name, qv, top_k=3, filters=filters):
            snippet = " ".join(r["text"].split())[:90]
            print(f"  {r['score']:.3f} p{r['page']:<4}{r['chapter_id']:<11}{r['chunk_id']:<28}{snippet}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.run")
    sub = parser.add_subparsers(dest="stage", required=True)
    p_chunk = sub.add_parser("chunk", help="read, normalize and chunk (strategies A and B)")
    p_chunk.add_argument("--dump", help="folder to write chunks_A.jsonl / chunks_B.jsonl for inspection")
    p_ingest = sub.add_parser("ingest", help="embed and upsert strategies A, B, C into pgvector")
    p_ingest.add_argument("--force-reembed", action="store_true",
                          help="re-embed every chunk (to measure full embedding time); rows still upsert by id")
    p_search = sub.add_parser("search", help="top-3 for one query from all three indexes")
    p_search.add_argument("query")
    p_search.add_argument("--chapter", help="chapter_id filter, applied only to strategy C")
    sub.add_parser("eval", help="implemented in GĐ5")
    args = parser.parse_args(argv)
    if args.stage == "chunk":
        return cmd_chunk(args)
    if args.stage == "ingest":
        return cmd_ingest(args)
    if args.stage == "search":
        return cmd_search(args)
    raise NotImplementedError(f"Stage '{args.stage}' chưa được hiện thực (GĐ5).")


if __name__ == "__main__":
    sys.exit(main())
