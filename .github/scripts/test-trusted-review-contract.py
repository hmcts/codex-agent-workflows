#!/usr/bin/env python3

from workflow_safety_test_support import *  # noqa: F403


class TrustedReviewContractTests(WorkflowSafetyTestCase):
    def test_review_event_roots_reject_write_oidc_secrets_and_environments(self):
        triggers = {
            "pull_request_review": "on:\n  pull_request_review:\n    types: [submitted]",
            "pull_request_review_comment": (
                "on:\n  pull_request_review_comment:\n    types: [created]"
            ),
        }
        cases = {
            "contents write": (
                """permissions:
  contents: write
jobs:
  inspect:
    runs-on: ubuntu-latest
    steps: []""",
                "effective write permission(s): contents",
            ),
            "OIDC": (
                """permissions:
  id-token: write
jobs:
  inspect:
    runs-on: ubuntu-latest
    steps: []""",
                "effective write permission(s): id-token",
            ),
            "secret checkout": (
                """permissions: read-all
jobs:
  inspect:
    runs-on: ubuntu-latest
    env:
      TOKEN: ${{ secrets.REVIEW_TOKEN }}
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}""",
                "references the secrets context",
            ),
            "environment": (
                """permissions: read-all
jobs:
  inspect:
    environment: production
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}""",
                "environment-backed credentials",
            ),
        }
        for event, trigger in triggers.items():
            for exposure, (body, diagnostic) in cases.items():
                with self.subTest(event=event, exposure=exposure):
                    self.assert_blocked(
                        workflow(body, trigger=trigger),
                        diagnostic,
                    )

    def test_review_event_filter_ambiguity_fails_closed(self):
        triggers = {
            "sequence required": "on:\n  pull_request_review:\n    types: submitted",
            "dynamic type": (
                "on:\n  pull_request_review_comment:\n"
                "    types: ['${{ inputs.review_action }}']"
            ),
            "unsupported branch filter": (
                "on:\n  pull_request_review:\n"
                "    types: [submitted]\n"
                "    branches: [main]"
            ),
            "mapping required": "on:\n  pull_request_review: submitted",
        }
        for diagnostic, trigger in triggers.items():
            with self.subTest(diagnostic=diagnostic):
                self.assert_blocked(
                    workflow(
                        """permissions: read-all
jobs:
  inspect:
    runs-on: ubuntu-latest
    steps: []""",
                        trigger=trigger,
                    ),
                    {
                        "sequence required": "must be a non-empty sequence",
                        "dynamic type": "dynamic or ambiguous",
                        "unsupported branch filter": "unsupported filter(s): branches",
                        "mapping required": "must be empty or a filter mapping",
                    }[diagnostic],
                )

    def test_review_event_recursively_checks_local_reusable_workflows(self):
        self.assert_workflows_blocked(
            {
                "ci.yml": workflow(
                    """permissions: read-all
jobs:
  inspect:
    uses: ./.github/workflows/inspect.yml""",
                    trigger="on:\n  pull_request_review:\n    types: [submitted]",
                ),
                "inspect.yml": reusable(
                    """permissions:
  contents: write
jobs:
  inspect:
    runs-on: ubuntu-latest
    steps: []"""
                ),
            },
            "effective write permission(s): contents",
        )

    def test_accepts_only_the_immutable_trusted_review_command_wrapper(self):
        completed = self.run_check({"codex_pr_review.yml": trusted_review_wrapper()})
        self.assertEqual(completed.returncode, 0, completed.stderr)

        mutations = {
            "40-character SHA": trusted_review_wrapper().replace(
                f"@{'1' * 40}", "@main", 1
            ),
            "exactly types": trusted_review_wrapper().replace(
                "types: [created]", "types: [created, edited]", 1
            ),
            "exact command": trusted_review_wrapper().replace(
                "comment.body == '/codex-review'", "contains(comment.body, '/codex-review')", 1
            ),
            "author-association": trusted_review_wrapper().replace(
                '["COLLABORATOR","MEMBER","OWNER"]', '["CONTRIBUTOR","OWNER"]', 1
            ),
            "must not execute steps": trusted_review_wrapper().replace(
                "    with:\n", "    steps: []\n    with:\n", 1
            ),
            "three trusted review secrets": trusted_review_wrapper().replace(
                "      CODEX_JIRA_PR_NOTIFY_URL:",
                "      EXTRA_SECRET: literal\n      CODEX_JIRA_PR_NOTIFY_URL:",
                1,
            ),
            "effective write permission": trusted_review_wrapper().replace(
                "      issues: read\n    uses:", "      issues: write\n    uses:", 1
            ),
        }
        for diagnostic, content in mutations.items():
            with self.subTest(diagnostic=diagnostic):
                self.assert_blocked(
                    content,
                    diagnostic,
                    filename="codex_pr_review.yml",
                )

    def test_trusted_review_wrapper_rejects_review_event_roots(self):
        trusted = {"codex_pr_review.yml": trusted_review_wrapper()}
        for event, trigger in {
            "review": "on:\n  pull_request_review:\n    types: [submitted]",
            "review comment": "on:\n  pull_request_review_comment:\n    types: [created]",
        }.items():
            with self.subTest(event=event):
                candidate = trusted_review_wrapper().replace(
                    "on:\n  issue_comment:\n    types: [created]", trigger
                )
                self.assert_workflows_blocked(
                    {"codex_pr_review.yml": candidate},
                    "must use only on.issue_comment",
                    filename="codex_pr_review.yml",
                    trusted_workflows=trusted,
                )

    def test_trusted_review_sonar_origin_is_exact_and_not_expression_driven(self):
        trusted = {"codex_pr_review.yml": trusted_review_wrapper()}
        rejected_values = {
            "alternate host": "https://attacker.example",
            "userinfo": "https://token@sonarcloud.io",
            "scheme": "http://sonarcloud.io",
            "port": "https://sonarcloud.io:8443",
            "path": "https://sonarcloud.io/api",
            "expression": "${{ github.event.client_payload.sonar_url }}",
            "static var": "${{ vars.SONAR_HOST_URL }}",
        }
        for case, value in rejected_values.items():
            with self.subTest(case=case):
                self.assert_workflows_blocked(
                    {
                        "codex_pr_review.yml": trusted_review_wrapper(
                            sonar_host_url=value
                        )
                    },
                    "must equal the approved Sonar origin https://sonarcloud.io",
                    filename="codex_pr_review.yml",
                    trusted_workflows=trusted,
                )

        completed = self.run_check(
            {"codex_pr_review.yml": trusted_review_wrapper()},
            trusted_workflows=trusted,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_trusted_review_inputs_equal_default_branch_contract(self):
        trusted = {"codex_pr_review.yml": trusted_review_wrapper()}
        mutations = {
            "runner": ("codex-juror-api-aks", "attacker-runner"),
            "application variable": (
                "vars.CODEX_GITHUB_APP_CLIENT_ID",
                "vars.ATTACKER_GITHUB_APP_CLIENT_ID",
            ),
            "Sonar project": ("sonar_project_key: juror-api", "sonar_project_key: attacker-project"),
        }
        for case, (before, after) in mutations.items():
            with self.subTest(case=case):
                candidate = trusted_review_wrapper().replace(before, after)
                self.assert_workflows_blocked(
                    {"codex_pr_review.yml": candidate},
                    "must equal the immutable default-branch wrapper contract",
                    filename="codex_pr_review.yml",
                    trusted_workflows=trusted,
                )

    def test_trusted_review_pin_equals_reviewed_default_branch_pin(self):
        trusted = {"codex_pr_review.yml": trusted_review_wrapper(pin="1" * 40)}
        for case, pin in {
            "old": "0" * 40,
            "unreviewed": "2" * 40,
            "different": "f" * 40,
        }.items():
            with self.subTest(case=case):
                self.assert_workflows_blocked(
                    {"codex_pr_review.yml": trusted_review_wrapper(pin=pin)},
                    "must equal the immutable default-branch pin",
                    filename="codex_pr_review.yml",
                    trusted_workflows=trusted,
                )

        self.assert_workflows_blocked(
            {"codex_pr_review.yml": trusted_review_wrapper(pin="main")},
            "40-character SHA",
            filename="codex_pr_review.yml",
            trusted_workflows=trusted,
        )

    def test_other_automatic_revision_event_roots_are_protected(self):
        triggers = {
            "create": "on: create",
            "review thread": (
                "on:\n  pull_request_review_thread:\n    types: [resolved]"
            ),
            "issue comment": "on:\n  issue_comment:\n    types: [created]",
            "merge group": "on:\n  merge_group:\n    types: [checks_requested]",
            "commit comment": "on:\n  commit_comment:\n    types: [created]",
            "check run": "on:\n  check_run:\n    types: [completed]",
            "check suite": "on:\n  check_suite:\n    types: [completed]",
            "status": "on: status",
        }
        for event, trigger in triggers.items():
            with self.subTest(event=event):
                self.assert_blocked(
                    workflow(
                        """permissions:
  contents: write
jobs:
  privileged:
    runs-on: ubuntu-latest
    steps: []""",
                        trigger=trigger,
                    ),
                    "effective write permission(s): contents",
                )

    def test_trusted_operator_and_default_branch_events_remain_permitted(self):
        triggers = {
            "workflow dispatch": "on: workflow_dispatch",
            "repository dispatch": (
                "on:\n  repository_dispatch:\n    types: [trusted-command]"
            ),
            "schedule": "on:\n  schedule:\n    - cron: '0 3 * * *'",
        }
        for event, trigger in triggers.items():
            with self.subTest(event=event):
                completed = self.run_check(
                    {
                        "trusted.yml": workflow(
                            """permissions: write-all
jobs:
  trusted:
    runs-on: ubuntu-latest
    env:
      TOKEN: ${{ secrets.TRUSTED_TOKEN }}
    steps: []""",
                            trigger=trigger,
                        )
                    }
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)



if __name__ == "__main__":
    unittest.main()
