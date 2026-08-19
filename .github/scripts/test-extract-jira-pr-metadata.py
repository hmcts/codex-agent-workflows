#!/usr/bin/env python3
"""Tests for generated pull request Jira metadata extraction."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("extract-jira-pr-metadata.py")
SPEC = importlib.util.spec_from_file_location("extract_jira_pr_metadata", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ExtractJiraPrMetadataTest(unittest.TestCase):
    def test_extracts_matching_generated_jira_link(self) -> None:
        issue_key, issue_url = MODULE.extract_metadata(
            {
                "title": "JS-123: Correct juror record",
                "body": "### Jira link\n\nSee [JS-123](https://tools.hmcts.net/jira/browse/JS-123)\n",
            }
        )
        self.assertEqual(issue_key, "JS-123")
        self.assertEqual(issue_url, "https://tools.hmcts.net/jira/browse/JS-123")

    def test_rejects_mismatched_link_label_and_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "label and URL key"):
            MODULE.extract_metadata(
                {
                    "title": "JS-123: Correct juror record",
                    "body": "See [JS-123](https://tools.hmcts.net/jira/browse/JS-456)",
                }
            )

    def test_rejects_title_for_another_issue(self) -> None:
        with self.assertRaisesRegex(ValueError, "title does not match"):
            MODULE.extract_metadata(
                {
                    "title": "JS-456: Different work",
                    "body": "See [JS-123](https://tools.hmcts.net/jira/browse/JS-123)",
                }
            )

    def test_rejects_ambiguous_jira_links(self) -> None:
        link = "See [JS-123](https://tools.hmcts.net/jira/browse/JS-123)"
        with self.assertRaisesRegex(ValueError, "exactly one"):
            MODULE.extract_metadata(
                {
                    "title": "JS-123: Correct juror record",
                    "body": f"{link}\n{link}",
                }
            )


if __name__ == "__main__":
    unittest.main()
