#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parent
ROOT = SCRIPTS.parents[1]
UPDATER = ROOT / ".github" / "workflows" / "update-callers.yml"


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


SELECTOR = load("select_caller_repositories", "select-caller-repositories.py")
MIGRATOR = load("update_caller_workflow", "update-caller-workflow.py")

JUROR = {
    "hmcts/juror-er-portal",
    "hmcts/juror-public",
    "hmcts/juror-bureau",
    "hmcts/juror-api",
    "hmcts/juror-scheduler-execution",
    "hmcts/juror-pnc",
    "hmcts/juror-scheduler-api",
}
APPREG = {"hmcts/appreg-api", "hmcts/appreg-frontend"}
OLD_SHA = "1" * 40
NEW_SHA = "2" * 40


def review_caller(extra: str = "") -> str:
    return f"""name: Codex PR Review Feedback
on:
  issue_comment:
    types: [created]
jobs:
  review:
    if: ${{{{ github.event.issue.pull_request && github.event.comment.body == '/codex-review' && contains(fromJSON('["COLLABORATOR","MEMBER","OWNER"]'), github.event.comment.author_association) }}}}
    uses: hmcts/codex-agent-workflows/.github/workflows/codex-review-feedback.yml@{OLD_SHA}
    with:
{extra}      runner_label: codex-frontend-azure-aks
      runner_group: appreg-codex
      github_app_client_id: ${{{{ vars.CODEX_GITHUB_APP_CLIENT_ID }}}}
    secrets:
      CODEX_OPENAI_API_KEY: ${{{{ secrets.CODEX_OPENAI_API_KEY }}}}
      CODEX_GITHUB_APP_PRIVATE_KEY: ${{{{ secrets.CODEX_GITHUB_APP_PRIVATE_KEY }}}}
      CODEX_JIRA_PR_NOTIFY_URL: ${{{{ secrets.CODEX_JIRA_PR_NOTIFY_URL }}}}
"""


APPREG_ENVIRONMENTS = {
    "model_environment": "codex-model",
    "publisher_environment": "codex-publisher",
}


class CallerRegistryTests(unittest.TestCase):
    def test_registry_lists_both_teams_with_their_runner_groups(self):
        callers = {caller["repository"]: caller for caller in SELECTOR.load_callers()}
        self.assertEqual(set(callers), JUROR | APPREG)
        for repository in JUROR:
            caller = callers[repository]
            self.assertEqual(
                (caller["set"], caller["branch"], caller["runner_group"]),
                ("juror", "master", "juror-codex"),
            )
            self.assertEqual(caller["model_environment"], "")
            self.assertEqual(caller["publisher_environment"], "")
        for repository in APPREG:
            caller = callers[repository]
            self.assertEqual(
                (caller["set"], caller["branch"], caller["runner_group"]),
                ("appreg", "master", "appreg-codex"),
            )
            self.assertEqual(caller["model_environment"], "codex-model")
            self.assertEqual(caller["publisher_environment"], "codex-publisher")

    def test_selection_by_caller_set(self):
        callers = SELECTOR.load_callers()
        self.assertEqual({c["repository"] for c in SELECTOR.select(callers, "juror")}, JUROR)
        self.assertEqual({c["repository"] for c in SELECTOR.select(callers, "appreg")}, APPREG)
        self.assertEqual({c["repository"] for c in SELECTOR.select(callers, "all")}, JUROR | APPREG)
        with self.assertRaisesRegex(SELECTOR.CallerRegistryError, "callers must be"):
            SELECTOR.select(callers, "everyone")

    def test_main_prints_a_job_matrix(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            status = SELECTOR.main(["--callers", "appreg"])
        self.assertEqual(status, 0)
        key, _, payload = stdout.getvalue().strip().partition("=")
        self.assertEqual(key, "matrix")
        matrix = json.loads(payload)
        self.assertEqual({entry["repository"] for entry in matrix["include"]}, APPREG)

    def test_invalid_registries_are_refused(self):
        valid = {
            "repository": "hmcts/example",
            "set": "juror",
            "branch": "master",
            "runner_group": "juror-codex",
            "model_environment": "",
            "publisher_environment": "",
        }
        cases = {
            "exactly": {**valid, "extra": "x"},
            "not an HMCTS repository": {**valid, "repository": "other/example"},
            "unknown caller set": {**valid, "set": "platops"},
            "runner-targets.json lacks": {**valid, "runner_group": "other-codex"},
            "both environments or neither": {**valid, "model_environment": "codex-model"},
            "invalid model_environment": {
                **valid,
                "model_environment": "codex model",
                "publisher_environment": "codex-publisher",
            },
            "invalid branch": {**valid, "branch": "-x"},
        }
        runner_targets = ROOT / ".github" / "config" / "runner-targets.json"
        with tempfile.TemporaryDirectory() as temporary_directory:
            registry = Path(temporary_directory) / "callers.json"
            for diagnostic, caller in cases.items():
                with self.subTest(diagnostic=diagnostic):
                    registry.write_text(json.dumps({"callers": [caller]}), encoding="utf-8")
                    with self.assertRaisesRegex(SELECTOR.CallerRegistryError, diagnostic):
                        SELECTOR.load_callers(registry, runner_targets)
            registry.write_text(json.dumps({"callers": [valid, valid]}), encoding="utf-8")
            with self.assertRaisesRegex(SELECTOR.CallerRegistryError, "listed twice"):
                SELECTOR.load_callers(registry, runner_targets)


class CallerEnvironmentTests(unittest.TestCase):
    def update(self, content: str, environments=APPREG_ENVIRONMENTS) -> str:
        return MIGRATOR.update_caller(
            content,
            "codex_pr_review.yml",
            NEW_SHA,
            requires_runner_group=True,
            runner_group="appreg-codex",
            environments=environments,
        )

    def test_missing_environments_are_added_in_order(self):
        updated = self.update(review_caller())
        self.assertIn(
            "    with:\n      model_environment: codex-model\n      publisher_environment: codex-publisher\n",
            updated,
        )
        self.assertIn(f"codex-review-feedback.yml@{NEW_SHA}", updated)
        self.assertEqual(self.update(updated), updated)

    def test_matching_environments_are_kept(self):
        caller = review_caller(
            "      model_environment: codex-model\n      publisher_environment: 'codex-publisher'\n"
        )
        updated = self.update(caller)
        self.assertEqual(updated.count("model_environment:"), 1)
        self.assertEqual(updated.count("publisher_environment:"), 1)

    def test_a_different_environment_is_refused(self):
        caller = review_caller("      publisher_environment: production\n")
        with self.assertRaisesRegex(
            MIGRATOR.CallerContractError,
            "caller publisher_environment production does not match the expected codex-publisher",
        ):
            self.update(caller)

    def test_no_expected_environment_leaves_the_caller_alone(self):
        caller = review_caller()
        updated = self.update(caller, {"model_environment": "", "publisher_environment": ""})
        self.assertNotIn("_environment", updated)
        with_environment = review_caller("      model_environment: codex-model\n")
        self.assertIn("model_environment: codex-model", self.update(with_environment, {}))

    def test_invalid_expectations_are_refused(self):
        with self.assertRaisesRegex(MIGRATOR.CallerContractError, "invalid expected model_environment"):
            self.update(review_caller(), {"model_environment": "codex model"})
        with self.assertRaisesRegex(MIGRATOR.CallerContractError, "unsupported environment input"):
            self.update(review_caller(), {"deploy_environment": "x"})


class UpdaterWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.content = UPDATER.read_text(encoding="utf-8")

    def test_dispatch_names_a_caller_set(self):
        triggers = self.content[self.content.index("\non:\n") : self.content.index("\npermissions:")]
        callers = triggers[triggers.index("      callers:\n") :]
        self.assertIn("required: true", callers)
        self.assertIn("type: choice", callers)
        for option in ("juror", "appreg", "all"):
            self.assertIn(f"          - {option}\n", callers)

    def test_matrix_comes_from_the_registry(self):
        self.assertIn(
            'python3 -I .github/scripts/select-caller-repositories.py --callers "${CALLERS}" >>"$GITHUB_OUTPUT"',
            self.content,
        )
        self.assertIn("CALLERS: ${{ inputs.callers }}", self.content)
        self.assertIn("matrix: ${{ fromJSON(needs.select-callers.outputs.matrix) }}", self.content)
        self.assertNotIn("hmcts/juror-", self.content)
        self.assertNotIn("RUNNER_GROUP: juror-codex", self.content)
        self.assertNotIn("TARGET_BRANCH: master", self.content)
        for variable, field in (
            ("TARGET_REPOSITORY", "repository"),
            ("TARGET_BRANCH", "branch"),
            ("RUNNER_GROUP", "runner_group"),
            ("MODEL_ENVIRONMENT", "model_environment"),
            ("PUBLISHER_ENVIRONMENT", "publisher_environment"),
        ):
            self.assertIn(f"{variable}: ${{{{ matrix.{field} }}}}", self.content)

    def test_migrator_enforces_the_callers_environments(self):
        self.assertIn('--model-environment "${MODEL_ENVIRONMENT}"', self.content)
        self.assertIn('--publisher-environment "${PUBLISHER_ENVIRONMENT}"', self.content)

    def test_callers_not_yet_on_the_shared_workflows_are_skipped_before_any_change(self):
        skip = self.content.index("does not call the shared workflows yet; skipping this release.")
        migrate = self.content.index("python3 -I .github/scripts/update-caller-workflow.py")
        branch = self.content.index('gh api --method POST "repos/${TARGET_REPOSITORY}/git/refs"')
        self.assertLess(skip, migrate)
        self.assertLess(skip, branch)

    def test_pull_request_body_is_team_neutral(self):
        self.assertIn('--body "Pins the Codex caller workflows', self.content)
        self.assertNotIn("Juror Codex caller", self.content)


if __name__ == "__main__":
    unittest.main()
