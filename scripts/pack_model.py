#!/usr/bin/env python3
"""Pack a local model directory into compressed, size-capped chunks for git.

GitHub rejects any single file over 100MB, and embedding models are typically
~90-130MB. This script tars + bzip2-compresses a model directory and splits the
archive into chunks under that limit so the model can be committed to a repo.

Usage:
    python scripts/pack_model.py <src_dir> <out_dir> [chunk_mb]

Produces <out_dir>/model.tar.bz2.part000, .part001, ... and a manifest.json.
See scripts/install_model.py to restore it.

Typical flow (offline / air-gapped install):
    1. On an online machine, save the model to a dir, e.g. with
       sentence-transformers:
           from sentence_transformers import SentenceTransformer
           SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")\
               .save("all-MiniLM-L6-v2")
    2. python scripts/pack_model.py all-MiniLM-L6-v2 models/MiniLM
    3. commit models/MiniLM/* to the repo
    4. On the target machine: python scripts/install_model.py models/MiniLM \
           ~/.forger_models/MiniLM
    5. forger --embed-provider sentence-transformers \
              --embed-model ~/.forger_models/MiniLM
"""

import io
import json
import sys
import tarfile
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    src = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2])
    chunk_mb = int(sys.argv[3]) if len(sys.argv) > 3 else 50
    if not src.is_dir():
        sys.exit(f"error: {src} is not a directory")

    out.mkdir(parents=True, exist_ok=True)
    # Clean any previous chunks so stale parts don't linger.
    for old in out.glob("model.tar.bz2.part*"):
        old.unlink()

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=path.relative_to(src))
    data = buf.getvalue()

    chunk = chunk_mb * 1024 * 1024
    parts = []
    for index in range(0, len(data), chunk):
        name = f"model.tar.bz2.part{len(parts):03d}"
        (out / name).write_bytes(data[index : index + chunk])
        parts.append(name)

    (out / "manifest.json").write_text(
        json.dumps(
            {"source": str(src), "total_bytes": len(data), "chunks": parts, "chunk_mb": chunk_mb},
            indent=2,
        )
    )
    print(f"Packed {len(data)} bytes -> {len(parts)} chunk(s) in {out}")


if __name__ == "__main__":
    main()
