#!/usr/bin/env python3

from __future__ import annotations

import re
import unittest
from pathlib import Path

from workflow_safety_test_support import (  # noqa: F401
    WorkflowSafetyTestCase,
    trusted_review_wrapper,
)


ROOT = Path(__file__).parents[1]
WORKFLOW_ROOT = ROOT / "workflows"
IMPLEMENT = WORKFLOW_ROOT / "codex-implement.yml"
REVIEW = WORKFLOW_ROOT / "codex-review-feedback.yml"
VERIFICATION = WORKFLOW_ROOT / "codex-verification.yml"
VERIFY_INITIAL = WORKFLOW_ROOT / "codex-verify-initial.yml"
REPAIR_ROUND = WORKFLOW_ROOT / "codex-repair-round.yml"
PUBLISH = WORKFLOW_ROOT / "codex-publish.yml"
POST_VERIFY = WORKFLOW_ROOT / "codex-post-verify.yml"
POST_REPAIR = WORKFLOW_ROOT / "codex-post-repair.yml"
REVIEW_GENERATE = WORKFLOW_ROOT / "codex-review-generate.yml"
REVIEW_PUBLISH = WORKFLOW_ROOT / "codex-review-publish.yml"
REVIEW_REPAIR = WORKFLOW_ROOT / "codex-review-repair.yml"

FORWARD_FORMATTER = "formatter: ${{ inputs.formatter }}"
FORWARD_NODE_VERSION_FILE = "node_version_file: ${{ inputs.node_version_file }}"
FORWARD_FAST_COMMAND = "frontend_fast_command: ${{ inputs.frontend_fast_command }}"
FORMATTER_ENV = "CODEX_FORMATTER: ${{ inputs.formatter }}"
FAST_COMMAND_ENV = "CODEX_FRONTEND_FAST_COMMAND: ${{ inputs.frontend_fast_command }}"
ADOPTER = "codex-adopt-formatted-patch.py"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def job(path: Path, name: str) -> str:
    content = text(path)
    start = content.index(f"\n  {name}:\n") + 1
    following = re.search(r"(?m)^  [A-Za-z0-9_-]+:\s*$", content[start + 3 :])
    end = start + 3 + following.start() if following else len(content)
    return content[start:end]


def step(job_text: str, name: str) -> str:
    start = job_text.index(f"      - name: {name}\n")
    following = job_text.find("\n      - name: ", start + 1)
    return job_text[start : following if following != -1 else len(job_text)]


def input_block(path: Path, name: str) -> str:
    content = text(path)
    inputs = content[content.index("    inputs:\n") : content.index("\njobs:\n")]
    start = inputs.index(f"      {name}:\n")
    following = re.search(r"(?m)^      [A-Za-z0-9_]+:\s*$", inputs[start + 7 :])
    end = start + 7 + following.start() if following else len(inputs)
    return inputs[start:end]


class FrontendVerificationOptionTests(unittest.TestCase):
    def test_public_workflows_offer_opt_in_inputs_with_unchanged_defaults(self):
        for workflow in (IMPLEMENT, REVIEW):
            with self.subTest(workflow=workflow.name):
                self.assertIn("default: none", input_block(workflow, "formatter"))
                self.assertIn("default: ''", input_block(workflow, "node_version_file"))
                self.assertIn("default: ''", input_block(workflow, "frontend_fast_command"))
                for name in ("formatter", "node_version_file", "frontend_fast_command"):
                    self.assertIn("required: false", input_block(workflow, name))
                    self.assertIn("description:", input_block(workflow, name))

    def test_public_workflows_refuse_an_unknown_formatter_before_any_stage(self):
        for workflow in (IMPLEMENT, REVIEW):
            with self.subTest(workflow=workflow.name):
                validation = job(workflow, "validate-runner-target")
                check = step(validation, "Refuse an unknown formatter")
                self.assertIn("FORMATTER: ${{ inputs.formatter }}", check)
                self.assertIn("none|prettier) ;;", check)
                self.assertIn("exit 1", check)

    def test_each_stage_receives_only_the_options_it_uses(self):
        expected = {
            (IMPLEMENT, "verify-and-repair"): {"formatter", "node_version_file", "frontend_fast_command"},
            (IMPLEMENT, "publish"): {"formatter"},
            (IMPLEMENT, "verify-published-pr"): {"node_version_file", "frontend_fast_command"},
            (IMPLEMENT, "repair-published-pr"): {"formatter", "node_version_file", "frontend_fast_command"},
            (IMPLEMENT, "plan"): set(),
            (IMPLEMENT, "generate"): set(),
            (VERIFICATION, "initial"): {"formatter", "node_version_file", "frontend_fast_command"},
            (VERIFICATION, "repair-1"): {"formatter", "node_version_file", "frontend_fast_command"},
            (VERIFICATION, "repair-2"): {"formatter", "node_version_file", "frontend_fast_command"},
            (VERIFICATION, "repair-3"): {"formatter", "node_version_file", "frontend_fast_command"},
            (REVIEW, "generate-and-verify"): {"formatter", "node_version_file", "frontend_fast_command"},
            (REVIEW, "publish"): {"formatter"},
            (REVIEW, "repair"): {"formatter", "node_version_file", "frontend_fast_command"},
            (REVIEW, "terminal-failure"): set(),
        }
        forwards = {
            "formatter": FORWARD_FORMATTER,
            "node_version_file": FORWARD_NODE_VERSION_FILE,
            "frontend_fast_command": FORWARD_FAST_COMMAND,
        }
        for (workflow, name), options in expected.items():
            caller = job(workflow, name)
            for option, forward in forwards.items():
                with self.subTest(workflow=workflow.name, job=name, option=option):
                    if option in options:
                        self.assertIn(forward, caller)
                    else:
                        self.assertNotIn(forward, caller)

    def test_every_node_setup_can_read_the_version_file(self):
        setups = 0
        for workflow in WORKFLOW_ROOT.glob("codex-*.yml"):
            content = text(workflow)
            self.assertNotIn("node-version: ${{ inputs.node_version }}\n", content, workflow.name)
            for match in re.finditer(r"uses: actions/setup-node@[0-9a-f]{40} # v6\n        with:\n(?P<with>(?:          .*\n)+)", content):
                setups += 1
                self.assertEqual(
                    match.group("with"),
                    "          node-version: ${{ inputs.node_version_file == '' && inputs.node_version || '' }}\n"
                    "          node-version-file: ${{ inputs.node_version_file }}\n",
                    workflow.name,
                )
        self.assertEqual(setups, 7)

    def test_verifiers_format_only_before_publication(self):
        formatting = {
            (VERIFY_INITIAL, "verify-codex-output", "Verify Codex patch"),
            (REPAIR_ROUND, "verify-repair", "Verify Codex patch"),
            (POST_REPAIR, "verify-published-pr-repair-1", "Verify repaired Codex patch before re-publish"),
            (REVIEW_GENERATE, "codex-review-verify", "Verify Codex review patch"),
            (REVIEW_REPAIR, "codex-review-external-repair-verify", "Verify external-status repair patch"),
        }
        published = {
            (POST_VERIFY, "verify-published-pr-patch", "Verify published PR patch without credentials"),
            (POST_REPAIR, "verify-published-pr-1-patch", "Verify repaired published PR patch without credentials"),
        }
        for workflow, name, step_name in formatting | published:
            with self.subTest(workflow=workflow.name, step=step_name):
                verify = step(job(workflow, name), step_name)
                self.assertIn(FAST_COMMAND_ENV, verify)
                if (workflow, name, step_name) in formatting:
                    self.assertIn(FORMATTER_ENV, verify)
                else:
                    self.assertNotIn("CODEX_FORMATTER", verify)

        structural = step(job(PUBLISH, "prepare-draft-publication"), "Validate latest patch structure without repository execution")
        self.assertNotIn("CODEX_FORMATTER", structural)
        self.assertIn('SKIP_LOCAL_PIPELINE: "true"', structural)

    def test_verified_artifacts_carry_the_verified_patch(self):
        for workflow, name, step_name in (
            (VERIFY_INITIAL, "verify-codex-output", "Upload verified Codex output"),
            (REPAIR_ROUND, "verify-repair", "Upload verified Codex output"),
            (POST_REPAIR, "verify-published-pr-repair-1", "Upload verified repaired Codex output"),
            (REVIEW_GENERATE, "codex-review-verify", "Upload verified Codex review output"),
            (REVIEW_REPAIR, "codex-review-external-repair-verify", "Upload verified external-status repair"),
        ):
            with self.subTest(workflow=workflow.name, step=step_name):
                upload = step(job(workflow, name), step_name)
                self.assertIn("${{ runner.temp }}/codex-output/changes.patch\n", upload)

    def test_publish_jobs_adopt_the_formatted_patch_before_the_credential_gate(self):
        for workflow, name, verified_artifact, uploads in (
            (PUBLISH, "publish-pr", "${{ env.CODEX_VERIFIED_ARTIFACT }}", True),
            (POST_REPAIR, "publish-published-pr-repair-1", "${{ env.CODEX_VERIFIED_ARTIFACT }}", True),
            (REVIEW_PUBLISH, "codex-review-publish", "codex-review-verified", False),
            (REVIEW_REPAIR, "codex-review-external-republish", "${{ env.CODEX_VERIFIED_ARTIFACT }}", False),
        ):
            with self.subTest(workflow=workflow.name, job=name):
                publisher = job(workflow, name)
                output = publisher.index("path: ${{ runner.temp }}/codex-output\n")
                download = publisher.index("path: ${{ runner.temp }}/codex-formatted")
                adopt = publisher.index(ADOPTER)
                materialize = publisher.index("codex-prepare-policy-candidate.sh")
                gate = publisher.index("check-codex-pr-safety.rb")
                token = publisher.index("actions/create-github-app-token@")
                self.assertLess(output, download)
                self.assertLess(download, adopt)
                self.assertLess(adopt, materialize)
                self.assertLess(materialize, gate)
                self.assertLess(gate, token)
                formatted = publisher[download - 400 : materialize]
                self.assertIn(f"name: {verified_artifact}\n", formatted)
                self.assertEqual(formatted.count("if: inputs.formatter == 'prettier'"), 3 if uploads else 2)
                self.assertIn('--output-dir "$RUNNER_TEMP/codex-output"', formatted)
                self.assertIn('--verified-dir "$RUNNER_TEMP/codex-formatted"', formatted)
                if uploads:
                    self.assertIn("name: ${{ env.CODEX_PUBLISHED_OUTPUT_ARTIFACT }}", formatted)
                    self.assertIn(
                        'echo "output_artifact=${CODEX_PUBLISHED_OUTPUT_ARTIFACT}" >>"$GITHUB_OUTPUT"',
                        publisher,
                    )
                    self.assertIn("format('{0}-formatted', ", publisher)

        self.assertNotIn(ADOPTER, job(PUBLISH, "publish-draft-pr"))

    def test_verifiers_format_after_the_gate_and_record_the_formatted_patch(self):
        for name in ("codex-jira-verify.sh", "codex-pr-review-verify.sh"):
            with self.subTest(script=name):
                content = text(ROOT / "scripts" / name)
                gate = content.index('run_sanitized ruby --disable-gems "${safety_gate_path}"')
                formatter = content.index('run_sanitized env CODEX_FORMATTER="${formatter}" bash')
                rehash = content.index('patch_sha="$(file_sha256 "${patch_path}")"', formatter)
                guardrails = content.index("\ndetect_guardrail_changes\n")
                recorded = content.index('echo "patch_sha=${patch_sha}"')
                self.assertLess(gate, formatter)
                self.assertLess(formatter, rehash)
                self.assertLess(rehash, guardrails)
                self.assertLess(rehash, recorded)
                self.assertIn(
                    'sanitized_env+=("FRONTEND_FAST_COMMAND=${CODEX_FRONTEND_FAST_COMMAND}")',
                    content,
                )

        review = text(ROOT / "scripts" / "codex-pr-review-verify.sh")
        copy = review.index('cp "${formatter_source_path}" "${trusted_formatter_path}"')
        verify = review.index('verify_trusted_file "${trusted_formatter_path}" "${trusted_formatter_sha}" "formatter"')
        execute = review.index('bash "${trusted_formatter_path}" "${patch_path}"')
        self.assertLess(copy, verify)
        self.assertLess(verify, execute)

    def test_review_bundles_package_the_trusted_formatter(self):
        for workflow, build, upload, verify_job, verify_step in (
            (
                REVIEW_GENERATE,
                "Build credential-free review source",
                "Upload credential-free review source",
                "codex-review-verify",
                "Verify Codex review patch",
            ),
            (
                REVIEW_REPAIR,
                "Build credential-free published review source",
                "Upload credential-free published review source",
                "codex-review-external-repair-verify",
                "Verify external-status repair patch",
            ),
        ):
            with self.subTest(workflow=workflow.name):
                content = text(workflow)
                self.assertIn(
                    'cp ${CODEX_RUNTIME_PATH}/.github/scripts/codex-format-changed-files.sh "$RUNNER_TEMP/trusted-codex-format-changed-files.sh"',
                    step(content, build),
                )
                self.assertIn(
                    "${{ runner.temp }}/trusted-codex-format-changed-files.sh\n",
                    step(content, upload),
                )
                self.assertIn(
                    "TRUSTED_FORMATTER_PATH: ${{ runner.temp }}/codex-review-verification-source/trusted-codex-format-changed-files.sh",
                    step(job(workflow, verify_job), verify_step),
                )


class TrustedReviewOptionTests(WorkflowSafetyTestCase):
    def test_trusted_review_wrapper_may_pass_the_frontend_options(self):
        wrapper = trusted_review_wrapper().replace(
            '      java_version: "17"\n',
            '      java_version: "17"\n'
            "      formatter: prettier\n"
            "      node_version_file: .nvmrc\n"
            "      frontend_fast_command: yarn lint\n",
            1,
        )
        self.assertIn("formatter: prettier", wrapper)
        completed = self.run_check(
            {"codex_pr_review.yml": wrapper},
            trusted_workflows={"codex_pr_review.yml": wrapper},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
