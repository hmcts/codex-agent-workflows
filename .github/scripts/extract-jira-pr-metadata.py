#!/usr/bin/env python3
"""Extract trusted Jira metadata from a generated pull request response."""

from __future__ import annotations

import json
import re
import sys


JIRA_LINK = re.compile(
    r"See \[(?P<label>[A-Z][A-Z0-9]+-[1-9][0-9]*)\]"
    r"\((?P<url>https://tools\.hmcts\.net/jira/browse/"
    r"(?P<url_key>[A-Z][A-Z0-9]+-[1-9][0-9]*))\)"
)


def extract_metadata(pull_request: dict[str, object]) -> tuple[str, str]:
    title = pull_request.get("title")
    body = pull_request.get("body")
    if not isinstance(title, str) or not isinstance(body, str):
        raise ValueError("Pull request title and body are required for Jira notification")

    matches = list(JIRA_LINK.finditer(body))
    if len(matches) != 1:
        raise ValueError("Expected exactly one trusted Jira link in the pull request body")

    match = matches[0]
    issue_key = match.group("label")
    if match.group("url_key") != issue_key:
        raise ValueError("Jira link label and URL key do not match")
    if not title.startswith(f"{issue_key}:"):
        raise ValueError("Pull request title does not match the Jira issue key")

    return issue_key, match.group("url")


def main() -> int:
    pull_request = json.load(sys.stdin)
    if not isinstance(pull_request, dict):
        raise ValueError("Pull request response must be a JSON object")
    issue_key, issue_url = extract_metadata(pull_request)
    print(f"{issue_key}\t{issue_url}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (json.JSONDecodeError, ValueError) as error:
        print(f"Unable to derive Jira metadata from pull request: {error}", file=sys.stderr)
        raise SystemExit(1)
