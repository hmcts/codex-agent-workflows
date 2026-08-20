#!/usr/bin/env python3

from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-codex-pr-safety.rb")


def workflow(body: str, *, trigger: str = "on: pull_request") -> str:
    return f"{trigger}\n{textwrap.dedent(body).strip()}\n"


def reusable(body: str) -> str:
    return workflow(body, trigger="on: workflow_call")


def named_workflow(name: str, body: str, *, trigger: str) -> str:
    return f"name: {name}\n{workflow(body, trigger=trigger)}"


def trusted_review_wrapper(
    *, pin: str | None = None, sonar_host_url: str = "https://sonarcloud.io"
) -> str:
    pin = pin or "1" * 40
    return f"""name: Codex PR Review
on:
  issue_comment:
    types: [created]
permissions:
  contents: read
  pull-requests: read
  issues: read
jobs:
  review:
    if: ${{{{ github.event.issue.pull_request && github.event.comment.body == '/codex-review' && contains(fromJSON('[\"COLLABORATOR\",\"MEMBER\",\"OWNER\"]'), github.event.comment.author_association) }}}}
    permissions:
      contents: read
      pull-requests: read
      issues: read
    uses: hmcts/codex-agent-workflows/.github/workflows/codex-review-feedback.yml@{pin}
    with:
      runner_label: codex-juror-api-aks
      github_app_client_id: ${{{{ vars.CODEX_GITHUB_APP_CLIENT_ID }}}}
      sonar_host_url: {sonar_host_url}
      sonar_project_key: juror-api
    secrets:
      CODEX_OPENAI_API_KEY: ${{{{ secrets.CODEX_OPENAI_API_KEY }}}}
      CODEX_GITHUB_APP_PRIVATE_KEY: ${{{{ secrets.CODEX_GITHUB_APP_PRIVATE_KEY }}}}
      CODEX_JIRA_PR_NOTIFY_URL: ${{{{ secrets.CODEX_JIRA_PR_NOTIFY_URL }}}}
"""


class WorkflowSafetyTestCase(unittest.TestCase):
    def run_check(
        self,
        workflows: dict[str, str],
        *,
        trusted_workflows: dict[str, str] | None = None,
        directories: tuple[str, ...] = (),
        symlinks: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workflow_dir = root / ".github" / "workflows"
            workflow_dir.mkdir(parents=True)
            for name, content in workflows.items():
                path = workflow_dir / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            for name in directories:
                (workflow_dir / name).mkdir()
            for name, target in (symlinks or {}).items():
                os.symlink(target, workflow_dir / name)
            trusted_root = root / "trusted"
            trusted_workflow_dir = trusted_root / ".github" / "workflows"
            trusted_workflow_dir.mkdir(parents=True)
            for name, content in (trusted_workflows or workflows).items():
                path = trusted_workflow_dir / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            return subprocess.run(
                [
                    "ruby",
                    "--disable-gems",
                    str(SCRIPT),
                    "--repository-root",
                    str(root),
                    "--trusted-repository-root",
                    str(trusted_root),
                ],
                capture_output=True,
                text=True,
            )

    def assert_blocked(
        self,
        content: str,
        diagnostic: str,
        *,
        filename: str = "ci.yml",
    ) -> None:
        self.assert_workflows_blocked({filename: content}, diagnostic, filename=filename)

    def assert_workflows_blocked(
        self,
        workflows: dict[str, str],
        diagnostic: str,
        *,
        filename: str = "ci.yml",
        trusted_workflows: dict[str, str] | None = None,
        directories: tuple[str, ...] = (),
        symlinks: dict[str, str] | None = None,
    ) -> None:
        completed = self.run_check(
            workflows,
            trusted_workflows=trusted_workflows,
            directories=directories,
            symlinks=symlinks,
        )
        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertIn("Unsafe generated-code credential exposure", completed.stderr)
        self.assertIn(diagnostic, completed.stderr)
        self.assertIn(f".github/workflows/{filename}", completed.stderr)
