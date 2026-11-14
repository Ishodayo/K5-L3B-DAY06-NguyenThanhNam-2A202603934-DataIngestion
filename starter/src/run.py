"""Pipeline entry point.

Run from the starter/ folder:
    .venv\\Scripts\\python -m src.run chunk                 # GĐ3: read, normalize, chunk A/B, write stats
    .venv\\Scripts\\python -m src.run chunk --dump DIR      # also write chunks_A/B.jsonl to DIR for inspection
    .venv\\Scripts\\python -m src.run ingest                # GĐ4: embed + upsert A, B, C into pgvector
    .venv\\Scripts\\python -m src.run ingest --force-reembed   # re-embed all chunks to measure full time
    .venv\\Scripts\\python -m src.run search "QUERY" [--chapter ch05]   # GĐ4: top-3 from all three indexes
    .venv\\Scripts\\python -m src.run eval --split dev      # GĐ5: top-3 traces + Hit@1/Hit@3 on dev
    .venv\\Scripts\\python -m src.run select                # GĐ5: choose strategy from DEV with pre-registered rule
    .venv\\Scripts\\python -m src.run eval --split test     # GĐ5: run test ONCE (needs selection.json)
    .venv\\Scripts\\python -m src.run audit                 # GĐ5: create manual audit sheets in results/audit/
    .venv\\Scripts\\python -m src.run audit --summary       # GĐ5: aggregate the labels filled by the grader
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
from .evaluation import evaluate_query, summarize
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


def _load_questions() -> list[dict]:
    path = REPO / "benchmark" / "questions.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _read_traces(name: str) -> list[dict]:
    path = RESULTS / f"traces_{name}.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _write_metrics() -> dict:
    traces = [t for s in STRATEGIES for t in _read_traces(s)]
    summary = summarize(traces)
    cols = ["strategy", "split", "hit1", "N", "hit3", "page_hit3", "filter_compliant", "filter_N",
            "nonfilter_hit3", "nonfilter_N"]
    lines = [",".join(cols + ["hit1_str", "hit3_str"])]
    for s in summary.values():
        lines.append(",".join([str(s[c]) for c in cols] + [f"{s['hit1']}/{s['N']}", f"{s['hit3']}/{s['N']}"]))
    (RESULTS / "metrics.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def cmd_eval(args: argparse.Namespace) -> int:
    split = args.split
    selection_path = RESULTS / "selection.json"
    if split == "test":
        if not selection_path.exists():
            raise SystemExit("Chưa có results/selection.json: chạy 'eval --split dev' rồi 'select' trước khi chạy test.")
        sel = json.loads(selection_path.read_text(encoding="utf-8"))
        if sel.get("test_runs") and not args.allow_rerun:
            raise SystemExit("Test đã chạy rồi (test chỉ chạy một lần). Dùng --allow-rerun nếu buộc phải chạy lại "
                             "và ghi lý do vào báo cáo.")
    db_url, model_name = _env()
    embedder = MyEmbedder(model_name)
    store = PgVectorStore(db_url, embedder.dimensions)
    questions = [q for q in _load_questions() if q["split"] == split]
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t0 = time.perf_counter()
    qvecs = {q["id"]: embedder.embed_query(q["question"]) for q in questions}  # same query vector for A/B/C
    for name, cfg in STRATEGIES.items():
        traces = [t for t in _read_traces(name) if t["split"] != split]  # replace this split only
        for q in questions:
            applied = q["filter"] if (cfg["filter"] and q.get("filter")) else None
            hits = store.search(name, qvecs[q["id"]], top_k=3, filters=applied)
            rec = evaluate_query(q, hits, applied)
            rec.update({"strategy": name, "run_at": run_at, "embedding_model": embedder.model_name})
            traces.append(rec)
        traces.sort(key=lambda t: (t["split"], t["id"]))
        with open(RESULTS / f"traces_{name}.jsonl", "w", encoding="utf-8") as f:
            for t in traces:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
    elapsed = round(time.perf_counter() - t0, 2)
    summary = _write_metrics()
    if split == "test":
        sel = json.loads(selection_path.read_text(encoding="utf-8"))
        sel.setdefault("test_runs", []).append(run_at)
        selection_path.write_text(json.dumps(sel, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Split: {split} | {len(questions)} câu | model {embedder.model_name} | {elapsed} s")
    print(f"{'Strategy':<9}{'Hit@1':>8}{'Hit@3':>8}{'PageHit@3':>11}{'Filter ok':>11}")
    for name in STRATEGIES:
        s = summary[f"{name}|{split}"]
        filt = f"{s['filter_compliant']}/{s['filter_N']}" if s["filter_N"] else "N/A"
        print(f"{name:<9}{s['hit1']:>4}/{s['N']:<3}{s['hit3']:>4}/{s['N']:<3}{s['page_hit3']:>7}/{s['N']:<3}{filt:>11}")
    for name in STRATEGIES:
        miss = [t["id"] for t in _read_traces(name) if t["split"] == split and not t["evidence_hit_at_3"]]
        print(f"  {name} trượt Hit@3: {miss}")
    print(f"Đã ghi: results\\traces_A/B/C.jsonl, results\\metrics.csv")
    return 0


def cmd_select(args: argparse.Namespace) -> int:
    """Apply the selection rule pre-registered in benchmark/README.md, using DEV results only."""
    summary = _write_metrics()
    dev = {name: summary.get(f"{name}|dev") for name in STRATEGIES}
    if any(v is None for v in dev.values()):
        raise SystemExit("Thiếu kết quả dev: chạy 'eval --split dev' trước.")
    chunks = {"A": 929, "B": 528, "C": 528}
    try:
        stats = json.loads((RESULTS / "chunk_stats.json").read_text(encoding="utf-8"))
        chunks = {"A": stats["strategies"]["A"]["count"], "B": stats["strategies"]["B"]["count"],
                  "C": stats["strategies"]["B"]["count"]}
    except Exception:  # noqa: BLE001
        pass
    log = []
    best3 = max(v["hit3"] for v in dev.values())
    cands = [n for n, v in dev.items() if v["hit3"] >= best3 - 1]
    log.append(f"1) Hit@3 dev: {{{', '.join(f'{n}: {v['hit3']}/{v['N']}' for n, v in dev.items())}}}; "
               f"max={best3}; ứng viên (chênh <= 1 câu): {cands}")
    best1 = max(dev[n]["hit1"] for n in cands)
    cands2 = [n for n in cands if dev[n]["hit1"] == best1]
    log.append(f"2) Hit@1 dev trong ứng viên: {{{', '.join(f'{n}: {dev[n]['hit1']}' for n in cands)}}}; "
               f"còn lại: {cands2}")
    # 3) tie-break: fewer chunks first, then no filter before filter (simpler)
    order = sorted(cands2, key=lambda n: (chunks[n], STRATEGIES[n]["filter"], n))
    winner = order[0]
    log.append(f"3) Hòa thì ưu tiên ít chunk / không filter: thứ tự {order} -> {winner}")
    if winner in ("B", "C"):
        b, c = dev["B"], dev["C"]
        rule4 = c["nonfilter_hit3"] >= b["nonfilter_hit3"] and c["filter_compliant"] == c["filter_N"] > 0
        log.append(f"4) B vs C: C non-filter Hit@3 {c['nonfilter_hit3']}/{c['nonfilter_N']} vs "
                   f"B {b['nonfilter_hit3']}/{b['nonfilter_N']}; C tuân thủ filter {c['filter_compliant']}/"
                   f"{c['filter_N']} -> {'chọn C' if rule4 else 'giữ ' + winner}")
        if rule4:
            winner = "C"
    sel = {"selected_strategy": winner, "decided_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "based_on": "dev only (benchmark/README.md, 'Tiêu chí chọn chiến lược')", "steps": log,
           "dev_summary": dev}
    path = RESULTS / "selection.json"
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("test_runs"):
            raise SystemExit("Đã chạy test sau lần chọn trước; không chọn lại để tránh dùng test để tinh chỉnh.")
    path.write_text(json.dumps(sel, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n".join(log))
    print(f"==> Chiến lược được chọn (từ dev): {winner}. Đã ghi results\\selection.json")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    from .audit import make_sheets, summarize_sheets

    if args.summary:
        summary = summarize_sheets(RESULTS)
        (RESULTS / "audit" / "audit_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                                             encoding="utf-8")
        for s, v in summary.items():
            c, m, g = v["coherence"], v["metadata"], v["grounding"]
            print(f"{s}: coherence {c['mean_of_2']}/2 ({c['labelled']}/{c['N']} đã chấm) | "
                  f"metadata đủ&đúng {m['complete_and_correct']}/{m['N']} ({m['confirmed']} đã xác nhận; "
                  f"auto {m['auto_all_ok']}/{m['N']}) | filter {m['filter_compliant_questions']}/{m['filter_questions']} | "
                  f"grounding 0/1/2 = {g['count_0']}/{g['count_1']}/{g['count_2']}, TB {g['mean_of_2']}/2 "
                  f"({g['labelled']}/{g['N']} đã chấm)")
        print("Đã ghi results\\audit\\audit_summary.json")
        return 0
    if (RESULTS / "audit" / "coherence.csv").exists() and not args.overwrite:
        raise SystemExit("results\\audit\\ đã có bảng chấm; dùng --overwrite nếu muốn tạo lại (sẽ mất nhãn đã điền).")
    pages, _, _ = load_corpus(REPO)
    chunks = {n: chunk_pages(pages, n, **STRATEGIES[n]["params"]) for n in ("A", "B")}
    counts = make_sheets(REPO, RESULTS, chunks)
    print(f"Đã tạo results\\audit\\coherence.csv ({counts['coherence_rows']} dòng), metadata.csv "
          f"({counts['metadata_rows']} dòng), grounding.csv ({counts['grounding_rows']} dòng). "
          f"Điền cột score/confirmed rồi chạy 'audit --summary'.")
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
    p_eval = sub.add_parser("eval", help="run the benchmark split on A, B, C and save traces/metrics")
    p_eval.add_argument("--split", choices=["dev", "test"], required=True)
    p_eval.add_argument("--allow-rerun", action="store_true", help="allow re-running test (must be reported)")
    sub.add_parser("select", help="choose the strategy from dev results with the pre-registered rule")
    p_audit = sub.add_parser("audit", help="create manual audit sheets (or --summary to aggregate labels)")
    p_audit.add_argument("--summary", action="store_true")
    p_audit.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    handlers = {"chunk": cmd_chunk, "ingest": cmd_ingest, "search": cmd_search, "eval": cmd_eval,
                "select": cmd_select, "audit": cmd_audit}
    return handlers[args.stage](args)


if __name__ == "__main__":
    sys.exit(main())
