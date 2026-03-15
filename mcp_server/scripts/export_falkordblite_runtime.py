#!/usr/bin/env python3
"""Export FalkorDBLite's bundled runtime to a stable local service directory.

This supports a launchd-managed singleton that reuses the exact Redis/FalkorDB
runtime shipped with the currently installed falkordblite/redislite package.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import stat
import sys
from pathlib import Path


REQUIRED_BINARIES = ("redis-server", "redis-cli", "falkordb.so")


def resolve_redislite_root() -> Path:
    spec = importlib.util.find_spec("redislite")
    if spec is None or spec.origin is None:
        raise RuntimeError("Could not locate installed redislite package")
    return Path(spec.origin).resolve().parent


def ensure_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if src.suffix != ".so":
        mode = dst.stat().st_mode
        dst.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def verify_export(target_root: Path) -> None:
    missing: list[str] = []
    for name in REQUIRED_BINARIES:
        if not (target_root / "bin" / name).exists():
            missing.append(f"bin/{name}")
    dylib_dir = target_root / ".dylibs"
    if not dylib_dir.exists() or not any(dylib_dir.iterdir()):
        missing.append(".dylibs/*")
    if missing:
        raise RuntimeError(f"Export incomplete; missing: {', '.join(missing)}")


def export_runtime(target_root: Path) -> None:
    redislite_root = resolve_redislite_root()
    bin_dir = redislite_root / "bin"
    dylib_dir = redislite_root / ".dylibs"

    if not bin_dir.is_dir():
        raise RuntimeError(f"Missing bundled bin directory: {bin_dir}")
    if not dylib_dir.is_dir():
        raise RuntimeError(f"Missing bundled dylib directory: {dylib_dir}")

    for name in REQUIRED_BINARIES:
        src = bin_dir / name
        if not src.exists():
            raise RuntimeError(f"Required runtime file not found: {src}")
        ensure_copy(src, target_root / "bin" / name)

    for src in sorted(dylib_dir.iterdir()):
        if src.is_file():
            ensure_copy(src, target_root / ".dylibs" / src.name)

    verify_export(target_root)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export FalkorDBLite bundled runtime files to a stable directory."
    )
    parser.add_argument(
        "--target",
        required=True,
        help="Target directory, e.g. ~/.graphiti",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_root = Path(os.path.expanduser(args.target)).resolve()
    export_runtime(target_root)
    print(f"Exported FalkorDBLite runtime to {target_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
