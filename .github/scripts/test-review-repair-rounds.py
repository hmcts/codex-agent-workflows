#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from workflow_safety_test_support import (  # noqa: F401
    WorkflowSafetyTestCase,
    trusted_review_wrapper,
)


WORKFLOWS = Path(__file__).parents[1] / "workflows"
REVIEW = WORKFLOWS / "codex-review-feedback.yml"
ROUND = WORKFLOWS / "codex-review-repair-round.yml"
REVIEW_GENERATE = WORKFLOWS / "codex-review-generate.yml"
REVIEW_PUBLISH = WORKFLOWS / "codex-review-publish.yml"
ROUNDS = ("verification-repair-1", "verification-repair-2", "verification-repair-3")


def job(path: Path, name: str) -> str:
    content = path.read_text(encoding="utf-8")
    start = content.index(f"\n  {name}:\n") + 1
    following = re.search(r"(?m)^  [A-Za-z0-9_-]+:\s*$", content[start + 3 :])
    return content[start : start + 3 + following.start() if following else len(content)]


def value(job_text: str, key: str, indent: int = 6) -> str:
    match = re.search(rf"(?m)^{' ' * indent}{re.escape(key)}: (.*)$", job_text)
    assert match, key
    return match.group(1)


def evaluate(expression: str, inputs: dict[str, str], needs: dict[str, dict[str, str]]):
    """Evaluate the subset of GitHub expression syntax these jobs use. GitHub's
    && and || return an operand, as Python's and/or do."""
    body = expression.strip()
    if body.startswith("${{") and body.endswith("}}"):
        body = body[3:-2]
    body = re.sub(
        r"needs\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_]+)",
        lambda match: f"N({match.group(1)!r}, {match.group(2)!r})",
        body,
    )
    body = re.sub(r"inputs\.([A-Za-z0-9_]+)", lambda match: f"I({match.group(1)!r})", body)
    body = body.replace("&&", " and ").replace("||", " or ").replace("always()", "True")
    return eval(  # noqa: S307 - expressions come from this repository's workflows
        body,
        {"__builtins__": {}},
        {
            "N": lambda name, key: needs.get(name, {}).get(key, ""),
            "I": lambda key: inputs.get(key, ""),
            "fromJSON": json.loads,
            "True": True,
        },
    )


def round_outputs(number: int, *, passed: str) -> dict[str, str]:
    outputs = {"has_changes": "true", "passed": passed, "output_artifact": f"codex-review-output-repair-{number}"}
    if passed == "true":
        outputs["verified_artifact"] = f"codex-review-verified-repair-{number}"
    if passed == "false":
        outputs["failure_artifact"] = f"codex-review-verification-failure-repair-{number}"
    return outputs


class ReviewRepairRoundTests(unittest.TestCase):
    def setUp(self):
        self.jobs = {name: job(REVIEW, name) for name in (*ROUNDS, "publish", "terminal-failure")}

    def scenario(self, rounds: str, initial_passed: str, *round_results: dict[str, str]):
        needs = {
            "intake": {"is_codex_pr": "true"},
            "generate-and-verify": {
                "passed": initial_passed,
                "failure_artifact": "codex-review-verification-failure",
            },
        }
        for name, outputs in zip(ROUNDS, round_results):
            needs[name] = outputs
        inputs = {"review_repair_rounds": rounds}

        def run(name):
            return bool(evaluate(value(self.jobs[name], "if", 4), inputs, needs))

        def publish(key):
            return evaluate(value(self.jobs["publish"], key), inputs, needs)

        def terminal(key):
            return evaluate(value(self.jobs["terminal-failure"], key), inputs, needs)

        return run, publish, terminal

    def test_default_behaves_exactly_as_before(self):
        for initial_passed in ("true", "false", ""):
            with self.subTest(initial_passed=initial_passed):
                run, publish, terminal = self.scenario("0", initial_passed)
                self.assertFalse(run("verification-repair-1"))
                self.assertEqual(run("publish"), initial_passed == "true")
                self.assertEqual(publish("verification_passed"), initial_passed)
                self.assertEqual(publish("output_artifact"), "codex-review-output")
                self.assertEqual(publish("verified_artifact"), "codex-review-verified")
                self.assertEqual(terminal("verification_passed"), initial_passed)
                self.assertEqual(terminal("failure_artifact"), "codex-review-verification-failure")

    def test_rounds_run_only_after_a_failed_verification(self):
        run, _, _ = self.scenario("3", "true")
        self.assertFalse(run("verification-repair-1"))
        run, _, _ = self.scenario("3", "")
        self.assertFalse(run("verification-repair-1"))
        run, _, _ = self.scenario("3", "false")
        self.assertTrue(run("verification-repair-1"))

    def test_a_passing_round_is_published(self):
        run, publish, terminal = self.scenario(
            "3", "false", round_outputs(1, passed="false"), round_outputs(2, passed="true")
        )
        self.assertTrue(run("verification-repair-2"))
        self.assertFalse(run("verification-repair-3"))
        self.assertTrue(run("publish"))
        self.assertEqual(publish("verification_passed"), "true")
        self.assertEqual(publish("output_artifact"), "codex-review-output-repair-2")
        self.assertEqual(publish("verified_artifact"), "codex-review-verified-repair-2")
        self.assertEqual(terminal("verification_passed"), "true")

    def test_rounds_stop_at_the_configured_count(self):
        run, _, _ = self.scenario(
            "2", "false", round_outputs(1, passed="false"), round_outputs(2, passed="false")
        )
        self.assertTrue(run("verification-repair-2"))
        self.assertFalse(run("verification-repair-3"))
        run, _, _ = self.scenario("1", "false", round_outputs(1, passed="false"))
        self.assertFalse(run("verification-repair-2"))

    def test_exhausted_rounds_report_the_last_failure(self):
        results = [round_outputs(number, passed="false") for number in (1, 2, 3)]
        run, _, terminal = self.scenario("3", "false", *results)
        self.assertTrue(run("verification-repair-3"))
        self.assertFalse(run("publish"))
        self.assertEqual(terminal("verification_passed"), "false")
        self.assertEqual(terminal("failure_artifact"), "codex-review-verification-failure-repair-3")

    def test_a_round_that_never_verified_ends_the_chain_on_the_last_real_failure(self):
        unverified = {"has_changes": "", "passed": ""}
        run, _, terminal = self.scenario("3", "false", round_outputs(1, passed="false"), unverified)
        self.assertFalse(run("verification-repair-3"))
        self.assertEqual(terminal("failure_artifact"), "codex-review-verification-failure-repair-1")
        broken = {"has_changes": "true", "passed": "false"}
        run, _, _ = self.scenario("3", "false", broken)
        self.assertFalse(run("verification-repair-2"))

    def test_each_round_starts_from_the_previous_attempt(self):
        self.assertEqual(value(self.jobs["verification-repair-1"], "input_artifact"), "codex-review-output")
        self.assertEqual(
            value(self.jobs["verification-repair-1"], "failure_artifact"),
            "${{ needs.generate-and-verify.outputs.failure_artifact }}",
        )
        for number in (2, 3):
            previous = f"verification-repair-{number - 1}"
            current = self.jobs[f"verification-repair-{number}"]
            self.assertEqual(value(current, "attempt"), f'"{number}"')
            self.assertEqual(value(current, "input_artifact"), f"${{{{ needs.{previous}.outputs.output_artifact }}}}")
            self.assertEqual(value(current, "failure_artifact"), f"${{{{ needs.{previous}.outputs.failure_artifact }}}}")
        for name in ROUNDS:
            round_job = self.jobs[name]
            self.assertIn("uses: ./.github/workflows/codex-review-repair-round.yml", round_job)
            self.assertEqual(value(round_job, "max_attempts"), "${{ inputs.review_repair_rounds }}")
            self.assertEqual(value(round_job, "source_artifact"), "codex-review-verification-source")
            for permission in ("contents: read", "pull-requests: read", "issues: read"):
                self.assertIn(permission, round_job)

    def test_rounds_verify_against_the_review_source_bundle(self):
        source_job = job(REVIEW_GENERATE, "prepare-review-verification-source")
        self.assertIn("artifact: codex-review-verification-source", source_job)
        self.assertIn("name: codex-review-verification-source\n", source_job)

    def test_public_input_and_its_validation(self):
        content = REVIEW.read_text(encoding="utf-8")
        block = content[content.index("      review_repair_rounds:\n") :]
        block = block[: block.index("      jira_notify_timeout_seconds:\n")]
        self.assertIn('default: "0"', block)
        validation = job(REVIEW, "validate-runner-target")
        self.assertIn("REVIEW_REPAIR_ROUNDS: ${{ inputs.review_repair_rounds }}", validation)
        self.assertIn("0|1|2|3) ;;", validation)


class ReviewRepairRoundWorkflowTests(unittest.TestCase):
    def test_repair_applies_the_previous_patch_to_the_verified_head(self):
        action = job(ROUND, "review-repair-action")
        self.assertNotIn("SOURCE_INCLUDES_INPUT_PATCH", action)
        self.assertIn("REPAIR_ATTEMPT: ${{ inputs.attempt }}", action)
        self.assertIn("MAX_CODEX_REPAIR_ATTEMPTS: ${{ inputs.max_attempts }}", action)
        self.assertIn("EXPECTED_HEAD_SHA: ${{ inputs.head_sha }}", action)
        self.assertIn("codex-pr-review-repair.sh", action)
        collect = job(ROUND, "collect-review-repair")
        self.assertIn("codex-pr-review-repair-collect.sh", collect)
        self.assertIn("HEAD_SHA: ${{ inputs.head_sha }}", collect)

    def test_verification_names_its_artifacts_only_for_its_outcome(self):
        verify = job(ROUND, "verify-review-repair")
        self.assertIn("if: needs.collect-review-repair.outputs.has_changes == 'true'", verify)
        self.assertIn(
            "verified_artifact: ${{ steps.verify.outputs.passed == 'true' && format('codex-review-verified-repair-{0}', inputs.attempt) || '' }}",
            verify,
        )
        self.assertIn(
            "failure_artifact: ${{ steps.verify.outputs.passed == 'false' && format('codex-review-verification-failure-repair-{0}', inputs.attempt) || '' }}",
            verify,
        )
        self.assertIn("CODEX_VERIFIED_ARTIFACT: codex-review-verified-repair-${{ inputs.attempt }}", verify)
        self.assertIn("CODEX_FAILURE_ARTIFACT: codex-review-verification-failure-repair-${{ inputs.attempt }}", verify)
        self.assertIn("name: ${{ inputs.source_artifact }}", verify)
        self.assertIn("permissions: {}", verify)

    def test_review_publication_takes_the_selected_artifacts(self):
        content = REVIEW_PUBLISH.read_text(encoding="utf-8")
        self.assertIn("      output_artifact:\n        required: false\n        default: codex-review-output\n", content)
        self.assertIn("      verified_artifact:\n        required: false\n        default: codex-review-verified\n", content)
        publisher = job(REVIEW_PUBLISH, "codex-review-publish")
        self.assertNotIn("name: codex-review-output\n", publisher)
        self.assertNotIn("name: codex-review-verified\n", publisher)
        self.assertEqual(publisher.count("name: ${{ inputs.verified_artifact }}"), 2)
        self.assertEqual(publisher.count("name: ${{ inputs.output_artifact }}"), 1)
        self.assertIn("output_artifact: ${{ inputs.output_artifact }}", publisher)


class TrustedReviewRepairRoundTests(WorkflowSafetyTestCase):
    def test_trusted_review_wrapper_may_request_repairs(self):
        wrapper = trusted_review_wrapper().replace(
            '      java_version: "17"\n',
            '      java_version: "17"\n      review_repair_rounds: "3"\n',
            1,
        )
        self.assertIn('review_repair_rounds: "3"', wrapper)
        completed = self.run_check(
            {"codex_pr_review.yml": wrapper},
            trusted_workflows={"codex_pr_review.yml": wrapper},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
