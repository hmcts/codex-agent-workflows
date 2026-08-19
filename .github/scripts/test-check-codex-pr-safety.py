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


class CheckCodexPrSafetyTests(unittest.TestCase):
    def run_check(
        self,
        workflows: dict[str, str],
        *,
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
            return subprocess.run(
                ["ruby", "--disable-gems", str(SCRIPT), "--repository-root", str(root)],
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
        directories: tuple[str, ...] = (),
        symlinks: dict[str, str] | None = None,
    ) -> None:
        completed = self.run_check(
            workflows,
            directories=directories,
            symlinks=symlinks,
        )
        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertIn("Unsafe generated-code credential exposure", completed.stderr)
        self.assertIn(diagnostic, completed.stderr)
        self.assertIn(f".github/workflows/{filename}", completed.stderr)

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
      - uses: actions/checkout@v4
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
                self.assert_blocked(workflow(body), "references the secrets context")

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

    def test_rejects_broad_and_generated_branch_push_permissions(self):
        triggers = {
            "scalar push": "on: push",
            "all branches": "on:\n  push:\n    branches: ['**']",
            "codex branches": "on:\n  push:\n    branches: ['codex/**']",
            "potential prefix": "on:\n  push:\n    branches: ['c*']",
            "unrelated ignore": "on:\n  push:\n    branches-ignore: [main]",
        }
        for name, trigger in triggers.items():
            with self.subTest(name=name):
                self.assert_blocked(
                    workflow(
                        """permissions:
  id-token: write
jobs:
  publish:
    runs-on: ubuntu-latest
    steps: []""",
                        trigger=trigger,
                    ),
                    "effective write permission(s): id-token",
                )

    def test_trusted_only_branch_push_may_use_credentials(self):
        for branches in ("[main]", "[main, 'release/**']"):
            with self.subTest(branches=branches):
                completed = self.run_check(
                    {
                        "publish.yml": workflow(
                            """permissions:
  id-token: write
jobs:
  publish:
    runs-on: ubuntu-latest
    env:
      TOKEN: ${{ secrets.PUBLISH_TOKEN }}
    steps: []""",
                            trigger=f"on:\n  push:\n    branches: {branches}",
                        )
                    }
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_tags_only_push_may_use_credentials(self):
        for filter_name in ("tags", "tags-ignore"):
            with self.subTest(filter_name=filter_name):
                completed = self.run_check(
                    {
                        "publish.yml": workflow(
                            """permissions: write-all
jobs:
  publish:
    runs-on: ubuntu-latest
    env:
      TOKEN: ${{ secrets.PUBLISH_TOKEN }}
    steps: []""",
                            trigger=f"on:\n  push:\n    {filter_name}: ['v*']",
                        )
                    }
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_full_generated_branch_exclusion_is_trusted_only(self):
        triggers = (
            "on:\n  push:\n    branches-ignore: ['codex/**']",
            "on:\n  push:\n    branches: ['**', '!codex/**']",
        )
        for trigger in triggers:
            with self.subTest(trigger=trigger):
                completed = self.run_check(
                    {
                        "publish.yml": workflow(
                            """permissions: write-all
jobs:
  publish:
    runs-on: ubuntu-latest
    steps: []""",
                            trigger=trigger,
                        )
                    }
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_mixed_trigger_remains_protected_by_pull_request(self):
        self.assert_blocked(
            workflow(
                """permissions:
  contents: write
jobs:
  publish:
    runs-on: ubuntu-latest
    steps: []""",
                trigger="on:\n  pull_request:\n  push:\n    branches: [main]",
            ),
            "effective write permission(s): contents",
        )

    def test_path_filters_do_not_make_generated_branch_push_safe(self):
        self.assert_blocked(
            workflow(
                """permissions: read-all
jobs:
  publish:
    runs-on: ubuntu-latest
    env:
      TOKEN: ${{ secrets.PUBLISH_TOKEN }}
    steps: []""",
                trigger="on:\n  push:\n    paths: ['trusted/**']",
            ),
            "references the secrets context",
        )

    def test_rejects_ambiguous_push_filter_forms(self):
        cases = {
            "must be a non-empty sequence": "on:\n  push:\n    branches: main",
            "dynamic or ambiguous": "on:\n  push:\n    branches: ['${{ inputs.branch }}']",
            "unsupported filter": "on:\n  push:\n    unknown: [main]",
            "cannot combine branches": (
                "on:\n  push:\n    branches: [main]\n    branches-ignore: ['codex/**']"
            ),
            "dynamic or empty event": "on: '${{ inputs.event }}'",
        }
        for diagnostic, trigger in cases.items():
            with self.subTest(diagnostic=diagnostic):
                self.assert_blocked(
                    workflow(
                        """permissions: read-all
jobs:
  test:
    runs-on: ubuntu-latest
    steps: []""",
                        trigger=trigger,
                    ),
                    diagnostic,
                )

    def test_inspects_dot_prefixed_workflow_files(self):
        self.assert_blocked(
            workflow(
                """permissions: write-all
jobs:
  hidden:
    runs-on: ubuntu-latest
    steps: []"""
            ),
            "effective write permission",
            filename=".hidden.yml",
        )

    def test_rejects_uninspectable_workflow_directory_entries(self):
        safe = workflow(
            """permissions: read-all
jobs:
  test:
    runs-on: ubuntu-latest
    steps: []"""
        )
        cases = (
            ({"ci.yml": safe, "notes.txt": "not a workflow\n"}, (), None, "notes.txt", "unsupported extension"),
            ({"ci.yml": safe}, ("nested",), None, "nested", "regular file"),
            ({"ci.yml": safe}, (), {"linked.yml": "ci.yml"}, "linked.yml", "symbolic link"),
        )
        for workflows, directories, symlinks, filename, diagnostic in cases:
            with self.subTest(filename=filename):
                self.assert_workflows_blocked(
                    workflows,
                    diagnostic,
                    filename=filename,
                    directories=directories,
                    symlinks=symlinks,
                )

    def test_accepts_safe_local_reusable_workflow_with_inherited_permissions(self):
        completed = self.run_check(
            {
                "ci.yml": workflow(
                    """permissions:
  contents: read
jobs:
  reusable:
    uses: ./.github/workflows/reusable.yml"""
                ),
                "reusable.yml": reusable(
                    """jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: make test"""
                ),
            }
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_rejects_write_permission_in_local_reusable_workflow(self):
        self.assert_workflows_blocked(
            {
                "ci.yml": workflow(
                    """permissions: read-all
jobs:
  reusable:
    uses: ./.github/workflows/reusable.yml"""
                ),
                "reusable.yml": reusable(
                    """permissions:
  security-events: write
jobs:
  scan:
    runs-on: ubuntu-latest
    steps: []"""
                ),
            },
            "effective write permission(s): security-events",
        )

    def test_recursively_rejects_nested_secret_exposure(self):
        self.assert_workflows_blocked(
            {
                "ci.yml": workflow(
                    """permissions: read-all
jobs:
  first:
    uses: ./.github/workflows/first.yml"""
                ),
                "first.yml": reusable(
                    """jobs:
  second:
    uses: ./.github/workflows/second.yml"""
                ),
                "second.yml": reusable(
                    """jobs:
  test:
    runs-on: ubuntu-latest
    env:
      TOKEN: ${{ secrets.NESTED_TOKEN }}
    steps: []"""
                ),
            },
            "references the secrets context",
        )

    def test_accepts_nested_local_reusable_workflows(self):
        completed = self.run_check(
            {
                "ci.yml": workflow(
                    """permissions: read-all
jobs:
  first:
    uses: ./.github/workflows/first.yml"""
                ),
                "first.yml": reusable(
                    """jobs:
  second:
    uses: ./.github/workflows/second.yml"""
                ),
                "second.yml": reusable(
                    """jobs:
  test:
    runs-on: ubuntu-latest
    steps: []"""
                ),
            }
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_rejects_reusable_workflow_cycles(self):
        self.assert_workflows_blocked(
            {
                "ci.yml": workflow(
                    """permissions: read-all
jobs:
  first:
    uses: ./.github/workflows/first.yml"""
                ),
                "first.yml": reusable(
                    """jobs:
  second:
    uses: ./.github/workflows/second.yml"""
                ),
                "second.yml": reusable(
                    """jobs:
  first:
    uses: ./.github/workflows/first.yml"""
                ),
            },
            "reusable workflow cycle detected",
        )

    def test_rejects_environment_credentials_directly_and_in_reusable_workflows(self):
        direct = workflow(
            """permissions: read-all
jobs:
  deploy:
    environment: production
    runs-on: ubuntu-latest
    steps: []"""
        )
        self.assert_blocked(direct, "environment-backed credentials")

        self.assert_workflows_blocked(
            {
                "ci.yml": workflow(
                    """permissions: read-all
jobs:
  deploy:
    uses: ./.github/workflows/deploy.yml"""
                ),
                "deploy.yml": reusable(
                    """jobs:
  deploy:
    environment:
      name: production
    runs-on: ubuntu-latest
    steps: []"""
                ),
            },
            "environment-backed credentials",
        )

    def test_rejects_reusable_workflow_secret_inheritance_and_mappings(self):
        target = reusable(
            """jobs:
  test:
    runs-on: ubuntu-latest
    steps: []"""
        )
        for secrets_block, diagnostic in (
            ("secrets: inherit", "secrets: inherit"),
            ("secrets:\n      token: literal-value", "passes credentials"),
            ("secrets: unsupported-scalar", "unsupported scalar"),
        ):
            with self.subTest(secrets_block=secrets_block):
                self.assert_workflows_blocked(
                    {
                        "ci.yml": workflow(
                            f"""permissions: read-all
jobs:
  reusable:
    uses: ./.github/workflows/reusable.yml
    {secrets_block}"""
                        ),
                        "reusable.yml": target,
                    },
                    diagnostic,
                )

    def test_rejects_external_dynamic_missing_and_noncallable_reusable_workflows(self):
        cases = {
            "external or unsupported": (
                "owner/repository/.github/workflows/test.yml@" + "1" * 40,
                {},
            ),
            "dynamic or ambiguous": ("${{ inputs.workflow }}", {}),
            "missing local workflow": ("./.github/workflows/missing.yml", {}),
            "missing an on.workflow_call": (
                "./.github/workflows/not-callable.yml",
                {
                    "not-callable.yml": workflow(
                        """permissions: read-all
jobs:
  test:
    runs-on: ubuntu-latest
    steps: []""",
                        trigger="on: workflow_dispatch",
                    )
                },
            ),
        }
        for diagnostic, (uses, extra_workflows) in cases.items():
            with self.subTest(diagnostic=diagnostic):
                workflows = {
                    "ci.yml": workflow(
                        f"""permissions: read-all
jobs:
  reusable:
    uses: {uses}"""
                    ),
                    **extra_workflows,
                }
                self.assert_workflows_blocked(workflows, diagnostic)


if __name__ == "__main__":
    unittest.main()
