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



    SKIP = "${{ !startsWith(github.head_ref, 'codex/') }}"

    @staticmethod
    def credentialed_job(condition: str | None, *, name: str = "add-redirect-uris") -> str:
        guard = f"    if: {condition}\n" if condition is not None else ""
        return (
            f"  {name}:\n"
            f"{guard}"
            "    runs-on: ubuntu-latest\n"
            "    env:\n"
            "      AZURE_CLIENT_ID: ${{ secrets.AZURE_CLIENT_ID }}\n"
            "    steps:\n"
            "      - run: echo register\n"
        )

    def test_generated_pr_skip_exempts_a_credentialed_pull_request_job(self):
        for trigger in (
            "on: pull_request",
            "on:\n  pull_request:\n    types: [opened, reopened, synchronize]",
            "on:\n  pull_request:\n    types: [closed]",
            "on: pull_request_target",
            "on:\n  pull_request:\n  pull_request_target:",
        ):
            for condition in (
                self.SKIP,
                "\"!startsWith(github.head_ref, 'codex/')\"",
                # A strip-chomped block scalar leaves exactly the expression.
                "|-\n      ${{ !startsWith(github.head_ref, 'codex/') }}",
            ):
                with self.subTest(trigger=trigger, condition=condition):
                    body = "permissions:\n  contents: read\njobs:\n" + self.credentialed_job(condition)
                    completed = self.run_check({"preview.yml": workflow(body, trigger=trigger)})
                    self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_generated_pr_skip_exempts_a_code_scanning_upload(self):
        body = f"""permissions:
  contents: read
jobs:
  analyze:
    if: {self.SKIP}
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write
    steps:
      - uses: github/codeql-action/analyze@v3"""
        trigger = (
            "on:\n  push:\n    branches: [master]\n"
            "  pull_request:\n    branches: [master]\n"
            "  schedule:\n    - cron: '0 3 * * 1'"
        )
        completed = self.run_check({"codeql.yaml": workflow(body, trigger=trigger)})
        self.assertEqual(completed.returncode, 0, completed.stderr)

        self.assert_blocked(
            workflow(body.replace(f"    if: {self.SKIP}\n", ""), trigger=trigger),
            "effective write permission(s): security-events",
            filename="codeql.yaml",
        )

    def test_only_the_exact_skip_condition_exempts_a_job(self):
        for condition in (
            None,
            "${{ !startsWith(github.head_ref, 'codex') }}",
            "${{ !startsWith(github.head_ref, 'CODEX/') }}",
            "${{ !startsWith(github.ref, 'refs/heads/codex/') }}",
            "${{ startsWith(github.head_ref, 'codex/') }}",
            "${{ github.head_ref != 'codex/' }}",
            "${{ !startsWith(github.head_ref, 'codex/') || true }}",
            "${{ !startsWith(github.head_ref, 'codex/') && github.actor != 'bot' || true }}",
            "${{ always() }}",
            # GitHub treats text around ${{ }} as a string template, which is
            # always true, so these run on codex/ branches despite the condition.
            "|\n      ${{ !startsWith(github.head_ref, 'codex/') }}",
            ">\n      ${{ !startsWith(github.head_ref, 'codex/') }}",
            "\" ${{ !startsWith(github.head_ref, 'codex/') }}\"",
            "\"${{ !startsWith(github.head_ref, 'codex/') }} \"",
            "\"${{ !startsWith(github.head_ref, 'codex/') }}\\t\"",
        ):
            with self.subTest(condition=condition):
                body = "permissions:\n  contents: read\njobs:\n" + self.credentialed_job(condition)
                self.assert_blocked(workflow(body), "references the secrets context")

    def test_skip_condition_gives_no_cover_when_another_event_reaches_the_workflow(self):
        body = "permissions:\n  contents: read\njobs:\n" + self.credentialed_job(self.SKIP)
        for trigger in (
            "on:\n  pull_request:\n  push:",
            "on:\n  pull_request:\n  push:\n    branches: ['**']",
            "on:\n  pull_request:\n  issue_comment:\n    types: [created]",
            "on:\n  pull_request:\n  create:",
            "on:\n  pull_request:\n  check_run:\n    types: [completed]",
        ):
            with self.subTest(trigger=trigger):
                self.assert_blocked(workflow(body, trigger=trigger), "references the secrets context")

    def test_skip_condition_gives_no_cover_to_a_workflow_run_listener(self):
        body = "permissions:\n  contents: read\njobs:\n" + self.credentialed_job(self.SKIP)
        self.assert_workflows_blocked(
            {
                "ci.yml": named_workflow(
                    "CI",
                    "permissions:\n  contents: read\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps: []",
                    trigger="on: pull_request",
                ),
                "listener.yml": named_workflow(
                    "Listener",
                    body,
                    trigger="on:\n  workflow_run:\n    workflows: [CI]\n    types: [completed]",
                ),
            },
            "references the secrets context",
            filename="listener.yml",
        )

    def test_skip_exempts_only_the_job_that_declares_it(self):
        body = (
            "permissions:\n  contents: read\njobs:\n"
            + self.credentialed_job(self.SKIP)
            + self.credentialed_job(None, name="build")
        )
        completed = self.run_check({"ci.yml": workflow(body)})
        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertIn("workflow.jobs.build.env.AZURE_CLIENT_ID references the secrets context", completed.stderr)
        self.assertNotIn("add-redirect-uris", completed.stderr)

    def test_a_dependent_job_needs_its_own_skip_condition(self):
        body = (
            "permissions:\n  contents: read\njobs:\n"
            "  plan:\n"
            f"    if: {self.SKIP}\n"
            "    runs-on: ubuntu-latest\n"
            "    steps: []\n"
            + self.credentialed_job(None, name="deploy").replace(
                "    runs-on: ubuntu-latest\n", "    needs: plan\n    runs-on: ubuntu-latest\n", 1
            )
        )
        self.assert_blocked(workflow(body), "workflow.jobs.deploy.env.AZURE_CLIENT_ID references the secrets context")

    def test_workflow_level_secrets_stay_blocked_when_every_job_skips(self):
        body = (
            "permissions:\n  contents: read\n"
            "env:\n  AZURE_CLIENT_ID: ${{ secrets.AZURE_CLIENT_ID }}\n"
            "jobs:\n"
            f"  register:\n    if: {self.SKIP}\n    runs-on: ubuntu-latest\n    steps: []\n"
        )
        self.assert_blocked(workflow(body), "workflow.env.AZURE_CLIENT_ID references the secrets context")

    def test_skipped_job_does_not_reach_its_local_reusable_workflow(self):
        callee = reusable(
            "permissions:\n  contents: read\njobs:\n" + self.credentialed_job(None, name="register")
        )
        for condition, blocked in ((self.SKIP, False), (None, True)):
            with self.subTest(skipped=not blocked):
                guard = f"    if: {condition}\n" if condition else ""
                caller = workflow(
                    "permissions:\n  contents: read\njobs:\n"
                    f"  register:\n{guard}    uses: ./.github/workflows/register.yml\n"
                )
                workflows = {"preview.yml": caller, "register.yml": callee}
                if blocked:
                    self.assert_workflows_blocked(
                        workflows, "references the secrets context", filename="preview.yml"
                    )
                else:
                    completed = self.run_check(workflows)
                    self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
