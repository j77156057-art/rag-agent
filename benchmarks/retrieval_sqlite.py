"""Synthetic benchmark for the incremental SQLite BM25 backend.

Usage:
    .venv/Scripts/python -m benchmarks.retrieval_sqlite --sizes 1000,10000,100000

The benchmark is intentionally dependency-free and does not touch the real
Chroma collections. It reports initial sync, warm query, and one-document
incremental update timings so deployments can compare hardware consistently.
"""
from __future__ import annotations

import argparse
import os
import tempfile
import time

from agent_runtime.retrieval import Document, get_lexical_index, invalidate_lexical_index


def run(size: int) -> dict[str, float | int]:
    docs = [Document(page_content=(
        f"game entity {index} movement controller jump physics inventory "
        f"scene_{index % 31} source code"),
        metadata={"_id": f"doc-{index}", "source": f"src/{index % 31}.gd"})
            for index in range(size)]
    changed = list(docs)
    changed[-1] = Document(page_content="rare benchmark replacement movement physics",
                           metadata={"_id": f"doc-{size - 1}", "source": "src/updated.gd"})
    with tempfile.TemporaryDirectory(prefix="docmind-bm25-") as directory:
        old_backend = os.environ.get("DOCMIND_LEXICAL_BACKEND")
        old_dir = os.environ.get("DOCMIND_LEXICAL_INDEX_DIR")
        os.environ["DOCMIND_LEXICAL_BACKEND"] = "sqlite"
        os.environ["DOCMIND_LEXICAL_INDEX_DIR"] = directory
        try:
            start = time.perf_counter()
            index, _ = get_lexical_index(f"bench-{size}", docs)
            build_ms = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            hits = index.query_hits("movement jump physics", 10)
            query_ms = (time.perf_counter() - start) * 1000
            invalidate_lexical_index(f"bench-{size}")
            start = time.perf_counter()
            _, meta = get_lexical_index(f"bench-{size}", changed)
            update_ms = (time.perf_counter() - start) * 1000
            return {"documents": size, "build_ms": round(build_ms, 2),
                    "query_ms": round(query_ms, 2), "update_ms": round(update_ms, 2),
                    "hits": len(hits), "updated": int((meta.get("last_delta") or {}).get("updated", 0))}
        finally:
            if old_backend is None:
                os.environ.pop("DOCMIND_LEXICAL_BACKEND", None)
            else:
                os.environ["DOCMIND_LEXICAL_BACKEND"] = old_backend
            if old_dir is None:
                os.environ.pop("DOCMIND_LEXICAL_INDEX_DIR", None)
            else:
                os.environ["DOCMIND_LEXICAL_INDEX_DIR"] = old_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="1000,10000,100000")
    args = parser.parse_args()
    for value in str(args.sizes).split(","):
        size = max(1, int(value.strip()))
        print(run(size))


if __name__ == "__main__":
    main()
