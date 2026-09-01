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
)
REVIEW_JOB_IF = (
    "${{ github.event.issue.pull_request && "
    "github.event.comment.body == '/codex-review' && "
    "contains(fromJSON('[\"COLLABORATOR\",\"MEMBER\",\"OWNER\"]'), "
    "github.event.comment.author_association) }}"
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


def _strict_secret_values(
    lines: list[str], start: int, end: int, indent: int
) -> dict[str, str]:
    values: dict[str, str] = {}
    item_indent = indent + 2
    pattern = re.compile(rf"^\s{{{item_indent}}}([A-Za-z0-9_]+):\s*(.*?)\s*$")
    for line in lines[start + 1 : end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = pattern.match(line.rstrip("\n"))
        if not match:
            raise CallerContractError(
                "caller secrets block contains unsupported mapping syntax"
            )
        name = match.group(1)
        if name in values:
            raise CallerContractError(f"caller supplies duplicate secret: {name}")
        values[name] = match.group(2)
    return values


def _validate_review_event_contract(
    lines: list[str], job_start: int, job_end: int, job_indent: int
) -> None:
    trigger_starts = [
        index for index, line in enumerate(lines) if re.match(r"^on:\s*$", line)
    ]
    if len(trigger_starts) != 1:
        raise CallerContractError(
            "review caller must contain exactly one top-level on: block"
        )

    trigger_start = trigger_starts[0]
    trigger_end = trigger_start + 1
    while trigger_end < len(lines):
        line = lines[trigger_end]
        if line.strip() and len(line) - len(line.lstrip()) == 0:
            break
        trigger_end += 1
    trigger_lines = [
        line.rstrip()
        for line in lines[trigger_start:trigger_end]
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if trigger_lines != ["on:", "  issue_comment:", "    types: [created]"]:
        raise CallerContractError(
            "review caller must use only on.issue_comment with exactly types: [created]"
        )

    condition_pattern = re.compile(
        rf"^\s{{{job_indent + 2}}}if:\s*(.*?)\s*$"
    )
    conditions = [
        match.group(1)
        for line in lines[job_start + 1 : job_end]
        if (match := condition_pattern.match(line.rstrip("\n")))
    ]
    if conditions != [REVIEW_JOB_IF]:
        raise CallerContractError(
            "review caller job must use the exact /codex-review command and author-association gate"
        )


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

    lines = content.splitlines(keepends=True)
    uses_line = content[: matches[0].start()].count("\n")
    uses_indent = len(matches[0].group("indent"))
    job_start, job_end = _job_bounds(lines, uses_line, uses_indent)

    if filename == "codex_pr_review.yml":
        _validate_review_event_contract(lines, job_start, job_end, uses_indent - 2)

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
    secret_values = _strict_secret_values(
        lines, secrets_start, secrets_end, secrets_indent
    )
    present_secrets = set(secret_values)
    extra_secrets = sorted(present_secrets - set(REQUIRED_SECRETS))
    if extra_secrets:
        raise CallerContractError(
            "caller supplies unsupported secrets: " + ", ".join(extra_secrets)
        )
    missing_secrets = sorted(set(REQUIRED_SECRETS) - present_secrets)
    if missing_secrets:
        raise CallerContractError(
            "caller is missing required secrets: " + ", ".join(missing_secrets)
        )

    for secret in REQUIRED_SECRETS:
        expected_mapping = f"${{{{ secrets.{secret} }}}}"
        if secret_values.get(secret) != expected_mapping:
            raise CallerContractError(
                f"caller secret {secret} must map exactly to {expected_mapping}"
            )

    validated = "".join(lines)
    migrated = (
        validated[: matches[0].start("sha")]
        + release_sha
        + validated[matches[0].end("sha") :]
    )
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
