#!/usr/bin/env python3
"""Generate or verify SHA-256 checksums for a packaged Lyricaod directory."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

CHECKSUM_FILE = "SHA256SUMS"
BUFFER_SIZE = 1024 * 1024


def _files(root: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.name != CHECKSUM_FILE
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(BUFFER_SIZE):
            hasher.update(chunk)
    return hasher.hexdigest()


def generate(root: Path) -> Path:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"package directory does not exist: {root}")
    output = root / CHECKSUM_FILE
    lines = [
        f"{_digest(path)}  {path.relative_to(root).as_posix()}"
        for path in _files(root)
    ]
    temporary = output.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    temporary.replace(output)
    return output


def verify(root: Path) -> list[str]:
    root = root.resolve()
    manifest = root / CHECKSUM_FILE
    if not manifest.is_file():
        return [f"missing {CHECKSUM_FILE}"]

    failures: list[str] = []
    expected_paths: set[str] = set()
    for line_number, raw_line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            expected, relative = raw_line.split("  ", 1)
        except ValueError:
            failures.append(f"invalid manifest line {line_number}")
            continue
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            failures.append(f"invalid digest on line {line_number}")
            continue
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            failures.append(f"unsafe path on line {line_number}: {relative}")
            continue
        normalized = relative_path.as_posix()
        if normalized in expected_paths:
            failures.append(f"duplicate path: {normalized}")
            continue
        expected_paths.add(normalized)
        target = root / relative_path
        if not target.is_file():
            failures.append(f"missing file: {normalized}")
            continue
        actual = _digest(target)
        if actual != expected:
            failures.append(f"checksum mismatch: {normalized}")

    actual_paths = {
        path.relative_to(root).as_posix()
        for path in _files(root)
    }
    for unlisted in sorted(actual_paths - expected_paths):
        failures.append(f"unlisted file: {unlisted}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "verify"))
    parser.add_argument("package_dir", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "generate":
            output = generate(args.package_dir)
            print(output)
            return 0
        failures = verify(args.package_dir)
    except (OSError, ValueError) as exc:
        print(f"checksum operation failed: {exc}", file=sys.stderr)
        return 2

    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print(f"verified {args.package_dir / CHECKSUM_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
