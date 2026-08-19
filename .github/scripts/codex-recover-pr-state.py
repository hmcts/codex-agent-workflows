#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


class RecoveryError(RuntimeError):
    pass


def require_base_sha(base_sha: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise RecoveryError(
            "Expected base SHA is missing or is not a lowercase 40-character commit ID"
        )


def gh_environment() -> dict[str, str]:
    required = ("HOME", "PATH", "GH_TOKEN")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RecoveryError(
            "Missing required environment variables: " + ", ".join(missing)
        )
    return {
        "HOME": os.environ["HOME"],
        "PATH": os.environ["PATH"],
        "GH_TOKEN": os.environ["GH_TOKEN"],
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", os.environ.get("LANG", "C.UTF-8")),
        "TERM": os.environ.get("TERM", "xterm"),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }


def fetch_json(command: list[str], description: str) -> object:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=gh_environment(),
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"gh exited with status {completed.returncode}"
        raise RecoveryError(f"Unable to query {description}: {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RecoveryError(f"Invalid JSON returned for {description}: {exc}") from exc


def fetch_open_pull_requests(repository: str) -> list[dict[str, Any]]:
    pages = fetch_json(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repository}/pulls?state=open&per_page=100",
        ],
        "open pull requests",
    )
    if not isinstance(pages, list):
        raise RecoveryError("Open pull request page envelope is not an array")

    pull_requests: list[dict[str, Any]] = []
    for page_number, page in enumerate(pages, start=1):
        if not isinstance(page, list):
            raise RecoveryError(
                f"Open pull request page {page_number} is not an array"
            )
        for item_number, pull_request in enumerate(page, start=1):
            if not isinstance(pull_request, dict):
                raise RecoveryError(
                    f"Open pull request item {item_number} on page {page_number} "
                    "is not an object"
                )
            pull_requests.append(pull_request)
    return pull_requests


def pull_request_number(pr_url: str) -> int:
    match = re.search(r"/pull/([1-9][0-9]*)(?:/|$)", pr_url)
    if not match:
        raise RecoveryError("Created pull request URL does not contain a valid number")
    return int(match.group(1))


def fetch_pull_request_by_number(
    repository: str, number: int
) -> dict[str, Any]:
    pull_request = fetch_json(
        ["gh", "api", f"repos/{repository}/pulls/{number}"],
        f"pull request #{number}",
    )
    if not isinstance(pull_request, dict):
        raise RecoveryError(f"Pull request #{number} response is not an object")
    return pull_request


def fetch_pull_request(repository: str, pr_url: str) -> dict[str, Any]:
    return fetch_pull_request_by_number(repository, pull_request_number(pr_url))


def nested_value(value: object, *keys: str) -> object:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def mismatch_reasons(
    pull_request: dict[str, Any],
    *,
    repository: str,
    base_ref: str,
    base_sha: str,
    head_ref: str,
    head_sha: str,
    draft: bool,
) -> list[str]:
    expected = {
        "state": "open",
        "base repository": repository,
        "base ref": base_ref,
        "base SHA": base_sha,
        "head repository": repository,
        "head ref": head_ref,
        "head SHA": head_sha,
        "draft state": draft,
    }
    actual = {
        "state": pull_request.get("state"),
        "base repository": nested_value(pull_request, "base", "repo", "full_name"),
        "base ref": nested_value(pull_request, "base", "ref"),
        "base SHA": nested_value(pull_request, "base", "sha"),
        "head repository": nested_value(pull_request, "head", "repo", "full_name"),
        "head ref": nested_value(pull_request, "head", "ref"),
        "head SHA": nested_value(pull_request, "head", "sha"),
        "draft state": pull_request.get("draft"),
    }
    return [
        f"{field} is {actual[field]!r}, expected {expected_value!r}"
        for field, expected_value in expected.items()
        if actual[field] != expected_value
    ]


def validate_pull_request(
    pull_request: dict[str, Any],
    *,
    repository: str,
    base_ref: str,
    base_sha: str,
    head_ref: str,
    head_sha: str,
    draft: bool,
) -> dict[str, object]:
    require_base_sha(base_sha)
    reasons = mismatch_reasons(
        pull_request,
        repository=repository,
        base_ref=base_ref,
        base_sha=base_sha,
        head_ref=head_ref,
        head_sha=head_sha,
        draft=draft,
    )
    if reasons:
        number = pull_request.get("number")
        label = f"#{number}" if isinstance(number, int) else "candidate"
        raise RecoveryError(
            f"Pull request {label} does not match exact recovery state: "
            + "; ".join(reasons)
        )

    number = pull_request.get("number")
    url = pull_request.get("html_url")
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or number <= 0
        or not isinstance(url, str)
        or not url
        or any(character in url for character in "\r\n")
    ):
        raise RecoveryError("Validated pull request has invalid number or URL metadata")

    return {
        "found": True,
        "branch_name": head_ref,
        "commit_sha": head_sha,
        "pr_url": url,
        "pr_number": number,
    }


def recover_pull_request(
    pull_requests: list[dict[str, Any]],
    *,
    repository: str,
    base_ref: str,
    base_sha: str,
    head_ref: str,
    head_sha: str,
    draft: bool,
    allow_missing: bool,
) -> dict[str, object]:
    require_base_sha(base_sha)
    related = [
        pull_request
        for pull_request in pull_requests
        if nested_value(pull_request, "head", "ref") == head_ref
    ]
    if len(related) > 1:
        numbers = ", ".join(str(item.get("number", "unknown")) for item in related)
        raise RecoveryError(
            f"Multiple open pull requests use expected head ref {head_ref}: {numbers}"
        )
    if not related:
        if allow_missing:
            return {"found": False}
        raise RecoveryError(
            f"No open pull request has the exact expected recovery state for {head_ref}"
        )

    return validate_pull_request(
        related[0],
        repository=repository,
        base_ref=base_ref,
        base_sha=base_sha,
        head_ref=head_ref,
        head_sha=head_sha,
        draft=draft,
    )


def recover_fresh_pull_request(
    pull_requests: list[dict[str, Any]],
    *,
    repository: str,
    base_ref: str,
    base_sha: str,
    head_ref: str,
    head_sha: str,
    draft: bool,
    allow_missing: bool,
) -> dict[str, object]:
    discovered = recover_pull_request(
        pull_requests,
        repository=repository,
        base_ref=base_ref,
        base_sha=base_sha,
        head_ref=head_ref,
        head_sha=head_sha,
        draft=draft,
        allow_missing=allow_missing,
    )
    if not discovered["found"]:
        return discovered

    selected_number = discovered["pr_number"]
    if not isinstance(selected_number, int) or isinstance(selected_number, bool):
        raise RecoveryError("Discovered pull request has an invalid number")
    fresh_pull_request = fetch_pull_request_by_number(repository, selected_number)
    fresh = validate_pull_request(
        fresh_pull_request,
        repository=repository,
        base_ref=base_ref,
        base_sha=base_sha,
        head_ref=head_ref,
        head_sha=head_sha,
        draft=draft,
    )
    if fresh["pr_number"] != selected_number:
        raise RecoveryError(
            "Fresh pull request response does not match the discovered number"
        )
    return fresh


def write_output(path: Path, state: dict[str, object], append: bool) -> None:
    values = [
        ("found", "true" if state["found"] else "false"),
    ]
    if state["found"]:
        values.extend(
            (key, str(state[key]))
            for key in ("branch_name", "commit_sha", "pr_url", "pr_number")
        )
    content = "".join(f"{key}={value}\n" for key, value in values)

    path.parent.mkdir(parents=True, exist_ok=True)
    if append:
        with path.open("a", encoding="utf-8") as output_file:
            output_file.write(content)
        return

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as output_file:
        temporary_path = Path(output_file.name)
        output_file.write(content)
    os.replace(temporary_path, path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate exact pull request state for publication recovery."
    )
    parser.add_argument("--repository", required=True)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--draft", choices=("true", "false"), required=True)
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--pr-url")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--append-output", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    draft = args.draft == "true"
    try:
        if args.pr_url:
            pull_request = fetch_pull_request(args.repository, args.pr_url)
            state = validate_pull_request(
                pull_request,
                repository=args.repository,
                base_ref=args.base_ref,
                base_sha=args.base_sha,
                head_ref=args.head_ref,
                head_sha=args.head_sha,
                draft=draft,
            )
        else:
            state = recover_fresh_pull_request(
                fetch_open_pull_requests(args.repository),
                repository=args.repository,
                base_ref=args.base_ref,
                base_sha=args.base_sha,
                head_ref=args.head_ref,
                head_sha=args.head_sha,
                draft=draft,
                allow_missing=args.allow_missing,
            )
        write_output(args.output, state, args.append_output)
    except (OSError, RecoveryError) as exc:
        print(f"Unable to recover exact pull request state: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
