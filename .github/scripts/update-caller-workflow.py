#!/usr/bin/env python3
"""Update and validate a Juror caller's shared-workflow contract."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
USES_PATTERN = re.compile(
    r"(?m)^(?P<indent>[ \t]*)uses:\s*"
    r"hmcts/codex-agent-workflows/\.github/workflows/"
    r"(?P<workflow>codex-(?:implement|review-feedback)\.yml)@"
    r"(?P<sha>[0-9a-f]{40})\s*$"
)

WORKFLOW_CONTRACTS = {
    "codex_jira_dispatch.yml": {
        "shared_workflow": "codex-implement.yml",
        "inputs": (
            "issueKey",
            "summary",
            "description",
            "status",
            "assignee",
            "issueUrl",
            "initiatorDisplayName",
            "runner_label",
            "github_app_client_id",
            "sonar_host_url",
            "sonar_project_key",
        ),
    },
    "codex_pr_review.yml": {
        "shared_workflow": "codex-review-feedback.yml",
        "inputs": (
            "runner_label",
            "github_app_client_id",
            "sonar_host_url",
            "sonar_project_key",
        ),
    },
}

REQUIRED_SECRETS = (
    "CODEX_OPENAI_API_KEY",
    "CODEX_GITHUB_APP_PRIVATE_KEY",
    "CODEX_JIRA_PR_NOTIFY_URL",
    "CODEX_SONAR_TOKEN",
)


class CallerContractError(ValueError):
    """Raised when a caller cannot be migrated safely."""


def _job_bounds(lines: list[str], uses_line: int, uses_indent: int) -> tuple[int, int]:
    expected_job_indent = uses_indent - 2
    if expected_job_indent < 0:
        raise CallerContractError("shared workflow reference is not inside a reusable job")

    job_start = None
    for index in range(uses_line - 1, -1, -1):
        match = re.match(r"^(\s*)[A-Za-z0-9_-]+:\s*$", lines[index])
        if match and len(match.group(1)) == expected_job_indent:
            job_start = index
            break
    if job_start is None:
        raise CallerContractError("unable to locate the reusable job containing uses:")

    job_end = job_start + 1
    while job_end < len(lines):
        candidate = lines[job_end]
        if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= expected_job_indent:
            break
        job_end += 1
    return job_start, job_end


def _block_bounds(
    lines: list[str], heading: str, start: int, end_limit: int, expected_indent: int
) -> tuple[int, int, int]:
    for index in range(start, end_limit):
        match = re.match(rf"^(\s*){re.escape(heading)}:\s*$", lines[index])
        if not match:
            continue
        indent = len(match.group(1))
        if indent != expected_indent:
            continue
        end = index + 1
        while end < end_limit:
            candidate = lines[end]
            if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= indent:
                break
            end += 1
        return index, end, indent
    raise CallerContractError(f"missing {heading}: block")


def _mapping_keys(lines: list[str], start: int, end: int, indent: int) -> set[str]:
    keys: set[str] = set()
    item_indent = indent + 2
    pattern = re.compile(rf"^\s{{{item_indent}}}([A-Za-z0-9_]+):")
    for line in lines[start + 1 : end]:
        match = pattern.match(line)
        if match:
            keys.add(match.group(1))
    return keys


def _mapping_values(lines: list[str], start: int, end: int, indent: int) -> dict[str, str]:
    values: dict[str, str] = {}
    item_indent = indent + 2
    pattern = re.compile(rf"^\s{{{item_indent}}}([A-Za-z0-9_]+):\s*(.*?)\s*$")
    for line in lines[start + 1 : end]:
        match = pattern.match(line.rstrip("\n"))
        if match:
            values[match.group(1)] = match.group(2)
    return values


def update_caller(content: str, filename: str, release_sha: str) -> str:
    if filename not in WORKFLOW_CONTRACTS:
        raise CallerContractError(f"unsupported caller workflow: {filename}")
    if not SHA_PATTERN.fullmatch(release_sha):
        raise CallerContractError("release SHA must be 40 lowercase hexadecimal characters")

    matches = list(USES_PATTERN.finditer(content))
    if len(matches) != 1:
        raise CallerContractError("caller must contain exactly one supported shared workflow reference")

    contract = WORKFLOW_CONTRACTS[filename]
    if matches[0].group("workflow") != contract["shared_workflow"]:
        raise CallerContractError(
            f"{filename} must call {contract['shared_workflow']}"
        )

    updated = (
        content[: matches[0].start("sha")]
        + release_sha
        + content[matches[0].end("sha") :]
    )
    lines = updated.splitlines(keepends=True)
    uses_line = updated[: matches[0].start()].count("\n")
    uses_indent = len(matches[0].group("indent"))
    _job_start, job_end = _job_bounds(lines, uses_line, uses_indent)

    with_start, with_end, with_indent = _block_bounds(
        lines, "with", uses_line, job_end, uses_indent
    )
    present_inputs = _mapping_keys(lines, with_start, with_end, with_indent)
    missing_inputs = sorted(set(contract["inputs"]) - present_inputs)
    if missing_inputs:
        raise CallerContractError(
            "caller is missing required inputs: " + ", ".join(missing_inputs)
        )

    secrets_start, secrets_end, secrets_indent = _block_bounds(
        lines, "secrets", uses_line, job_end, uses_indent
    )
    secret_values = _mapping_values(lines, secrets_start, secrets_end, secrets_indent)
    present_secrets = set(secret_values)
    unknown_missing = [
        secret
        for secret in REQUIRED_SECRETS
        if secret not in present_secrets and secret != "CODEX_JIRA_PR_NOTIFY_URL"
    ]
    if unknown_missing:
        raise CallerContractError(
            "caller is missing required secrets: " + ", ".join(unknown_missing)
        )

    if "CODEX_JIRA_PR_NOTIFY_URL" not in present_secrets:
        item_indent = " " * (secrets_indent + 2)
        insertion = (
            f"{item_indent}CODEX_JIRA_PR_NOTIFY_URL: "
            "${{ secrets.CODEX_JIRA_PR_NOTIFY_URL }}\n"
        )
        sonar_line = None
        for index in range(secrets_start + 1, secrets_end):
            if re.match(r"^\s*CODEX_SONAR_TOKEN:", lines[index]):
                sonar_line = index
                break
        lines.insert(sonar_line if sonar_line is not None else secrets_end, insertion)
        secret_values["CODEX_JIRA_PR_NOTIFY_URL"] = (
            "${{ secrets.CODEX_JIRA_PR_NOTIFY_URL }}"
        )

    for secret in REQUIRED_SECRETS:
        expected_mapping = f"${{{{ secrets.{secret} }}}}"
        if secret_values.get(secret) != expected_mapping:
            raise CallerContractError(
                f"caller secret {secret} must map exactly to {expected_mapping}"
            )

    migrated = "".join(lines)
    if not migrated.endswith("\n"):
        migrated += "\n"
    return migrated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    content = args.input.read_text(encoding="utf-8")
    migrated = update_caller(content, Path(args.workflow).name, args.release_sha)
    args.output.write_text(migrated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
