#!/usr/bin/env python3

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
IMPLEMENT_WORKFLOW = ROOT / "workflows" / "codex-implement.yml"
REVIEW_WORKFLOW = ROOT / "workflows" / "codex-review-feedback.yml"
UPDATER_WORKFLOW = ROOT / "workflows" / "update-callers.yml"


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

    def test_release_updater_uses_contract_migrator(self):
        content = UPDATER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(".github/scripts/update-caller-workflow.py", content)
        self.assertNotIn("sed -E", content)


if __name__ == "__main__":
    unittest.main()
