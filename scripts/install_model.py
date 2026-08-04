#!/usr/bin/env python3
"""Rejoin + decompress chunks produced by pack_model.py.

Usage:
    python scripts/install_model.py <packed_dir> <dest_dir>

After this, point Func-Forger at the restored directory:
    forger --embed-provider sentence-transformers --embed-model <dest_dir>
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
    packed = Path(sys.argv[1])
    dest = Path(sys.argv[2])
    manifest_path = packed / "manifest.json"
    if not manifest_path.is_file():
        sys.exit(f"error: {manifest_path} not found (not a packed model dir?)")

    manifest = json.loads(manifest_path.read_text())
    data = b"".join((packed / name).read_bytes() for name in manifest["chunks"])
    if len(data) != manifest["total_bytes"]:
        sys.exit(
            f"error: size mismatch ({len(data)} != {manifest['total_bytes']}); "
            "a chunk may be missing or corrupted."
        )

    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as tar:
        tar.extractall(dest)

    print(f"Restored model ({len(data)} bytes) -> {dest}")
    print(
        "Run with:\n"
        f"  forger --embed-provider sentence-transformers --embed-model {dest}"
    )


if __name__ == "__main__":
    main()
