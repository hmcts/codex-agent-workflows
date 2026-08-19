#!/usr/bin/env python3
"""Fail closed when pull-request workflows expose GitHub OIDC credentials."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


TOP_LEVEL_KEY = re.compile(r"^(?P<key>['\"]?[A-Za-z0-9_-]+['\"]?):(?:\s*(?P<value>.*))?$")
MAPPING_KEY = re.compile(
    r"^\s*['\"]?(?P<key>[A-Za-z0-9_-]+)['\"]?\s*:\s*(?P<value>.*?)\s*$"
)
PR_EVENTS = {"pull_request", "pull_request_target"}


def strip_comment(line: str) -> str:
    """Remove YAML comments while preserving hashes inside quoted strings."""

    quote = ""
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
            continue
        if character in {"'", '"'}:
            if not quote:
                quote = character
            elif quote == character:
                quote = ""
            continue
        if character == "#" and not quote:
            return line[:index]
    return line


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def has_pull_request_trigger(lines: list[str]) -> bool:
    on_start = None
    on_value = ""
    for index, line in enumerate(lines):
        if not line or line[0].isspace():
            continue
        match = TOP_LEVEL_KEY.match(line)
        if match and unquote(match.group("key")) == "on":
            on_start = index
            on_value = (match.group("value") or "").strip()
            break

    if on_start is None:
        return False
    if any(
        re.search(rf"(^|[^A-Za-z0-9_-]){event}([^A-Za-z0-9_-]|$)", on_value)
        for event in PR_EVENTS
    ):
        return True

    for line in lines[on_start + 1 :]:
        if line and not line[0].isspace():
            break
        match = MAPPING_KEY.match(line)
        if match and unquote(match.group("key")) in PR_EVENTS:
            return True
    return False


def exposes_oidc_credentials(lines: list[str]) -> bool:
    for line in lines:
        match = MAPPING_KEY.match(line)
        if not match:
            continue
        key = unquote(match.group("key")).lower()
        value = unquote(match.group("value")).lower()
        if key == "permissions" and value == "write-all":
            return True
        if key == "permissions" and re.search(
            r"(?:^|[{,]\s*)['\"]?id-token['\"]?\s*:\s*['\"]?write['\"]?(?:\s*[,}]|$)",
            value,
        ):
            return True
        if key == "id-token" and value == "write":
            return True
    return False


def unsafe_workflows(repository_root: Path) -> list[Path]:
    workflow_dir = repository_root / ".github" / "workflows"
    unsafe = []
    for path in sorted((*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml"))):
        lines = [
            strip_comment(line).rstrip()
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        if has_pull_request_trigger(lines) and exposes_oidc_credentials(lines):
            unsafe.append(path.relative_to(repository_root))
    return unsafe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    offenders = unsafe_workflows(args.repository_root.resolve())
    if offenders:
        paths = ", ".join(str(path) for path in offenders)
        print(
            "::error title=Unsafe PR credential exposure::Autonomous Codex publication is blocked because "
            "pull-request workflow(s) grant id-token: write or permissions: write-all to jobs that may execute "
            f"generated code: {paths}. Remove the PR-time cloud credential before retrying.",
            file=sys.stderr,
        )
        return 1

    print("Caller PR workflows do not expose GitHub OIDC write credentials.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
