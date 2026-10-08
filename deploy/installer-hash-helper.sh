#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: installer-hash-helper.sh <approved-root> <output-tsv> <relative-path>..." >&2
  exit 2
fi

approved_root=$1
output_path=$2
shift 2

exec python3 - "$approved_root" "$output_path" "$@" <<'PY'
from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(2)


def host_path(raw: str) -> Path:
    """Accept the /mnt/<drive> form used by the cross-platform harness."""

    if os.name == "nt" and raw.startswith("/mnt/") and len(raw) > 6:
        return Path(f"{raw[5].upper()}:/{raw[7:]}")
    return Path(raw)


def validate_relative(root: Path, relative: str) -> Path:
    if not relative or "\x00" in relative:
        fail("hash helper received an empty or invalid path")
    posix = PurePosixPath(relative)
    windows = PureWindowsPath(relative)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        fail("hash helper refuses an absolute path")
    parts = posix.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        fail("hash helper refuses an unsafe relative path")
    candidate = root.joinpath(*parts)
    try:
        candidate.relative_to(root)
    except ValueError:
        fail("hash helper path escaped the approved root")
    current = root
    for part in parts:
        current = current / part
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                fail("hash helper refuses an unexpected symlink")
        except FileNotFoundError:
            fail(f"hash helper asset is missing: {relative}")
    if not candidate.is_file():
        fail(f"hash helper asset is not a regular file: {relative}")
    return candidate


def file_fingerprint(path: Path, relative: str) -> tuple[str, str]:
    before = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        fail(f"hash helper asset is not a regular file: {relative}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as error:
        fail(f"hash helper could not read asset: {relative}")
    after = os.stat(path, follow_symlinks=False)
    compared = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in compared):
        fail(f"hash helper detected an asset changing during read: {relative}")
    return digest.hexdigest(), f"{stat.S_IMODE(after.st_mode):04o}"


root = host_path(sys.argv[1]).resolve(strict=True)
if not root.is_dir():
    fail("hash helper approved root is not a directory")
output = host_path(sys.argv[2])
if output.exists() and output.is_symlink():
    fail("hash helper output must not be a symlink")
output.parent.mkdir(parents=True, exist_ok=True)
temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
try:
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("path\tsha256\tmode\n")
        for relative in sys.argv[3:]:
            path = validate_relative(root, relative)
            digest, mode = file_fingerprint(path, relative)
            handle.write(f"{relative}\t{digest}\t{mode}\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, output)
except FileExistsError:
    fail("hash helper output staging path already exists")
except OSError as error:
    fail("hash helper could not write its output")
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
PY
