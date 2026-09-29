#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-shared-workflow-trust.rb")
REPOSITORY_ROOT = Path(__file__).parents[2]

INPUTS = """\
    inputs:
      runner_group:
        required: true
        type: string
      runner_label:
        required: true
        type: string
      model_environment:
        required: false
        default: ''
        type: string
      publisher_environment:
        required: false
        default: ''
        type: string
"""
CALL_TRIGGER = f"on:\n  workflow_call:\n{INPUTS}"

MODEL_JOB = """\
  model:
    permissions:
      contents: read
    runs-on:
      group: ${{ inputs.runner_group }}
      labels: ${{ inputs.runner_label }}
    environment: ${{ inputs.model_environment }}
    steps:
      - name: Prepare prompt
        run: echo prepare
      - name: Run Codex
        uses: openai/codex-action@0000000000000000000000000000000000000000
        with:
          openai-api-key: ${{ secrets.CODEX_OPENAI_API_KEY }}
"""
PUBLISH_JOB = """\
  publish:
    permissions:
      contents: read
    runs-on: ubuntu-latest
    environment: ${{ inputs.publisher_environment }}
    steps:
      - name: Publish
        env:
          APP_KEY: ${{ secrets.CODEX_GITHUB_APP_PRIVATE_KEY }}
          NOTIFY_URL: ${{ secrets.CODEX_JIRA_PR_NOTIFY_URL }}
        run: echo publish
"""
VERIFY_JOB = """\
  verify:
    permissions:
      contents: read
    runs-on: ubuntu-latest
    steps:
      - name: Verify
        run: echo verify
"""
CALL_JOB = """\
  child:
    permissions:
      contents: read
    uses: ./.github/workflows/codex-child.yml
    with:
      runner_group: ${{ inputs.runner_group }}
      runner_label: ${{ inputs.runner_label }}
      model_environment: ${{ inputs.model_environment }}
      publisher_environment: ${{ inputs.publisher_environment }}
    secrets:
      CODEX_OPENAI_API_KEY: ${{ secrets.CODEX_OPENAI_API_KEY }}
      CODEX_GITHUB_APP_PRIVATE_KEY: ${{ secrets.CODEX_GITHUB_APP_PRIVATE_KEY }}
"""


def child_workflow(
    *,
    triggers: str = CALL_TRIGGER,
    model: str = MODEL_JOB,
    publish: str = PUBLISH_JOB,
    verify: str = VERIFY_JOB,
) -> str:
    return f"name: Child\n{triggers}jobs:\n{model}{publish}{verify}"


def parent_workflow(*, call: str = CALL_JOB) -> str:
    return f"name: Parent\n{CALL_TRIGGER}jobs:\n{call}"


def check(child: str | None = None, parent: str | None = None) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        workflows = Path(tmp) / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "codex-child.yml").write_text(child or child_workflow(), encoding="utf-8")
        (workflows / "codex-parent.yml").write_text(parent or parent_workflow(), encoding="utf-8")
        return subprocess.run(
            ["ruby", str(SCRIPT), tmp], capture_output=True, text=True, check=False
        )


class SharedWorkflowTrustTests(unittest.TestCase):
    def assert_violation(self, result: subprocess.CompletedProcess, message: str) -> None:
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)

    def test_repository_workflows_satisfy_the_contract(self):
        result = subprocess.run(
            ["ruby", str(SCRIPT), str(REPOSITORY_ROOT)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_compliant_workflows_pass(self):
        result = check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_model_job_requires_the_callers_runner_group_and_label(self):
        grouped = (
            "    runs-on:\n"
            "      group: ${{ inputs.runner_group }}\n"
            "      labels: ${{ inputs.runner_label }}\n"
        )
        for runner in (
            "    runs-on: ${{ inputs.runner_label }}\n",
            "    runs-on: ubuntu-latest\n",
            "    runs-on:\n      group: appreg-codex\n      labels: ${{ inputs.runner_label }}\n",
        ):
            with self.subTest(runner=runner):
                self.assert_violation(
                    check(child_workflow(model=MODEL_JOB.replace(grouped, runner))),
                    "model execution requires the caller's runner group and label",
                )

    def test_model_job_requires_the_callers_model_environment(self):
        model = MODEL_JOB.replace("    environment: ${{ inputs.model_environment }}\n", "")
        self.assert_violation(
            check(child_workflow(model=model)),
            "model jobs must run in the caller's model environment",
        )

    def test_model_job_receives_only_the_model_api_key(self):
        model = MODEL_JOB.replace(
            "        run: echo prepare\n",
            "        env:\n          APP_KEY: ${{ secrets.CODEX_GITHUB_APP_PRIVATE_KEY }}\n"
            "        run: echo prepare\n",
        )
        self.assert_violation(
            check(child_workflow(model=model)),
            "model jobs may receive only the model API key",
        )

    def test_codex_action_is_the_final_step_of_its_model_job(self):
        model = MODEL_JOB + "      - name: After Codex\n        run: echo after\n"
        self.assert_violation(
            check(child_workflow(model=model)),
            "the Codex Action must be the final step of its model job",
        )

    def test_non_model_jobs_use_github_hosted_compute(self):
        verify = VERIFY_JOB.replace(
            "    runs-on: ubuntu-latest\n",
            "    runs-on:\n      group: ${{ inputs.runner_group }}\n"
            "      labels: ${{ inputs.runner_label }}\n",
        )
        self.assert_violation(
            check(child_workflow(verify=verify)),
            "non-model jobs must use GitHub-hosted compute",
        )

    def test_publisher_credentials_require_the_publisher_environment(self):
        gated = "    environment: ${{ inputs.publisher_environment }}\n"
        for environment in ("", "    environment: ${{ inputs.model_environment }}\n"):
            with self.subTest(environment=environment):
                self.assert_violation(
                    check(child_workflow(publish=PUBLISH_JOB.replace(gated, environment))),
                    "publisher credentials require the caller's publisher environment",
                )

    def test_only_model_jobs_receive_the_model_api_key(self):
        verify = VERIFY_JOB.replace(
            "        run: echo verify\n",
            "        env:\n          KEY: ${{ secrets.CODEX_OPENAI_API_KEY }}\n"
            "        run: echo verify\n",
        )
        self.assert_violation(
            check(child_workflow(verify=verify)),
            "only model jobs may receive the model API key",
        )

    def test_credential_free_jobs_do_not_declare_an_environment(self):
        verify = VERIFY_JOB.replace(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n    environment: ${{ inputs.publisher_environment }}\n",
        )
        self.assert_violation(
            check(child_workflow(verify=verify)),
            "credential-free jobs must not declare an environment",
        )

    def test_rejects_unknown_dynamic_and_inherited_credentials(self):
        cases = {
            "unknown": (
                child_workflow(
                    verify=VERIFY_JOB.replace(
                        "        run: echo verify\n",
                        "        env:\n          T: ${{ secrets.OTHER_TOKEN }}\n"
                        "        run: echo verify\n",
                    )
                ),
                None,
                "unknown or dynamic credential reference",
            ),
            "removed Sonar token": (
                child_workflow(
                    verify=VERIFY_JOB.replace(
                        "        run: echo verify\n",
                        "        env:\n          SONAR_TOKEN: ${{ secrets.CODEX_SONAR_TOKEN }}\n"
                        "        run: echo verify\n",
                    )
                ),
                None,
                "unknown or dynamic credential reference",
            ),
            "dynamic": (
                child_workflow(
                    verify=VERIFY_JOB.replace(
                        "        run: echo verify\n",
                        "        env:\n          T: ${{ secrets[inputs.runner_label] }}\n"
                        "        run: echo verify\n",
                    )
                ),
                None,
                "unknown or dynamic credential reference",
            ),
            "inherit": (
                None,
                parent_workflow(
                    call=CALL_JOB.split("    secrets:\n")[0] + "    secrets: inherit\n"
                ),
                "jobs must not inherit every caller secret",
            ),
        }
        for name, (child, parent, message) in cases.items():
            with self.subTest(case=name):
                self.assert_violation(check(child, parent), message)

    def test_call_sites_forward_runner_and_environment_inputs_unchanged(self):
        for name in ("runner_group", "runner_label", "model_environment", "publisher_environment"):
            forwarded = f"      {name}: ${{{{ inputs.{name} }}}}\n"
            for replacement in ("", f"      {name}: fixed-value\n"):
                with self.subTest(input=name, replacement=replacement):
                    self.assert_violation(
                        check(parent=parent_workflow(call=CALL_JOB.replace(forwarded, replacement))),
                        f"must forward {name} unchanged to codex-child.yml",
                    )

    def test_call_sites_use_this_repositorys_workflows(self):
        call = CALL_JOB.replace(
            "./.github/workflows/codex-child.yml",
            "other-org/other-repo/.github/workflows/codex-child.yml@" + "1" * 40,
        )
        self.assert_violation(
            check(parent=parent_workflow(call=call)),
            "call sites must use this repository's workflows",
        )

    def test_job_permissions_are_explicit_and_read_only(self):
        explicit = "    permissions:\n      contents: read\n"
        for permissions in ("", "    permissions:\n      contents: write\n"):
            with self.subTest(permissions=permissions):
                self.assert_violation(
                    check(child_workflow(verify=VERIFY_JOB.replace(explicit, permissions))),
                    "job permissions must be explicit and read-only",
                )

    def test_component_workflows_accept_only_workflow_call(self):
        triggers = CALL_TRIGGER + "  workflow_dispatch:\n"
        self.assert_violation(
            check(child_workflow(triggers=triggers)),
            "only workflow_call may start this workflow",
        )


if __name__ == "__main__":
    unittest.main()
