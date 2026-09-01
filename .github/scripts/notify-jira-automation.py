#!/usr/bin/env python3
"""Notify the Azure webhook about a Codex PR or terminal run result."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _required_env(name: str) -> str:
    value = _env(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _run_url() -> str:
    server_url = _env("GITHUB_SERVER_URL", "https://github.com")
    repository = _env("GITHUB_REPOSITORY")
    run_id = _env("GITHUB_RUN_ID")

    if repository and run_id:
        return f"{server_url}/{repository}/actions/runs/{run_id}"

    return ""


def _post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "codex-jira-dispatch",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Azure Jira PR notification failed with HTTP {exc.code}: {details}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Azure Jira PR notification failed: {exc.reason}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pr-url")
    mode.add_argument("--status", choices=("blocked", "failed", "no-changes"))
    parser.add_argument("--branch-name")
    parser.add_argument("--commit-sha")
    parser.add_argument("--draft", choices=("true", "false"), default="false")
    parser.add_argument("--verification-status", default="passed")
    parser.add_argument("--message")
    return parser


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload = {
        "issueKey": _required_env("ISSUE_KEY"),
        "issueUrl": _required_env("ISSUE_URL"),
        "repository": _required_env("GITHUB_REPOSITORY"),
        "runUrl": _run_url(),
    }

    if args.status:
        if not args.message:
            raise RuntimeError("--message is required with --status")
        payload.update(
            {
                "status": args.status,
                "message": args.message.strip(),
            }
        )
        return payload

    if not args.branch_name or not args.commit_sha:
        raise RuntimeError("--branch-name and --commit-sha are required with --pr-url")
    payload.update(
        {
            "prUrl": args.pr_url,
            "prTitle": f"{payload['issueKey']}: {_required_env('ISSUE_SUMMARY')}",
            "branchName": args.branch_name,
            "commitSha": args.commit_sha,
            "isDraft": args.draft == "true",
            "verificationStatus": args.verification_status,
            "actor": _env("GITHUB_ACTOR"),
        }
    )
    return payload


def main() -> int:
    args = build_parser().parse_args()

    notify_url = _env("CODEX_JIRA_PR_NOTIFY_URL")
    if not notify_url:
        print(
            "::warning::CODEX_JIRA_PR_NOTIFY_URL is not configured; "
            "skipping Jira Automation notification."
        )
        return 0

    payload = build_payload(args)

    timeout = int(_env("CODEX_JIRA_PR_NOTIFY_TIMEOUT_SECONDS", "10"))
    result = _post_json(notify_url, payload, timeout)
    print(f"Azure Jira notification accepted for {payload['issueKey']}: {result}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(1)
