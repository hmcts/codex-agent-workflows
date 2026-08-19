#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("update-caller-workflow.py")
SPEC = importlib.util.spec_from_file_location("update_caller_workflow", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)

OLD_SHA = "1" * 40
NEW_SHA = "2" * 40


def dispatch_caller(*, include_notify: bool = False, include_summary: bool = True) -> str:
    notify = (
        "      CODEX_JIRA_PR_NOTIFY_URL: ${{ secrets.CODEX_JIRA_PR_NOTIFY_URL }}\n"
        if include_notify
        else ""
    )
    summary = "      summary: ${{ inputs.summary }}\n" if include_summary else ""
    return f"""name: Codex Jira Dispatch
jobs:
  implement:
    uses: hmcts/codex-agent-workflows/.github/workflows/codex-implement.yml@{OLD_SHA}
    with:
      issueKey: ${{{{ inputs.issueKey }}}}
{summary}      description: ${{{{ inputs.description }}}}
      status: ${{{{ inputs.status }}}}
      assignee: ${{{{ inputs.assignee }}}}
      issueUrl: ${{{{ inputs.issueUrl }}}}
      initiatorDisplayName: ${{{{ inputs.initiatorDisplayName }}}}
      runner_label: codex-juror-api-aks
      github_app_client_id: ${{{{ vars.CODEX_GITHUB_APP_CLIENT_ID }}}}
      sonar_host_url: https://sonarcloud.io
      sonar_project_key: juror-api
    secrets:
      CODEX_OPENAI_API_KEY: ${{{{ secrets.CODEX_OPENAI_API_KEY }}}}
      CODEX_GITHUB_APP_PRIVATE_KEY: ${{{{ secrets.CODEX_GITHUB_APP_PRIVATE_KEY }}}}
{notify}      CODEX_SONAR_TOKEN: ${{{{ secrets.CODEX_SONAR_TOKEN }}}}
"""


def review_caller() -> str:
    return f"""name: Codex PR Review Feedback
jobs:
  review:
    uses: hmcts/codex-agent-workflows/.github/workflows/codex-review-feedback.yml@{OLD_SHA}
    with:
      runner_label: codex-juror-api-aks
      github_app_client_id: ${{{{ vars.CODEX_GITHUB_APP_CLIENT_ID }}}}
      sonar_host_url: https://sonarcloud.io
      sonar_project_key: juror-api
    secrets:
      CODEX_OPENAI_API_KEY: ${{{{ secrets.CODEX_OPENAI_API_KEY }}}}
      CODEX_GITHUB_APP_PRIVATE_KEY: ${{{{ secrets.CODEX_GITHUB_APP_PRIVATE_KEY }}}}
      CODEX_SONAR_TOKEN: ${{{{ secrets.CODEX_SONAR_TOKEN }}}}
"""


class UpdateCallerWorkflowTests(unittest.TestCase):
    def test_adds_notify_secret_and_updates_dispatch_pin(self):
        updated = MODULE.update_caller(
            dispatch_caller(), "codex_jira_dispatch.yml", NEW_SHA
        )
        self.assertIn(f"codex-implement.yml@{NEW_SHA}", updated)
        self.assertEqual(updated.count("CODEX_JIRA_PR_NOTIFY_URL"), 2)

    def test_adds_notify_secret_and_updates_review_pin(self):
        updated = MODULE.update_caller(
            review_caller(), "codex_pr_review.yml", NEW_SHA
        )
        self.assertIn(f"codex-review-feedback.yml@{NEW_SHA}", updated)
        self.assertEqual(updated.count("CODEX_JIRA_PR_NOTIFY_URL"), 2)

    def test_migration_is_idempotent(self):
        first = MODULE.update_caller(
            dispatch_caller(include_notify=True), "codex_jira_dispatch.yml", NEW_SHA
        )
        second = MODULE.update_caller(first, "codex_jira_dispatch.yml", NEW_SHA)
        self.assertEqual(second, first)

    def test_rejects_missing_required_input(self):
        with self.assertRaisesRegex(
            MODULE.CallerContractError, "missing required inputs: summary"
        ):
            MODULE.update_caller(
                dispatch_caller(include_summary=False),
                "codex_jira_dispatch.yml",
                NEW_SHA,
            )

    def test_rejects_wrong_shared_workflow(self):
        with self.assertRaisesRegex(MODULE.CallerContractError, "must call"):
            MODULE.update_caller(
                review_caller().replace(
                    "codex-review-feedback.yml", "codex-implement.yml"
                ),
                "codex_pr_review.yml",
                NEW_SHA,
            )

    def test_does_not_borrow_with_block_from_later_job(self):
        caller = dispatch_caller().replace("    with:\n", "    configuration:\n", 1)
        caller += "  later:\n    runs-on: ubuntu-latest\n    with:\n      summary: borrowed\n"

        with self.assertRaisesRegex(MODULE.CallerContractError, "missing with: block"):
            MODULE.update_caller(caller, "codex_jira_dispatch.yml", NEW_SHA)

    def test_does_not_borrow_secrets_block_from_later_job(self):
        caller = dispatch_caller().replace("    secrets:\n", "    configuration-secrets:\n", 1)
        caller += (
            "  later:\n"
            "    runs-on: ubuntu-latest\n"
            "    secrets:\n"
            "      CODEX_OPENAI_API_KEY: ${{ secrets.CODEX_OPENAI_API_KEY }}\n"
        )

        with self.assertRaisesRegex(MODULE.CallerContractError, "missing secrets: block"):
            MODULE.update_caller(caller, "codex_jira_dispatch.yml", NEW_SHA)

    def test_rejects_wrong_required_secret_mapping(self):
        caller = dispatch_caller().replace(
            "${{ secrets.CODEX_SONAR_TOKEN }}", "${{ secrets.OTHER_TOKEN }}"
        )

        with self.assertRaisesRegex(
            MODULE.CallerContractError,
            r"CODEX_SONAR_TOKEN must map exactly to \$\{\{ secrets.CODEX_SONAR_TOKEN \}\}",
        ):
            MODULE.update_caller(caller, "codex_jira_dispatch.yml", NEW_SHA)

    def test_rejects_empty_required_secret_mapping(self):
        caller = dispatch_caller().replace(
            "CODEX_OPENAI_API_KEY: ${{ secrets.CODEX_OPENAI_API_KEY }}",
            "CODEX_OPENAI_API_KEY:",
        )

        with self.assertRaisesRegex(
            MODULE.CallerContractError, "CODEX_OPENAI_API_KEY must map exactly"
        ):
            MODULE.update_caller(caller, "codex_jira_dispatch.yml", NEW_SHA)


if __name__ == "__main__":
    unittest.main()
