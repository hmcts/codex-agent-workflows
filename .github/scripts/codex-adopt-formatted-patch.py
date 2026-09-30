#!/usr/bin/env python3
"""Adopt the formatted patch that credential-free verification rebuilt.

Verification runs the repository's own tooling, so the formatted patch it
uploads is untrusted data. It replaces the Codex patch only if its hash is
the one verification recorded and it changes only files the Codex patch
changed, counting rename and copy sources. Formatting can return a file to
its original content, so a subset is accepted. The publish job then checks
the formatted tree with the credential safety gate before any token is minted.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import shutil
import sys
from pathlib import Path


COLLECTOR = Path(__file__).with_name("collect-codex-patch-result.py")


class AdoptionError(RuntimeError):
    pass


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_codex_patch_result", COLLECTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise AdoptionError(f"{label} is missing or is not a regular file: {path}")
    return path


def recorded_patch_sha(verification_env: Path) -> str:
    values = {}
    for line in regular_file(verification_env, "verification.env").read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    sha = values.get("patch_sha", "")
    if len(sha) != 64 or any(character not in "0123456789abcdef" for character in sha):
        raise AdoptionError("verification.env does not record a patch_sha")
    return sha


def patch_paths(collector, patch: Path) -> set[str]:
    content = patch.read_bytes()
    if len(content) > collector.MAX_PATCH_BYTES:
        raise AdoptionError(f"{patch.name} exceeds the 5 MiB patch limit")
    if b"\0" in content:
        raise AdoptionError(f"{patch.name} contains a NUL byte")
    try:
        paths = collector.patch_paths(content.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise AdoptionError(f"{patch.name} is not UTF-8 text: {error}") from error
    except ValueError as error:
        raise AdoptionError(f"{patch.name} is not a patch that can be adopted: {error}") from error
    if not paths:
        raise AdoptionError(f"{patch.name} changes no files")
    return paths


def adopt(output_dir: Path, verified_dir: Path) -> set[str]:
    codex_patch = regular_file(output_dir / "changes.patch", "Codex patch")
    formatted_patch = regular_file(verified_dir / "changes.patch", "formatted patch")
    expected_sha = recorded_patch_sha(verified_dir / "verification.env")
    actual_sha = hashlib.sha256(formatted_patch.read_bytes()).hexdigest()
    if actual_sha != expected_sha:
        raise AdoptionError("the formatted patch does not match the hash verification recorded")

    collector = load_collector()
    codex_paths = patch_paths(collector, codex_patch)
    formatted_paths = patch_paths(collector, formatted_patch)
    outside = sorted(formatted_paths - codex_paths)
    if outside:
        raise AdoptionError(
            "the formatted patch changes files the Codex patch does not: "
            + ", ".join(outside)
        )

    shutil.copyfile(formatted_patch, codex_patch)
    return formatted_paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verified-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        paths = adopt(args.output_dir, args.verified_dir)
    except AdoptionError as error:
        print(f"::error title=Formatted patch refused::{error}", file=sys.stderr)
        return 1
    print(f"Adopted the formatted patch for {len(paths)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
