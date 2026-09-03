#!/usr/bin/env python3
"""Collect reproducible GitHub evidence for a Jira-to-PR acceptance run."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any


INITIATOR_PATTERN = re.compile(
    r"^Initiated in Jira by:\s*(?P<name>.+?)\s*$", re.MULTILINE
)
MODEL_JOB_SUFFIXES = (
    "/ codex-plan-action",
    "/ codex-generate-action",
    "/ repair-action",
    "/ codex-review-action",
    "/ codex-review-external-repair-action",
)


def gh_json(endpoint: str, *, accept: str | None = None) -> Any:
    command = ["gh", "api"]
    if accept:
        command.extend(["-H", f"Accept: {accept}"])
    command.append(endpoint)
    completed = subprocess.run(
        command, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def job_summary(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": job.get("name", ""),
        "status": job.get("status", ""),
        "conclusion": job.get("conclusion"),
        "runner_name": job.get("runner_name", ""),
        "labels": job.get("labels", []),
        "url": job.get("html_url", ""),
    }


def build_report(
    repository: str,
    issue_key: str,
    run: dict[str, Any],
    jobs: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    pull_request: dict[str, Any],
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    body = pull_request.get("body") or ""
    initiator_match = INITIATOR_PATTERN.search(body)
    actor = (pull_request.get("user") or {}).get("login", "")
    plan_artifacts = [
        artifact.get("name", "")
        for artifact in artifacts
        if "validated-plan" in artifact.get("name", "")
    ]
    raw_model_jobs = [
        job
        for job in jobs
        if any(job.get("name", "").endswith(suffix) for suffix in MODEL_JOB_SUFFIXES)
    ]
    model_jobs = [job_summary(job) for job in raw_model_jobs]
    runner_names = sorted(
        {job.get("runner_name", "") for job in jobs if job.get("runner_name")}
    )
    check_summaries = [
        {
            "name": check.get("name", ""),
            "status": check.get("status", ""),
            "conclusion": check.get("conclusion"),
            "url": check.get("details_url", ""),
        }
        for check in checks
    ]

    return {
        "repository": repository,
        "issue_key": issue_key,
        "run_id": run.get("id"),
        "pr_number": pull_request.get("number"),
        "workflow_run": {
            "id": run.get("id"),
            "url": run.get("html_url", ""),
            "event": run.get("event", ""),
            "head_branch": run.get("head_branch", ""),
            "head_sha": run.get("head_sha", ""),
            "status": run.get("status", ""),
            "conclusion": run.get("conclusion"),
        },
        "model_jobs": model_jobs,
        "runner_names": runner_names,
        "artifacts": [artifact.get("name", "") for artifact in artifacts],
        "pull_request": {
            "number": pull_request.get("number"),
            "url": pull_request.get("html_url", ""),
            "author": actor,
            "draft": pull_request.get("draft", False),
            "state": pull_request.get("state", ""),
            "head": (pull_request.get("head") or {}).get("ref", ""),
            "base": (pull_request.get("base") or {}).get("ref", ""),
            "initiator": initiator_match.group("name") if initiator_match else "",
        },
        "checks": check_summaries,
        "assertions": {
            "bot_authored": actor.endswith("[bot]"),
            "initiator_recorded": initiator_match is not None,
            "validated_plan_retained": bool(plan_artifacts),
            "repository_checks_started": bool(check_summaries),
            "model_jobs_used_self_hosted_runner": bool(raw_model_jobs)
            and all(
                bool(job.get("runner_name"))
                and not job.get("runner_name", "").startswith("GitHub Actions ")
                for job in raw_model_jobs
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True, help="OWNER/REPOSITORY")
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--issue-key", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    run = gh_json(f"repos/{args.repository}/actions/runs/{args.run_id}")
    jobs = gh_json(
        f"repos/{args.repository}/actions/runs/{args.run_id}/jobs?per_page=100"
    ).get("jobs", [])
    artifacts = gh_json(
        f"repos/{args.repository}/actions/runs/{args.run_id}/artifacts?per_page=100"
    ).get("artifacts", [])
    pull_request = gh_json(f"repos/{args.repository}/pulls/{args.pr_number}")
    head_sha = (pull_request.get("head") or {}).get("sha", "")
    checks = []
    if head_sha:
        checks = gh_json(
            f"repos/{args.repository}/commits/{head_sha}/check-runs?per_page=100",
            accept="application/vnd.github+json",
        ).get("check_runs", [])

    report = build_report(
        args.repository,
        args.issue_key,
        run,
        jobs,
        artifacts,
        pull_request,
        checks,
    )
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
