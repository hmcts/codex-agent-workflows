#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-codex-pr-safety.rb")


def workflow(body: str, *, trigger: str = "on: pull_request") -> str:
    return f"{trigger}\n{textwrap.dedent(body).strip()}\n"


class CheckCodexPrSafetyTests(unittest.TestCase):
    def run_check(self, workflows: dict[str, str]) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workflow_dir = root / ".github" / "workflows"
            workflow_dir.mkdir(parents=True)
            for name, content in workflows.items():
                (workflow_dir / name).write_text(content, encoding="utf-8")
            return subprocess.run(
                ["ruby", "--disable-gems", str(SCRIPT), "--repository-root", str(root)],
                capture_output=True,
                text=True,
            )

    def assert_blocked(self, content: str, diagnostic: str) -> None:
        completed = self.run_check({"ci.yml": content})
        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertIn("Unsafe PR credential exposure", completed.stderr)
        self.assertIn(diagnostic, completed.stderr)
        self.assertIn(".github/workflows/ci.yml", completed.stderr)

    def test_accepts_normal_read_only_pr_workflow(self):
        completed = self.run_check(
            {
                "ci.yml": workflow(
                    """permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: ./gradlew check"""
                )
            }
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_accepts_sequence_trigger_with_explicit_job_permissions(self):
        completed = self.run_check(
            {
                "ci.yml": workflow(
                    """jobs:
  test:
    permissions: {}
    runs-on: ubuntu-latest
    steps:
      - run: make test""",
                    trigger="on:\n  - push\n  - pull_request",
                )
            }
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_rejects_folded_and_literal_write_permissions(self):
        for style in (">-", "|"):
            with self.subTest(style=style):
                self.assert_blocked(
                    workflow(
                        f"""permissions:
  id-token: {style}
    write
jobs:
  test:
    runs-on: ubuntu-latest
    steps: []""",
                        trigger="on:\n  - pull_request",
                    ),
                    "effective write permission(s): id-token",
                )

    def test_rejects_scheduler_api_pr_ci_before_generated_gradle_runs(self):
        self.assert_blocked(
            workflow(
                """permissions:
  contents: read
  id-token: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: azure/login@v2
      - run: az acr login --name hmctsprod
      - run: ./gradlew check"""
            ),
            "effective write permission(s): id-token",
        )

    def test_rejects_every_workflow_or_job_write_permission(self):
        cases = {
            "workflow write": """permissions:
  contents: write
jobs:
  test:
    runs-on: ubuntu-latest
    steps: []""",
            "workflow write-all": """permissions: write-all
jobs:
  test:
    runs-on: ubuntu-latest
    steps: []""",
            "job write": """permissions: read-all
jobs:
  test:
    permissions:
      checks: write
    runs-on: ubuntu-latest
    steps: []""",
        }
        for name, body in cases.items():
            with self.subTest(name=name):
                self.assert_blocked(workflow(body), "effective write permission")

    def test_job_read_only_override_removes_inherited_workflow_write(self):
        completed = self.run_check(
            {
                "ci.yml": workflow(
                    """permissions:
  contents: write
jobs:
  test:
    permissions:
      contents: read
    runs-on: ubuntu-latest
    steps: []"""
                )
            }
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_rejects_implicit_repository_default_permissions(self):
        self.assert_blocked(
            workflow(
                """jobs:
  test:
    runs-on: ubuntu-latest
    steps: []"""
            ),
            "explicit read-only permissions are required",
        )

    def test_rejects_secret_context_at_workflow_job_and_step_scope(self):
        cases = {
            "workflow env": """permissions: read-all
env:
  TOKEN: ${{ secrets.CODEX_GITHUB_APP_PRIVATE_KEY }}
jobs:
  test:
    runs-on: ubuntu-latest
    steps: []""",
            "job env": """permissions: read-all
jobs:
  test:
    runs-on: ubuntu-latest
    env:
      TOKEN: ${{ secrets.OTHER_SECRET }}
    steps: []""",
            "step env": """permissions: read-all
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - env:
          TOKEN: ${{ secrets['INDEXED_SECRET'] }}
        run: make test""",
            "bare context": """permissions: read-all
jobs:
  test:
    runs-on: ubuntu-latest
    env:
      ALL_SECRETS: ${{ toJSON(secrets) }}
    steps: []""",
        }
        for scope, body in cases.items():
            with self.subTest(scope=scope):
                self.assert_blocked(
                    workflow(body),
                    "references the secrets context",
                )

    def test_rejects_folded_secret_expression(self):
        self.assert_blocked(
            workflow(
                """permissions: read-all
jobs:
  test:
    runs-on: ubuntu-latest
    env:
      TOKEN: >-
        ${{ secrets.FOLDED_SECRET }}
    steps: []"""
            ),
            "references the secrets context",
        )

    def test_rejects_reusable_workflow_secret_mapping_and_inherit(self):
        for secrets_block in (
            "secrets: inherit",
            "secrets:\n      token: ${{ secrets.REUSABLE_TOKEN }}",
            "secrets: unsupported-scalar",
        ):
            with self.subTest(secrets_block=secrets_block):
                self.assert_blocked(
                    workflow(
                        f"""permissions: read-all
jobs:
  reusable:
    uses: owner/repository/.github/workflows/test.yml@{'1' * 40}
    {secrets_block}"""
                    ),
                    "secrets",
                )

    def test_accepts_read_only_reusable_workflow_without_secrets(self):
        completed = self.run_check(
            {
                "ci.yml": workflow(
                    f"""jobs:
  reusable:
    permissions:
      contents: read
    uses: owner/repository/.github/workflows/test.yml@{'1' * 40}""",
                    trigger="'on': [pull_request_target]",
                )
            }
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_resolves_trigger_and_permission_aliases(self):
        completed = self.run_check(
            {
                "ci.yml": """x-events: &events [push, pull_request]
on: *events
permissions: &read-only
  contents: read
jobs:
  test:
    permissions: *read-only
    runs-on: ubuntu-latest
    steps: []
"""
            }
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_alias_cannot_hide_write_permission(self):
        self.assert_blocked(
            """on: [pull_request]
permissions: &cloud
  id-token: write
jobs:
  test:
    permissions: *cloud
    runs-on: ubuntu-latest
    steps: []
""",
            "effective write permission(s): id-token",
        )

    def test_rejects_malformed_duplicate_merge_and_unresolved_alias_yaml(self):
        cases = {
            "malformed YAML": "on: [pull_request\npermissions: read-all\n",
            "duplicate key": """on: pull_request
permissions: read-all
permissions: {}
jobs: {}
""",
            "YAML merge key": """on: pull_request
permissions: &base
  contents: read
jobs:
  test:
    <<: *base
    permissions: {}
""",
            "YAML could not be resolved safely": """on: *missing
permissions: read-all
jobs: {}
""",
            "exactly one YAML document": """on: push
---
on: pull_request
permissions: read-all
jobs: {}
""",
        }
        for diagnostic, content in cases.items():
            with self.subTest(diagnostic=diagnostic):
                self.assert_blocked(content, diagnostic)

    def test_rejects_ambiguous_top_level_boolean_key(self):
        for ambiguous_key in ("true", "ON", "yes"):
            with self.subTest(ambiguous_key=ambiguous_key):
                self.assert_blocked(
                    f"""on: pull_request
{ambiguous_key}: push
permissions: read-all
jobs: {{}}
""",
                    "special on semantics",
                )

    def test_push_only_workflow_may_publish_with_oidc_and_secrets(self):
        completed = self.run_check(
            {
                "publish.yml": """on: push
permissions:
  id-token: write
jobs:
  publish:
    runs-on: ubuntu-latest
    env:
      TOKEN: ${{ secrets.PUBLISH_TOKEN }}
    steps: []
"""
            }
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
