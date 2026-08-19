#!/usr/bin/env python3

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
IMPLEMENT_WORKFLOW = ROOT / "workflows" / "codex-implement.yml"
REVIEW_WORKFLOW = ROOT / "workflows" / "codex-review-feedback.yml"
UPDATER_WORKFLOW = ROOT / "workflows" / "update-callers.yml"
PREFLIGHT = ROOT / "scripts" / "codex-runner-preflight.sh"
ROLLOUT = ROOT.parent / "docs" / "juror-rollout.md"


class WorkflowContractTests(unittest.TestCase):
    def test_both_reusable_workflows_require_jira_callback_secret(self):
        for workflow in (IMPLEMENT_WORKFLOW, REVIEW_WORKFLOW):
            content = workflow.read_text(encoding="utf-8")
            self.assertIn("CODEX_JIRA_PR_NOTIFY_URL:", content)
            self.assertIn("required: true", content)

    def test_validated_plan_bundle_is_retained(self):
        content = IMPLEMENT_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("name: codex-validated-plan", content)
        self.assertIn("path: ${{ runner.temp }}/codex-plan", content)
        self.assertIn("retention-days: 30", content)

    def test_planning_failure_always_attempts_jira_callback(self):
        content = IMPLEMENT_WORKFLOW.read_text(encoding="utf-8")
        start = content.index("  codex-plan-failed:")
        end = content.index("\n  codex-plan-blocked:", start)
        job = content[start:end]
        self.assertIn("always()", job)
        self.assertIn("needs.codex-plan-action.result != 'success'", job)
        self.assertIn("needs.validate-codex-plan.result != 'success'", job)
        self.assertIn("notify-jira-automation.py", job)
        self.assertIn("--status failed", job)

    def test_implementation_generation_failure_always_attempts_jira_callback(self):
        content = IMPLEMENT_WORKFLOW.read_text(encoding="utf-8")
        start = content.index("  codex-generation-terminal-failed:")
        end = content.index("\n  codex-no-changes:", start)
        job = content[start:end]
        self.assertIn("always()", job)
        self.assertIn(
            "needs: [validate-codex-plan, codex-generate-action, codex-generate]", job
        )
        self.assertIn("needs.codex-generate.outputs.has_changes != 'true'", job)
        self.assertIn("needs.codex-generate.outputs.has_changes != 'false'", job)
        self.assertIn("permissions: {}", job)
        self.assertIn("--status failed", job)

    def test_release_updater_uses_contract_migrator(self):
        content = UPDATER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(".github/scripts/update-caller-workflow.py", content)
        self.assertNotIn("sed -E", content)

    def test_release_updater_only_skips_explicit_contents_404_and_retries(self):
        content = UPDATER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("schedule:", content)
        self.assertIn("cron: '17 */6 * * *'", content)
        self.assertIn("HTTP 404", content)
        self.assertIn("only an explicit Contents API HTTP 404", content)
        lookup_start = content.index('metadata="$(gh api')
        lookup = content[
            lookup_start : content.index('blob_sha="$(jq', lookup_start)
        ]
        self.assertNotIn("|| true", lookup)
        self.assertIn("exit 1", lookup)

    def test_rollout_requires_post_onboarding_reviewed_release_dispatch(self):
        content = ROLLOUT.read_text(encoding="utf-8")
        merge_callers = content.index("Merge the seven caller onboarding PRs")
        dispatch = content.index("manually dispatch `Update caller workflow pins`")
        activation = content.index("Enable the Jira dispatch rule")
        self.assertLess(merge_callers, dispatch)
        self.assertLess(dispatch, activation)
        self.assertIn("pin exactly the recorded release SHA", content)

    def test_model_preflight_never_executes_repository_gradle_wrapper(self):
        content = PREFLIGHT.read_text(encoding="utf-8")
        self.assertNotIn("./gradlew", content)
        repository_commands = [
            line.strip()
            for line in content.splitlines()
            if line.strip().startswith(("./", "bash ./", "sh ./"))
        ]
        self.assertEqual(repository_commands, [])

    def test_codex_action_is_final_in_every_model_facing_job(self):
        for workflow in (IMPLEMENT_WORKFLOW, REVIEW_WORKFLOW):
            content = workflow.read_text(encoding="utf-8")
            job_starts = [
                index
                for index, line in enumerate(content.splitlines())
                if line.startswith("  ")
                and not line.startswith("    ")
                and line.endswith(":")
            ]
            lines = content.splitlines()
            for position, start in enumerate(job_starts):
                end = (
                    job_starts[position + 1]
                    if position + 1 < len(job_starts)
                    else len(lines)
                )
                job = lines[start:end]
                if not any("uses: openai/codex-action@" in line for line in job):
                    continue
                steps = [
                    index
                    for index, line in enumerate(job)
                    if line.startswith("      - name:")
                ]
                self.assertTrue(steps, workflow)
                self.assertIn(
                    "uses: openai/codex-action@",
                    "\n".join(job[steps[-1] :]),
                    f"Codex Action is not the final step in {workflow}:{lines[start].strip()}",
                )

    def test_all_publication_jobs_gate_pr_oidc_before_token_minting(self):
        expected_jobs = {
            IMPLEMENT_WORKFLOW: (
                "publish-draft-pr",
                "publish-pr",
                "publish-published-pr-repair-1",
            ),
            REVIEW_WORKFLOW: (
                "codex-review-publish",
                "codex-review-external-republish",
            ),
        }
        for workflow, job_names in expected_jobs.items():
            content = workflow.read_text(encoding="utf-8")
            for job_name in job_names:
                start = content.index(f"  {job_name}:")
                next_job = re.search(
                    r"(?m)^  [A-Za-z0-9_-]+:\s*$", content[start + 3 :]
                )
                end = start + 3 + next_job.start() if next_job else len(content)
                job = content[start:end]
                gate = job.index("check-codex-pr-safety.py")
                token = job.index("actions/create-github-app-token@")
                self.assertLess(gate, token, f"late credential gate in {job_name}")

    def test_verifiers_gate_applied_patch_with_trusted_checker(self):
        for name in ("codex-jira-verify.sh", "codex-pr-review-verify.sh"):
            content = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            apply_patch = content.index('apply --index --binary "${patch_path}"')
            safety_gate = content.index('run_sanitized python3 -I "${safety_gate_path}"')
            self.assertLess(apply_patch, safety_gate)

    def test_jira_no_change_result_has_terminal_callback(self):
        content = IMPLEMENT_WORKFLOW.read_text(encoding="utf-8")
        start = content.index("  codex-no-changes:")
        end = content.index("\n  verify-codex-output:", start)
        job = content[start:end]
        self.assertIn("always()", job)
        self.assertIn("has_changes == 'false'", job)
        self.assertIn("--status no-changes", job)

    def test_verification_failures_have_structure_only_draft_recovery(self):
        content = IMPLEMENT_WORKFLOW.read_text(encoding="utf-8")
        start = content.index("  prepare-draft-publication:")
        end = content.index("\n  publish-draft-pr:", start)
        job = content[start:end]
        self.assertIn("always()", job)
        self.assertIn('SKIP_LOCAL_PIPELINE: "true"', job)
        self.assertIn("permissions: {}", job)
        self.assertIn("available=false", job)
        self.assertIn("codex-jira-terminal-draft", job)
        self.assertIn("  codex-prepublication-terminal-failed:", content)

    def test_review_setup_failure_returns_existing_pr_to_draft(self):
        content = REVIEW_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "passed: ${{ steps.verify.outputs.passed || 'false' }}", content
        )
        start = content.index("  codex-review-prepublication-verification-failed:")
        job = content[start:]
        self.assertIn("always()", job)
        self.assertIn("codex-mark-pr-failed.sh", job)
        self.assertIn('NOTIFY_JIRA: "true"', job)

    def test_partial_jira_publication_recovers_remote_pr_state(self):
        content = IMPLEMENT_WORKFLOW.read_text(encoding="utf-8")
        jobs = {
            "publish-draft-pr": "publish-pr",
            "publish-pr": "verify-published-pr-patch",
            "publish-published-pr-repair-1": "verify-published-pr-1-patch",
        }
        for job_name, next_job_name in jobs.items():
            start = content.index(f"  {job_name}:")
            end = content.index(f"\n  {next_job_name}:", start)
            job = content[start:end]
            self.assertIn(
                "steps.state.outputs.pr_number || steps.publish.outputs.pr_number", job
            )
            self.assertIn("if: always() && steps.publish.outputs.commit_sha != ''", job)
            self.assertIn("--json number,url,headRefOid", job)

    def test_release_updater_validates_before_branch_mutation_and_resets_stale_branch(self):
        content = UPDATER_WORKFLOW.read_text(encoding="utf-8")
        validation = content.index("?ref=${TARGET_BRANCH}")
        branch_create = content.index(
            'gh api --method POST "repos/${TARGET_REPOSITORY}/git/refs"'
        )
        open_pr = content.index('existing="$(gh pr list')
        stale_reset = content.index(
            'gh api --method PATCH "repos/${TARGET_REPOSITORY}/git/refs/heads/${branch}"'
        )
        self.assertLess(validation, open_pr)
        self.assertLess(open_pr, stale_reset)
        self.assertLess(stale_reset, branch_create)
        self.assertIn("-F force=true", content)


if __name__ == "__main__":
    unittest.main()
