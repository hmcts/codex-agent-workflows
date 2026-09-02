#!/usr/bin/env python3

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
WORKFLOW_ROOT = ROOT / "workflows"
IMPLEMENT_WORKFLOW = WORKFLOW_ROOT / "codex-implement.yml"
PLAN_WORKFLOW = WORKFLOW_ROOT / "codex-plan.yml"
GENERATE_WORKFLOW = WORKFLOW_ROOT / "codex-generate.yml"
VERIFY_INITIAL_WORKFLOW = WORKFLOW_ROOT / "codex-verify-initial.yml"
REPAIR_ROUND_WORKFLOW = WORKFLOW_ROOT / "codex-repair-round.yml"
PUBLISH_WORKFLOW = WORKFLOW_ROOT / "codex-publish.yml"
POST_REPAIR_WORKFLOW = WORKFLOW_ROOT / "codex-post-repair.yml"
REVIEW_WORKFLOW = WORKFLOW_ROOT / "codex-review-feedback.yml"
REVIEW_INTAKE_WORKFLOW = WORKFLOW_ROOT / "codex-review-intake.yml"
REVIEW_GENERATE_WORKFLOW = WORKFLOW_ROOT / "codex-review-generate.yml"
REVIEW_PUBLISH_WORKFLOW = WORKFLOW_ROOT / "codex-review-publish.yml"
REVIEW_REPAIR_WORKFLOW = WORKFLOW_ROOT / "codex-review-repair.yml"
REVIEW_TERMINAL_WORKFLOW = WORKFLOW_ROOT / "codex-review-terminal.yml"
UPDATER_WORKFLOW = ROOT / "workflows" / "update-callers.yml"
PREFLIGHT = ROOT / "scripts" / "codex-runner-preflight.sh"
ROLLOUT = ROOT.parent / "docs" / "juror-rollout.md"
COMPONENT_WORKFLOWS = tuple(WORKFLOW_ROOT.glob("codex-*.yml"))


def workflow_job(workflow: Path, job_name: str) -> str:
    content = workflow.read_text(encoding="utf-8")
    start = content.index(f"  {job_name}:")
    next_job = re.search(r"(?m)^  [A-Za-z0-9_-]+:\s*$", content[start + 3 :])
    end = start + 3 + next_job.start() if next_job else len(content)
    return content[start:end]


class WorkflowContractTests(unittest.TestCase):
    def test_internal_reusable_workflows_use_same_release_components(self):
        pattern = re.compile(r"uses: \./(\.github/workflows/[^\s]+)")
        external_pattern = re.compile(
            r"uses: hmcts/codex-agent-workflows/\.github/workflows/[^@\s]+@"
        )
        references = []
        for workflow in COMPONENT_WORKFLOWS:
            content = workflow.read_text(encoding="utf-8")
            self.assertIsNone(
                external_pattern.search(content),
                f"{workflow.name} must load internal stages from the caller release",
            )
            references.extend(pattern.findall(content))
        self.assertTrue(references)
        for path in references:
            self.assertTrue(
                (ROOT.parent / path).is_file(),
                f"same-release workflow component is missing: {path}",
            )

    def test_immutable_runtime_pin_is_released_and_packages_policy_preparer(self):
        pins = set()
        pattern = re.compile(
            r"hmcts/codex-agent-workflows/\.github/actions/runtime@([0-9a-f]{40})"
        )
        for workflow in COMPONENT_WORKFLOWS:
            pins.update(pattern.findall(workflow.read_text(encoding="utf-8")))

        self.assertEqual(len(pins), 1)
        pin = pins.pop()
        released = subprocess.run(
            ["git", "merge-base", "--is-ancestor", pin, "origin/main"],
            cwd=ROOT.parent,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            released.returncode,
            0,
            f"immutable runtime {pin} is not part of origin/main",
        )
        for path in (
            ".github/scripts/check-codex-pr-safety.rb",
            ".github/scripts/codex-prepare-policy-candidate.sh",
            ".github/scripts/codex-recover-pr-state.py",
            ".github/scripts/codex-review-feedback-data.py",
        ):
            completed = subprocess.run(
                ["git", "cat-file", "-e", f"{pin}:{path}"],
                cwd=ROOT.parent,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                f"immutable runtime {pin} does not package {path}: {completed.stderr}",
            )

        recovery_helper = subprocess.run(
            ["git", "show", f"{pin}:.github/scripts/codex-recover-pr-state.py"],
            cwd=ROOT.parent,
            capture_output=True,
            text=True,
        )
        self.assertEqual(recovery_helper.returncode, 0, recovery_helper.stderr)
        self.assertIn(
            'parser.add_argument("--base-sha", required=True)',
            recovery_helper.stdout,
        )
        self.assertIn("recover_fresh_pull_request", recovery_helper.stdout)
        self.assertIn("fetch_pull_request_by_number", recovery_helper.stdout)

    def test_both_reusable_workflows_require_jira_callback_secret(self):
        for workflow in (IMPLEMENT_WORKFLOW, REVIEW_WORKFLOW):
            content = workflow.read_text(encoding="utf-8")
            self.assertIn("CODEX_JIRA_PR_NOTIFY_URL:", content)
            self.assertIn("required: true", content)

    def test_validated_plan_bundle_is_retained(self):
        content = PLAN_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("name: codex-validated-plan", content)
        self.assertIn("path: ${{ runner.temp }}/codex-plan", content)
        self.assertIn("retention-days: 30", content)

    def test_planning_failure_always_attempts_jira_callback(self):
        job = workflow_job(PLAN_WORKFLOW, "codex-plan-failed")
        self.assertIn("always()", job)
        self.assertIn("needs.codex-plan-action.result != 'success'", job)
        self.assertIn("needs.validate-codex-plan.result != 'success'", job)
        self.assertIn("notify-jira-automation.py", job)
        self.assertIn("--status failed", job)

    def test_implementation_generation_failure_always_attempts_jira_callback(self):
        job = workflow_job(GENERATE_WORKFLOW, "codex-generation-terminal-failed")
        self.assertIn("always()", job)
        self.assertIn(
            "needs: [codex-generate-action, codex-generate]", job
        )
        self.assertIn("needs.codex-generate.outputs.has_changes != 'true'", job)
        self.assertIn("needs.codex-generate.outputs.has_changes != 'false'", job)
        self.assertIn("permissions: {}", job)
        self.assertIn("--status failed", job)

    def test_release_updater_uses_contract_migrator(self):
        content = UPDATER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("- '.github/workflows/codex-*.yml'", content)
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

    def test_rollout_blocks_all_callers_until_live_master_is_credential_free(self):
        content = ROLLOUT.read_text(encoding="utf-8")
        prerequisite = content.index("Make PR verification credential-free in every Juror caller")
        live_gate = content.index("live `master` checkout of all seven callers")
        activation = content.index("Enable the Jira dispatch rule")
        self.assertLess(prerequisite, activation)
        self.assertLess(live_gate, activation)
        self.assertIn("explicit least-privilege read-only `permissions`", content)
        self.assertIn("Azure login and ACR authentication push-only", content)
        self.assertIn("cannot match generated `codex/**` branches", content)
        self.assertIn("normal PR check and the exact shared Codex credential safety gate", content)
        self.assertIn("intentionally fails closed for every caller", content)
        for repository in (
            "juror-er-portal",
            "juror-public",
            "juror-bureau",
            "juror-api",
            "juror-scheduler-execution",
            "juror-pnc",
            "juror-scheduler-api",
        ):
            self.assertIn(f"`hmcts/{repository}`", content)
        self.assertIn("repository-default token permissions", content)
        self.assertIn("`security-events: write`", content)
        self.assertIn("`id-token: write`", content)

    def test_rollout_documents_dual_tree_event_root_policy(self):
        content = ROLLOUT.read_text(encoding="utf-8")
        self.assertIn("same two-snapshot policy decision", content)
        self.assertIn("candidate event roots and upstream names", content)
        self.assertIn("unchanged trusted default-branch listeners", content)
        self.assertIn("cannot be materialized and parsed", content)
        self.assertIn("complete listener graph", content)
        self.assertIn("listener cycles fail closed", content)
        self.assertIn("only credential-bearing automatic-event exception", content)
        self.assertIn("only `issue_comment` with exactly `types: [created]`", content)
        self.assertIn("`pull_request_review` and `pull_request_review_comment` are not accepted", content)
        self.assertIn("whose `author_association` is exactly one of", content)
        self.assertIn("same reviewed 40-character SHA", content)
        self.assertIn("exactly `https://sonarcloud.io`", content)
        self.assertIn("cannot contain executable steps", content)
        self.assertIn("`workflow_dispatch` requires a trusted operator", content)
        self.assertIn("`repository_dispatch` requires an authenticated trusted service", content)

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
        for workflow in COMPONENT_WORKFLOWS:
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
            PUBLISH_WORKFLOW: {
                "publish-draft-pr": "${{ inputs.source_sha }}",
                "publish-pr": "${{ inputs.source_sha }}",
            },
            POST_REPAIR_WORKFLOW: {
                "publish-published-pr-repair-1": (
                    "${{ inputs.commit_sha }}"
                ),
            },
            REVIEW_PUBLISH_WORKFLOW: {
                "codex-review-publish": "${{ inputs.head_sha }}",
            },
            REVIEW_REPAIR_WORKFLOW: {
                "codex-review-external-republish": (
                    "${{ needs.prepare-published-review-repair-source.outputs.head_sha }}"
                ),
            },
        }
        for workflow, jobs in expected_jobs.items():
            for job_name, candidate_base in jobs.items():
                job = workflow_job(workflow, job_name)
                checkout = job.index("Checkout candidate policy base")
                download = job.index("path: ${{ runner.temp }}/codex-output")
                materialize = job.index("codex-prepare-policy-candidate.sh")
                gate = job.index("check-codex-pr-safety.rb")
                token = job.index("actions/create-github-app-token@")
                self.assertLess(
                    checkout, download, f"candidate output precedes checkout in {job_name}"
                )
                self.assertLess(
                    download, materialize, f"candidate output is not materialized in {job_name}"
                )
                self.assertLess(
                    materialize, gate, f"candidate tree is not gated in {job_name}"
                )
                self.assertLess(gate, token, f"late credential gate in {job_name}")
                self.assertIn(
                    f"POLICY_CANDIDATE_BASE_SHA: {candidate_base}", job
                )
                self.assertIn("ref: ${{ env.POLICY_CANDIDATE_BASE_SHA }}", job)
                self.assertIn(
                    '--repository-root "$GITHUB_WORKSPACE/codex-policy-candidate"',
                    job,
                )
                self.assertIn(
                    '--trusted-repository-root "$GITHUB_WORKSPACE"', job
                )

    def test_review_publication_revalidates_fresh_default_before_push(self):
        for workflow, job_name in (
            (REVIEW_PUBLISH_WORKFLOW, "codex-review-publish"),
            (REVIEW_REPAIR_WORKFLOW, "codex-review-external-republish"),
        ):
            job = workflow_job(workflow, job_name)
            fresh_checkout = job.index("Checkout current default branch")
            current_revision = job.index("Resolve current default revision")
            policy_gate = job.index("check-codex-pr-safety.rb")
            token = job.index("actions/create-github-app-token@")
            publish = job.index("codex-pr-review-publish.sh")
            self.assertLess(fresh_checkout, current_revision)
            self.assertLess(current_revision, policy_gate)
            self.assertLess(policy_gate, token)
            self.assertLess(token, publish)
            self.assertIn("ref: ${{ env.DEFAULT_BRANCH }}", job)
            self.assertIn(
                "EXPECTED_DEFAULT_SHA: ${{ steps.current-default.outputs.sha }}",
                job,
            )

        publisher = (ROOT / "scripts" / "codex-pr-review-publish.sh").read_text(
            encoding="utf-8"
        )
        prepared_commit = publisher.index('commit_sha="$(git_local rev-parse HEAD)"')
        final_default_check = publisher.index(
            "\nverify_default_unchanged\n", prepared_commit
        )
        push = publisher.index("git_authenticated push", prepared_commit)
        self.assertLess(prepared_commit, final_default_check)
        self.assertLess(final_default_check, push)
        self.assertIn("Default branch unavailable", publisher)
        self.assertIn("Default branch moved", publisher)

    def test_jira_publication_revalidates_default_at_push_and_recovery(self):
        publisher = (ROOT / "scripts" / "codex-jira-publish.sh").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            publisher.count("verify_default_unchanged\n    git_authenticated push"),
            2,
        )
        self.assertEqual(
            publisher.count(
                'verify_default_unchanged\n    commit_sha="${remote_branch_sha}"'
            ),
            2,
        )
        self.assertEqual(
            publisher.count(
                "verify_default_unchanged\n"
                "  verify_generated_branch_unchanged\n"
                '  created_pr_url="$(gh_authenticated "${create_args[@]}")"'
            ),
            1,
        )
        self.assertIn("Generated branch unavailable", publisher)
        self.assertIn("before pull request creation", publisher)
        self.assertIn('--base-sha "${verified_base_sha}"', publisher)
        self.assertIn("Default branch unavailable", publisher)
        self.assertIn("Default branch moved", publisher)

    def test_review_reusable_workflow_accepts_only_safe_issue_comments(self):
        job = workflow_job(REVIEW_INTAKE_WORKFLOW, "detect-codex-pr")
        self.assertIn('case "$GITHUB_EVENT_NAME" in', job)
        self.assertIn("            issue_comment)", job)
        self.assertNotIn("pull_request_review", job)
        self.assertIn('if [[ "$body" != "/codex-review" ]]', job)
        self.assertIn("COLLABORATOR|MEMBER|OWNER", job)
        self.assertIn("actor_permission", job)
        preparation = (ROOT / "scripts" / "codex-pr-review-feedback.sh").read_text(
            encoding="utf-8"
        )
        collector = (ROOT / "scripts" / "codex-review-feedback-data.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('event_name == "pull_request_review"', preparation)
        self.assertNotIn('event_name == "pull_request_review_comment"', preparation)
        self.assertIn(
            'python3 -I "${script_dir}/codex-review-feedback-data.py"', preparation
        )
        self.assertIn("no actionable trusted review feedback was found", collector)
        self.assertIn("latest_submitted_reviews", collector)
        self.assertIn('["gh", "api", "--paginate", "--slurp", endpoint]', collector)

    def test_verifiers_gate_applied_patch_with_trusted_checker(self):
        for name in ("codex-jira-verify.sh", "codex-pr-review-verify.sh"):
            content = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            prepare = content.index('"${policy_preparer_path}"')
            safety_gate = content.index(
                'run_sanitized ruby --disable-gems "${safety_gate_path}"'
            )
            self.assertLess(prepare, safety_gate)
            self.assertIn('PATCH_PATH="${patch_path}"', content)
            self.assertIn(
                'TRUSTED_REPOSITORY_ROOT="${trusted_repository_root}"', content
            )
            self.assertIn("EXPECTED_TRUSTED_SHA=", content)
            self.assertIn('--repository-root . \\', content)
            self.assertIn(
                '--trusted-repository-root "${trusted_repository_root}"', content
            )
            self.assertIn("Missing trusted policy candidate preparer", content)

    def test_review_verification_bundle_includes_structural_safety_checker(self):
        content = "\n".join(
            workflow.read_text(encoding="utf-8")
            for workflow in (REVIEW_GENERATE_WORKFLOW, REVIEW_REPAIR_WORKFLOW)
        )
        self.assertEqual(content.count("trusted-check-codex-pr-safety.rb"), 6)
        self.assertEqual(
            content.count("trusted-codex-prepare-policy-candidate.sh"), 6
        )
        self.assertEqual(content.count("TRUSTED_POLICY_PREPARER_PATH:"), 2)
        self.assertNotIn("trusted-check-codex-pr-safety.py", content)

    def test_jira_no_change_result_has_terminal_callback(self):
        job = workflow_job(GENERATE_WORKFLOW, "codex-no-changes")
        self.assertIn("always()", job)
        self.assertIn("has_changes == 'false'", job)
        self.assertIn("--status no-changes", job)

    def test_verification_failures_have_structure_only_draft_recovery(self):
        content = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
        job = workflow_job(PUBLISH_WORKFLOW, "prepare-draft-publication")
        self.assertIn("always()", job)
        self.assertIn('SKIP_LOCAL_PIPELINE: "true"', job)
        self.assertIn("permissions: {}", job)
        self.assertIn("available=false", job)
        self.assertIn("codex-jira-terminal-draft", job)
        self.assertIn("  codex-prepublication-terminal-failed:", content)

    def test_review_setup_failure_returns_existing_pr_to_draft(self):
        content = REVIEW_GENERATE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "passed: ${{ steps.verify.outputs.passed || 'false' }}", content
        )
        job = workflow_job(
            REVIEW_TERMINAL_WORKFLOW,
            "codex-review-prepublication-verification-failed",
        )
        self.assertIn("always()", job)
        self.assertIn("codex-mark-pr-failed.sh", job)
        self.assertIn('NOTIFY_JIRA: "true"', job)

    def test_partial_jira_publication_recovers_remote_pr_state(self):
        jobs = {
            (PUBLISH_WORKFLOW, "publish-draft-pr"): True,
            (PUBLISH_WORKFLOW, "publish-pr"): False,
            (POST_REPAIR_WORKFLOW, "publish-published-pr-repair-1"): False,
        }
        for (workflow, job_name), draft in jobs.items():
            job = workflow_job(workflow, job_name)
            self.assertIn(
                "steps.state.outputs.pr_number || steps.publish.outputs.pr_number", job
            )
            self.assertIn("if: always() && steps.publish.outputs.commit_sha != ''", job)
            self.assertIn("codex-recover-pr-state.py", job)
            self.assertIn('--repository "$GITHUB_REPOSITORY"', job)
            self.assertIn('--base-ref "$DEFAULT_BRANCH"', job)
            self.assertIn('--base-sha "$EXPECTED_BASE_SHA"', job)
            self.assertIn('--head-ref "$BRANCH_NAME"', job)
            self.assertIn('--head-sha "$COMMIT_SHA"', job)
            self.assertIn("--append-output", job)
            self.assertIn(f"--draft {str(draft).lower()}", job)

    def test_release_updater_validates_before_branch_mutation_and_resets_stale_branch(self):
        content = UPDATER_WORKFLOW.read_text(encoding="utf-8")
        validation = content.index("?ref=${TARGET_BRANCH}")
        contract_validation = content.index("update-caller-workflow.py")
        branch_create = content.index(
            'gh api --method POST "repos/${TARGET_REPOSITORY}/git/refs"'
        )
        open_pr = content.index('existing="$(gh pr list')
        stale_reset = content.index(
            'gh api --method PATCH "repos/${TARGET_REPOSITORY}/git/refs/heads/${branch}"'
        )
        self.assertLess(validation, open_pr)
        self.assertLess(contract_validation, open_pr)
        self.assertLess(contract_validation, stale_reset)
        self.assertLess(contract_validation, branch_create)
        self.assertLess(open_pr, stale_reset)
        self.assertLess(stale_reset, branch_create)
        self.assertIn("-F force=true", content)


if __name__ == "__main__":
    unittest.main()
