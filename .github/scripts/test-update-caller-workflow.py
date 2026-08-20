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


def dispatch_caller(*, include_notify: bool = True, include_summary: bool = True) -> str:
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
{notify}
"""


def review_caller() -> str:
    return f"""name: Codex PR Review Feedback
on:
  issue_comment:
    types: [created]
jobs:
  review:
    if: ${{{{ github.event.issue.pull_request && github.event.comment.body == '/codex-review' && contains(fromJSON('["COLLABORATOR","MEMBER","OWNER"]'), github.event.comment.author_association) }}}}
    uses: hmcts/codex-agent-workflows/.github/workflows/codex-review-feedback.yml@{OLD_SHA}
    with:
      runner_label: codex-juror-api-aks
      github_app_client_id: ${{{{ vars.CODEX_GITHUB_APP_CLIENT_ID }}}}
      sonar_host_url: https://sonarcloud.io
      sonar_project_key: juror-api
    secrets:
      CODEX_OPENAI_API_KEY: ${{{{ secrets.CODEX_OPENAI_API_KEY }}}}
      CODEX_GITHUB_APP_PRIVATE_KEY: ${{{{ secrets.CODEX_GITHUB_APP_PRIVATE_KEY }}}}
      CODEX_JIRA_PR_NOTIFY_URL: ${{{{ secrets.CODEX_JIRA_PR_NOTIFY_URL }}}}
"""


class UpdateCallerWorkflowTests(unittest.TestCase):
    def test_updates_dispatch_pin_with_exact_secret_set(self):
        updated = MODULE.update_caller(
            dispatch_caller(), "codex_jira_dispatch.yml", NEW_SHA
        )
        self.assertIn(f"codex-implement.yml@{NEW_SHA}", updated)
        self.assertEqual(updated.count("CODEX_JIRA_PR_NOTIFY_URL"), 2)

    def test_updates_safe_review_pin_with_exact_secret_set(self):
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

    def test_rejects_missing_required_secret(self):
        with self.assertRaisesRegex(
            MODULE.CallerContractError,
            "missing required secrets: CODEX_JIRA_PR_NOTIFY_URL",
        ):
            MODULE.update_caller(
                dispatch_caller(include_notify=False),
                "codex_jira_dispatch.yml",
                NEW_SHA,
            )

    def test_rejects_one_or_multiple_extra_secrets(self):
        for extras in (
            ["EXTRA_TOKEN"],
            ["ANOTHER_TOKEN", "EXTRA_TOKEN"],
        ):
            with self.subTest(extras=extras):
                extra_lines = "".join(
                    f"      {name}: ${{{{ secrets.{name} }}}}\n" for name in extras
                )
                caller = dispatch_caller().replace(
                    "      CODEX_JIRA_PR_NOTIFY_URL:",
                    extra_lines + "      CODEX_JIRA_PR_NOTIFY_URL:",
                )
                with self.assertRaisesRegex(
                    MODULE.CallerContractError,
                    "caller supplies unsupported secrets: " + ", ".join(extras),
                ):
                    MODULE.update_caller(
                        caller, "codex_jira_dispatch.yml", NEW_SHA
                    )

    def test_review_caller_requires_safe_issue_comment_gate(self):
        mutations = {
            "event": review_caller().replace(
                "issue_comment:\n    types: [created]",
                "pull_request_review:\n    types: [submitted]",
            ),
            "command": review_caller().replace(
                "comment.body == '/codex-review'",
                "contains(comment.body, '/codex-review')",
            ),
            "association": review_caller().replace(
                '["COLLABORATOR","MEMBER","OWNER"]',
                '["CONTRIBUTOR","OWNER"]',
            ),
        }
        for case, caller in mutations.items():
            with self.subTest(case=case):
                with self.assertRaisesRegex(
                    MODULE.CallerContractError,
                    "review caller",
                ):
                    MODULE.update_caller(
                        caller, "codex_pr_review.yml", NEW_SHA
                    )

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
            "${{ secrets.CODEX_JIRA_PR_NOTIFY_URL }}", "${{ secrets.OTHER_TOKEN }}"
        )

        with self.assertRaisesRegex(
            MODULE.CallerContractError,
            r"CODEX_JIRA_PR_NOTIFY_URL must map exactly to \$\{\{ secrets.CODEX_JIRA_PR_NOTIFY_URL \}\}",
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
