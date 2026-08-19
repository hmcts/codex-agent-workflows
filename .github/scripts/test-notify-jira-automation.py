#!/usr/bin/env python3

import argparse
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("notify-jira-automation.py")
SPEC = importlib.util.spec_from_file_location("notify_jira_automation", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class NotifyJiraAutomationTest(unittest.TestCase):
    def environment(self):
        return patch.dict(
            os.environ,
            {
                "ISSUE_KEY": "JS-123",
                "ISSUE_SUMMARY": "Fix the issue",
                "ISSUE_URL": "https://tools.hmcts.net/jira/browse/JS-123",
                "GITHUB_REPOSITORY": "hmcts/juror-api",
                "GITHUB_SERVER_URL": "https://github.com",
                "GITHUB_RUN_ID": "456",
                "GITHUB_ACTOR": "bot-user",
            },
            clear=True,
        )

    def test_builds_pr_payload(self):
        args = argparse.Namespace(
            pr_url="https://github.com/hmcts/juror-api/pull/1",
            status=None,
            branch_name="codex/js-123",
            commit_sha="a" * 40,
            draft=False,
            verification_status="passed",
            message=None,
        )
        with self.environment():
            payload = MODULE.build_payload(args)
        self.assertEqual(payload["issueKey"], "JS-123")
        self.assertEqual(payload["prTitle"], "JS-123: Fix the issue")
        self.assertFalse(payload["isDraft"])
        self.assertNotIn("status", payload)

    def test_builds_blocked_run_payload(self):
        args = argparse.Namespace(
            pr_url=None,
            status="blocked",
            branch_name=None,
            commit_sha=None,
            draft=False,
            verification_status="passed",
            message="A required interface is not documented.",
        )
        with self.environment():
            payload = MODULE.build_payload(args)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["message"], "A required interface is not documented.")
        self.assertEqual(payload["runUrl"], "https://github.com/hmcts/juror-api/actions/runs/456")
        self.assertNotIn("prUrl", payload)

    def test_builds_terminal_no_changes_payload(self):
        args = argparse.Namespace(
            pr_url=None,
            status="no-changes",
            branch_name=None,
            commit_sha=None,
            draft=False,
            verification_status="passed",
            message="No repository changes are required.",
        )
        with self.environment():
            payload = MODULE.build_payload(args)
        self.assertEqual(payload["status"], "no-changes")
        self.assertEqual(payload["message"], "No repository changes are required.")
        self.assertNotIn("prUrl", payload)

    def test_rejects_incomplete_mode_arguments(self):
        args = argparse.Namespace(
            pr_url=None,
            status="blocked",
            branch_name=None,
            commit_sha=None,
            draft=False,
            verification_status="passed",
            message="",
        )
        with self.environment(), self.assertRaisesRegex(RuntimeError, "--message"):
            MODULE.build_payload(args)


if __name__ == "__main__":
    unittest.main()
