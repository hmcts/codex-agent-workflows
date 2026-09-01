#!/usr/bin/env python3

from workflow_safety_test_support import *  # noqa: F403


class WorkflowCredentialPolicyTests(WorkflowSafetyTestCase):
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
