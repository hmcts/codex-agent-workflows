#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
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

    def test_pins_a_caller_that_referenced_main(self):
        caller = dispatch_caller().replace(f"@{OLD_SHA}", "@main")
        updated = MODULE.update_caller(caller, "codex_jira_dispatch.yml", NEW_SHA)
        self.assertIn(f"codex-implement.yml@{NEW_SHA}\n", updated)
        self.assertNotIn("@main", updated)

    def test_rejects_a_release_that_is_not_a_full_commit_sha(self):
        for reference in ("main", "v1", "2" * 39, "2" * 41, "A" * 40):
            with self.subTest(reference=reference):
                with self.assertRaisesRegex(
                    MODULE.CallerContractError,
                    "release SHA must be 40 lowercase hexadecimal characters",
                ):
                    MODULE.update_caller(
                        dispatch_caller(), "codex_jira_dispatch.yml", reference
                    )

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


def release_workflow(*, with_runner_group: bool) -> str:
    runner_group = (
        "      runner_group:\n"
        "        required: true\n"
        "        type: string\n"
        if with_runner_group
        else ""
    )
    # The job below passes runner_group at the same indentation as an input
    # declaration; only on.workflow_call.inputs may count as the contract.
    return (
        "name: Codex implementation\n"
        "\n"
        "on:\n"
        "  workflow_call:\n"
        "    inputs:\n"
        "      issueKey:\n"
        "        required: true\n"
        "        type: string\n"
        f"{runner_group}"
        "      runner_label:\n"
        "        required: true\n"
        "        type: string\n"
        "\n"
        "jobs:\n"
        "  plan:\n"
        "    uses: ./.github/workflows/codex-plan.yml\n"
        "    with:\n"
        "      runner_group: ${{ inputs.runner_group }}\n"
        "      runner_label: ${{ inputs.runner_label }}\n"
    )


def with_runner_group(caller: str, value: str) -> str:
    return caller.replace(
        "      runner_label: codex-juror-api-aks\n",
        f"      runner_label: codex-juror-api-aks\n      runner_group: {value}\n",
        1,
    )


class RunnerGroupTests(unittest.TestCase):
    def test_detects_runner_group_only_in_workflow_call_inputs(self):
        self.assertTrue(
            MODULE.release_requires_runner_group(release_workflow(with_runner_group=True))
        )
        self.assertFalse(
            MODULE.release_requires_runner_group(release_workflow(with_runner_group=False))
        )

    def test_adds_runner_group_after_runner_label_when_release_requires_it(self):
        for caller, filename, workflow in (
            (dispatch_caller(), "codex_jira_dispatch.yml", "codex-implement.yml"),
            (review_caller(), "codex_pr_review.yml", "codex-review-feedback.yml"),
        ):
            with self.subTest(filename=filename):
                updated = MODULE.update_caller(
                    caller,
                    filename,
                    NEW_SHA,
                    requires_runner_group=True,
                    runner_group="juror-codex",
                )
                self.assertIn(f"{workflow}@{NEW_SHA}", updated)
                self.assertIn(
                    "      runner_label: codex-juror-api-aks\n"
                    "      runner_group: juror-codex\n",
                    updated,
                )
                self.assertEqual(updated.count("runner_group:"), 1)

    def test_runner_group_insertion_is_idempotent(self):
        first = MODULE.update_caller(
            dispatch_caller(),
            "codex_jira_dispatch.yml",
            NEW_SHA,
            requires_runner_group=True,
            runner_group="juror-codex",
        )
        second = MODULE.update_caller(
            first,
            "codex_jira_dispatch.yml",
            NEW_SHA,
            requires_runner_group=True,
            runner_group="juror-codex",
        )
        self.assertEqual(second, first)

    def test_keeps_a_matching_quoted_runner_group(self):
        caller = with_runner_group(dispatch_caller(), "'juror-codex'")
        updated = MODULE.update_caller(
            caller,
            "codex_jira_dispatch.yml",
            NEW_SHA,
            requires_runner_group=True,
            runner_group="juror-codex",
        )
        self.assertEqual(updated, caller.replace(OLD_SHA, NEW_SHA))

    def test_rejects_a_different_runner_group(self):
        caller = with_runner_group(dispatch_caller(), "appreg-codex")
        with self.assertRaisesRegex(
            MODULE.CallerContractError,
            "runner_group appreg-codex does not match the expected juror-codex",
        ):
            MODULE.update_caller(
                caller,
                "codex_jira_dispatch.yml",
                NEW_SHA,
                requires_runner_group=True,
                runner_group="juror-codex",
            )

    def test_rejects_a_missing_or_invalid_runner_group_when_release_requires_it(self):
        for group in (None, "", "juror codex", "-juror", "juror/codex", "a" * 65):
            with self.subTest(group=group):
                with self.assertRaisesRegex(
                    MODULE.CallerContractError, "requires runner_group"
                ):
                    MODULE.update_caller(
                        dispatch_caller(),
                        "codex_jira_dispatch.yml",
                        NEW_SHA,
                        requires_runner_group=True,
                        runner_group=group,
                    )

    def test_rejects_runner_group_for_a_release_that_does_not_accept_it(self):
        caller = with_runner_group(dispatch_caller(), "juror-codex")
        with self.assertRaisesRegex(
            MODULE.CallerContractError, "does not accept runner_group"
        ):
            MODULE.update_caller(caller, "codex_jira_dispatch.yml", NEW_SHA)

    def test_does_not_add_runner_group_for_a_release_without_it(self):
        updated = MODULE.update_caller(
            dispatch_caller(),
            "codex_jira_dispatch.yml",
            NEW_SHA,
            requires_runner_group=False,
            runner_group="juror-codex",
        )
        self.assertNotIn("runner_group", updated)

    def test_cli_checks_the_release_workflow_each_caller_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release = root / "release"
            release.mkdir()
            (release / "codex-implement.yml").write_text(
                release_workflow(with_runner_group=True), encoding="utf-8"
            )
            (release / "codex-review-feedback.yml").write_text(
                release_workflow(with_runner_group=False), encoding="utf-8"
            )
            for name, caller, expect_group in (
                ("codex_jira_dispatch.yml", dispatch_caller(), True),
                ("codex_pr_review.yml", review_caller(), False),
            ):
                with self.subTest(caller=name):
                    source = root / f"{name}.input"
                    output = root / f"{name}.output"
                    source.write_text(caller, encoding="utf-8")
                    subprocess.run(
                        [
                            sys.executable,
                            "-I",
                            str(SCRIPT),
                            "--workflow",
                            f".github/workflows/{name}",
                            "--release-sha",
                            NEW_SHA,
                            "--input",
                            str(source),
                            "--output",
                            str(output),
                            "--release-workflows",
                            str(release),
                            "--runner-group",
                            "juror-codex",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        "      runner_group: juror-codex\n"
                        in output.read_text(encoding="utf-8"),
                        expect_group,
                    )


def release_with_sonar(*, required: bool) -> str:
    flag = "true" if required else "false"
    default = "" if required else "        default: ''\n"
    return (
        "name: Codex implementation\n"
        "\n"
        "on:\n"
        "  workflow_call:\n"
        "    inputs:\n"
        "      runner_group:\n"
        "        required: true\n"
        "        type: string\n"
        "      sonar_host_url:\n"
        f"        required: {flag}\n"
        f"{default}"
        "        type: string\n"
        "      sonar_project_key:\n"
        f"        required: {flag}\n"
        f"{default}"
        "        type: string\n"
        "\n"
        "jobs:\n"
        "  plan:\n"
        "    uses: ./.github/workflows/codex-plan.yml\n"
        "    with:\n"
        "      sonar_project_key: ${{ inputs.sonar_project_key }}\n"
    )


class RetiredInputTests(unittest.TestCase):
    def test_reads_which_inputs_a_release_still_requires(self):
        self.assertEqual(
            MODULE.release_required_inputs(release_with_sonar(required=True)),
            {"runner_group", "sonar_host_url", "sonar_project_key"},
        )
        self.assertEqual(
            MODULE.release_required_inputs(release_with_sonar(required=False)),
            {"runner_group"},
        )

    def test_removes_retired_inputs_and_keeps_everything_else(self):
        updated = MODULE.update_caller(
            dispatch_caller(),
            "codex_jira_dispatch.yml",
            NEW_SHA,
            remove_inputs=MODULE.RETIRED_INPUTS,
        )
        self.assertNotIn("sonar_", updated)
        for kept in ("runner_label: codex-juror-api-aks", "github_app_client_id:", "issueUrl:"):
            self.assertIn(kept, updated)
        again = MODULE.update_caller(
            updated, "codex_jira_dispatch.yml", NEW_SHA, remove_inputs=MODULE.RETIRED_INPUTS
        )
        self.assertEqual(again, updated)

    def test_refuses_to_remove_a_multi_line_retired_input(self):
        caller = dispatch_caller().replace(
            "      sonar_project_key: juror-api\n",
            "      sonar_project_key: >-\n        juror-api\n",
        )
        with self.assertRaisesRegex(MODULE.CallerContractError, "spans several lines"):
            MODULE.update_caller(
                caller, "codex_jira_dispatch.yml", NEW_SHA, remove_inputs=MODULE.RETIRED_INPUTS
            )

    def test_cli_removes_sonar_only_once_the_release_stops_requiring_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for required, expect_sonar in ((True, True), (False, False)):
                with self.subTest(release_requires_sonar=required):
                    release = root / f"release-{required}"
                    release.mkdir()
                    (release / "codex-implement.yml").write_text(
                        release_with_sonar(required=required), encoding="utf-8"
                    )
                    source = root / f"input-{required}.yml"
                    output = root / f"output-{required}.yml"
                    source.write_text(dispatch_caller(), encoding="utf-8")
                    subprocess.run(
                        [
                            sys.executable,
                            "-I",
                            str(SCRIPT),
                            "--workflow",
                            ".github/workflows/codex_jira_dispatch.yml",
                            "--release-sha",
                            NEW_SHA,
                            "--input",
                            str(source),
                            "--output",
                            str(output),
                            "--release-workflows",
                            str(release),
                            "--runner-group",
                            "juror-codex",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        "sonar_project_key" in output.read_text(encoding="utf-8"),
                        expect_sonar,
                    )


if __name__ == "__main__":
    unittest.main()
