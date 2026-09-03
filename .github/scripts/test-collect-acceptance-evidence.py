#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("collect-acceptance-evidence.py")
SPEC = importlib.util.spec_from_file_location("collect_acceptance_evidence", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AcceptanceEvidenceTests(unittest.TestCase):
    def test_report_records_acceptance_assertions(self) -> None:
        report = MODULE.build_report(
            "hmcts/example",
            "JS-123",
            {"id": 10, "status": "completed", "conclusion": "success"},
            [{
                "name": "implement / plan / codex-plan-action",
                "status": "completed",
                "conclusion": "success",
                "runner_name": "codex-example-123",
                "labels": ["self-hosted", "codex-example-aks"],
            }],
            [{"name": "codex-validated-plan"}],
            {
                "number": 20,
                "user": {"login": "hmcts-pr-publisher[bot]"},
                "draft": False,
                "body": "### Automation request\n\nInitiated in Jira by: Zac Healy\n",
            },
            [{"name": "build", "status": "completed", "conclusion": "success"}],
        )

        self.assertTrue(report["assertions"]["bot_authored"])
        self.assertTrue(report["assertions"]["initiator_recorded"])
        self.assertTrue(report["assertions"]["validated_plan_retained"])
        self.assertTrue(report["assertions"]["repository_checks_started"])
        self.assertTrue(report["assertions"]["model_jobs_used_self_hosted_runner"])
        self.assertEqual(report["pull_request"]["initiator"], "Zac Healy")
        self.assertEqual(report["run_id"], 10)
        self.assertEqual(report["pr_number"], 20)

    def test_report_does_not_infer_missing_evidence(self) -> None:
        report = MODULE.build_report(
            "hmcts/example",
            "JS-124",
            {},
            [],
            [],
            {"user": {"login": "human-user"}, "body": ""},
            [],
        )
        self.assertFalse(report["assertions"]["bot_authored"])
        self.assertFalse(report["assertions"]["initiator_recorded"])
        self.assertFalse(report["assertions"]["validated_plan_retained"])
        self.assertFalse(report["assertions"]["repository_checks_started"])

    def test_trusted_jobs_are_not_mistaken_for_model_jobs(self) -> None:
        report = MODULE.build_report(
            "hmcts/example",
            "JS-125",
            {"id": 11},
            [{
                "name": "implement / plan / validate-codex-plan",
                "runner_name": "GitHub Actions 123",
                "labels": ["ubuntu-latest"],
            }],
            [],
            {"number": 21, "user": {"login": "hmcts-pr-publisher[bot]"}},
            [],
        )

        self.assertEqual(report["model_jobs"], [])
        self.assertFalse(report["assertions"]["model_jobs_used_self_hosted_runner"])


if __name__ == "__main__":
    unittest.main()
