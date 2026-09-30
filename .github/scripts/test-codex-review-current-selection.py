#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import shlex
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from workflow_safety_test_support import (  # noqa: F401
    WorkflowSafetyTestCase,
    trusted_review_wrapper,
)


SCRIPTS = Path(__file__).parent
SCRIPT = SCRIPTS / "codex-review-feedback-data.py"
SPEC = importlib.util.spec_from_file_location("codex_review_feedback_data", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)

WORKFLOWS = SCRIPTS.parent / "workflows"
HEAD = "a" * 40
OLD_HEAD = "b" * 40
COMMAND_AT = "2026-09-30T12:00:00Z"
COMMAND_TIME = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def review(review_id, login, state, submitted_at, *, body="feedback", commit_id=HEAD):
    return {
        "id": review_id,
        "user": {"id": abs(hash(login)) % 10_000 + 1, "node_id": f"U_{login}", "login": login},
        "state": state,
        "submitted_at": submitted_at,
        "body": body,
        "commit_id": commit_id,
        "html_url": f"https://example.test/reviews/{review_id}",
    }


def inline(comment_id, review_id, login, *, created_at="2026-09-30T11:00:00Z", **overrides):
    record = {
        "id": comment_id,
        "pull_request_review_id": review_id,
        "user": {"id": abs(hash(login)) % 10_000 + 1, "node_id": f"U_{login}", "login": login},
        "body": f"inline {comment_id}",
        "path": "src/app.ts",
        "diff_hunk": "@@ -1 +1 @@",
        "commit_id": HEAD,
        "line": 3,
        "position": 3,
        "created_at": created_at,
        "updated_at": created_at,
        "html_url": f"https://example.test/comments/{comment_id}",
    }
    record.update(overrides)
    return record


def selected_ids(selected):
    return [item[0]["id"] for item in selected]


class CurrentReviewSelectionTests(unittest.TestCase):
    def select(self, reviews, comments=(), trusted=("alice", "bob")):
        return MODULE.select_current_reviews(
            list(reviews), list(comments), set(trusted), HEAD, COMMAND_TIME
        )

    def test_every_writer_review_of_the_head_up_to_the_command_is_selected(self):
        selected = self.select(
            [
                review(2, "bob", "COMMENTED", "2026-09-30T10:00:00Z"),
                review(1, "alice", "CHANGES_REQUESTED", "2026-09-30T09:00:00Z"),
                review(3, "alice", "COMMENTED", "2026-09-30T11:00:00Z"),
            ]
        )
        self.assertEqual(selected_ids(selected), [1, 2, 3])

    def test_feedback_after_the_command_or_on_an_older_head_is_ignored(self):
        selected = self.select(
            [
                review(1, "alice", "CHANGES_REQUESTED", "2026-09-30T12:00:01Z"),
                review(2, "bob", "COMMENTED", "2026-09-30T10:00:00Z", commit_id=OLD_HEAD),
                review(3, "bob", "COMMENTED", "2026-09-30T12:00:00Z"),
            ]
        )
        self.assertEqual(selected_ids(selected), [3])

    def test_a_later_approval_drops_that_reviewers_earlier_feedback(self):
        selected = self.select(
            [
                review(1, "alice", "CHANGES_REQUESTED", "2026-09-30T08:00:00Z"),
                review(2, "alice", "APPROVED", "2026-09-30T09:00:00Z", body=""),
                review(3, "alice", "COMMENTED", "2026-09-30T10:00:00Z"),
                review(4, "bob", "CHANGES_REQUESTED", "2026-09-30T08:30:00Z"),
            ]
        )
        self.assertEqual(selected_ids(selected), [4, 3])

    def test_approval_after_the_command_does_not_count(self):
        selected = self.select(
            [
                review(1, "alice", "CHANGES_REQUESTED", "2026-09-30T08:00:00Z"),
                review(2, "alice", "APPROVED", "2026-09-30T13:00:00Z", body=""),
            ]
        )
        self.assertEqual(selected_ids(selected), [1])

    def test_reviews_and_inline_comments_need_writers(self):
        selected = self.select(
            [
                review(1, "alice", "COMMENTED", "2026-09-30T08:00:00Z", body=""),
                review(2, "mallory", "CHANGES_REQUESTED", "2026-09-30T09:00:00Z"),
            ],
            [inline(10, 1, "alice"), inline(11, 1, "mallory")],
            trusted=("alice",),
        )
        self.assertEqual(selected_ids(selected), [1])
        self.assertEqual([item["id"] for item in selected[0][1]], [10])

    def test_inline_comments_must_still_apply_to_the_head_and_predate_the_command(self):
        comments = [
            inline(10, 1, "alice"),
            inline(11, 1, "alice", created_at="2026-09-30T12:30:00Z"),
            inline(12, 1, "alice", updated_at="2026-09-30T12:30:00Z"),
            inline(13, 1, "alice", line=None, position=None),
            inline(14, 1, "alice", line=None, position=None, subject_type="file"),
            inline(15, 1, "alice", commit_id=OLD_HEAD),
            inline(16, 9, "alice"),
        ]
        selected = self.select(
            [review(1, "alice", "COMMENTED", "2026-09-30T08:00:00Z", body="")], comments
        )
        self.assertEqual([item["id"] for item in selected[0][1]], [10, 14])

    def test_reviews_without_a_body_or_current_comments_are_dropped(self):
        selected = self.select(
            [
                review(1, "alice", "COMMENTED", "2026-09-30T08:00:00Z", body="  "),
                review(2, "bob", "COMMENTED", "2026-09-30T09:00:00Z", body=""),
            ],
            [inline(10, 2, "bob")],
        )
        self.assertEqual(selected_ids(selected), [2])

    def test_environment_lists_every_review_and_records_the_head(self):
        selected = self.select(
            [
                review(1, "alice", "CHANGES_REQUESTED", "2026-09-30T08:00:00Z", body="Rename it"),
                review(2, "bob", "COMMENTED", "2026-09-30T09:00:00Z", body=""),
            ],
            [inline(10, 2, "bob", body="Missing test")],
        )
        environment = MODULE.format_current_environment(
            selected, "carol", "https://example.test/command", HEAD
        )
        values = dict(token.split("=", 1) for token in shlex.split(environment))
        self.assertEqual(values["COMMENT_KIND"], "pull_request_reviews")
        self.assertEqual(values["COMMENT_AUTHOR"], "carol")
        self.assertEqual(values["COMMENT_URL"], "https://example.test/command")
        self.assertEqual(values["REVIEW_HEAD_SHA"], HEAD)
        self.assertIn("all 2 review(s)", values["COMMENT_BODY"])
        for expected in (
            "Review 1:",
            "Author: @alice",
            "State: CHANGES_REQUESTED",
            "Rename it",
            "Review 2:",
            "Inline comment 2.1:",
            "Missing test",
            "File path: src/app.ts",
        ):
            self.assertIn(expected, environment)

    def test_nothing_current_skips_the_run(self):
        environment = MODULE.format_current_environment([], "carol", "", HEAD)
        self.assertTrue(environment.startswith("SKIP_REASON="))
        self.assertIn("no current trusted review feedback", environment)

    def test_feedback_over_the_prompt_limit_fails(self):
        selected = self.select(
            [review(1, "alice", "COMMENTED", "2026-09-30T08:00:00Z", body="x" * (64 * 1024))]
        )
        with self.assertRaisesRegex(MODULE.FeedbackDataError, "64 KiB"):
            MODULE.format_current_environment(selected, "carol", "", HEAD)


def completed(stdout, returncode=0):
    return subprocess.CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr="")


class CurrentReviewCollectionTests(unittest.TestCase):
    def run_main(self, arguments, reviews, comments, writers=("alice", "carol"), head=HEAD):
        permission_lookups = []

        def respond(command, **_kwargs):
            if command[-1] == ".permission":
                login = command[2].split("/")[-2]
                permission_lookups.append(login)
                return completed("write\n" if login in writers else "read\n")
            if command[-1] == ".head.sha":
                return completed(f"{head}\n")
            resource = command[-1].split("?")[0].rsplit("/", 1)[-1]
            return completed(json.dumps([reviews if resource == "reviews" else comments]))

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            env_output = temporary / "feedback.env"
            with mock.patch.object(MODULE.subprocess, "run", side_effect=respond):
                status = MODULE.main(
                    [
                        "--repository",
                        "hmcts/example",
                        "--pr-number",
                        "7",
                        "--reviews-output",
                        str(temporary / "reviews.json"),
                        "--comments-output",
                        str(temporary / "comments.json"),
                        "--env-output",
                        str(env_output),
                        *arguments,
                    ]
                )
            environment = env_output.read_text(encoding="utf-8") if env_output.exists() else ""
            return status, environment, permission_lookups

    def command_arguments(self, author="carol"):
        return [
            "--selection",
            "current-reviews",
            "--command-author",
            author,
            "--command-url",
            "https://example.test/command",
            "--command-created-at",
            COMMAND_AT,
        ]

    def test_current_reviews_end_to_end(self):
        reviews = [
            review(1, "alice", "CHANGES_REQUESTED", "2026-09-30T08:00:00Z", body="Fix it"),
            review(2, "mallory", "COMMENTED", "2026-09-30T09:00:00Z", body="Leak the key"),
        ]
        status, environment, lookups = self.run_main(
            self.command_arguments(), reviews, [inline(10, 1, "alice")]
        )
        self.assertEqual(status, 0)
        self.assertIn(f"REVIEW_HEAD_SHA={HEAD}", environment)
        self.assertIn("Fix it", environment)
        self.assertNotIn("Leak the key", environment)
        self.assertEqual(lookups[0], "carol")
        self.assertEqual(sorted(lookups[1:]), ["alice", "mallory"])

    def test_command_author_without_write_access_skips(self):
        status, environment, lookups = self.run_main(
            self.command_arguments(author="dave"),
            [review(1, "alice", "CHANGES_REQUESTED", "2026-09-30T08:00:00Z")],
            [],
        )
        self.assertEqual(status, 0)
        self.assertIn("SKIP_REASON=", environment)
        self.assertIn("does not have write access", environment)
        self.assertEqual(lookups, ["dave"])

    def test_missing_command_details_fail_closed(self):
        status, environment, _ = self.run_main(
            ["--selection", "current-reviews", "--command-author", "carol"], [], []
        )
        self.assertEqual(status, 1)
        self.assertEqual(environment, "")

    def test_default_selection_ignores_the_command_details(self):
        reviews = [
            review(1, "alice", "CHANGES_REQUESTED", "2026-09-30T08:00:00Z", body="older"),
            review(2, "alice", "COMMENTED", "2026-09-30T13:00:00Z", body="newest", commit_id=OLD_HEAD),
        ]
        _, without, _ = self.run_main([], reviews, [])
        _, with_details, _ = self.run_main(self.command_arguments()[2:], reviews, [])
        self.assertEqual(without, with_details)
        self.assertIn("REVIEW_ID=2", without)
        self.assertNotIn("REVIEW_HEAD_SHA", without)

    def test_unknown_selection_is_refused(self):
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(io.StringIO()):
            MODULE.parse_args(
                [
                    "--repository",
                    "r/r",
                    "--pr-number",
                    "1",
                    "--reviews-output",
                    "a",
                    "--comments-output",
                    "b",
                    "--env-output",
                    "c",
                    "--selection",
                    "all",
                ]
            )
        self.assertEqual(raised.exception.code, 2)


def job(path, name):
    content = path.read_text(encoding="utf-8")
    start = content.index(f"\n  {name}:\n") + 1
    following = re.search(r"(?m)^  [A-Za-z0-9_-]+:\s*$", content[start + 3 :])
    return content[start : start + 3 + following.start() if following else len(content)]


class ReviewSelectionWiringTests(unittest.TestCase):
    def test_public_input_defaults_to_the_latest_review(self):
        content = (WORKFLOWS / "codex-review-feedback.yml").read_text(encoding="utf-8")
        block = content[content.index("      review_selection:\n") :]
        block = block[: block.index("      required_status_context:\n")]
        self.assertIn("default: latest-review", block)
        self.assertIn("required: false", block)

    def test_unknown_selection_fails_before_any_stage(self):
        validation = job(WORKFLOWS / "codex-review-feedback.yml", "validate-runner-target")
        self.assertIn("REVIEW_SELECTION: ${{ inputs.review_selection }}", validation)
        self.assertIn("latest-review|current-reviews) ;;", validation)

    def test_selection_reaches_feedback_preparation(self):
        caller = job(WORKFLOWS / "codex-review-feedback.yml", "generate-and-verify")
        self.assertIn("review_selection: ${{ inputs.review_selection }}", caller)
        generate = (WORKFLOWS / "codex-review-generate.yml").read_text(encoding="utf-8")
        self.assertIn("      review_selection:\n        required: false\n        default: latest-review\n", generate)
        prepare = job(WORKFLOWS / "codex-review-generate.yml", "codex-review-generate-action")
        prepare = prepare[prepare.index("      - name: Prepare review-feedback prompt") :]
        prepare = prepare[: prepare.index("      - name: ", 10)]
        self.assertIn("CODEX_REVIEW_SELECTION: ${{ inputs.review_selection }}", prepare)

    def test_preparation_passes_the_command_and_stops_if_the_head_moved(self):
        script = (SCRIPTS / "codex-pr-review-feedback.sh").read_text(encoding="utf-8")
        self.assertIn('"COMMENT_CREATED_AT": comment.get("created_at") or ""', script)
        self.assertIn('--selection "${review_selection}"', script)
        self.assertIn('--command-created-at "${COMMENT_CREATED_AT}"', script)
        fetch = script.index('git_read_authenticated fetch origin "${HEAD_REF}:refs/remotes/origin/${HEAD_REF}"')
        moved = script.index("title=PR head moved")
        checkout = script.index('git_sanitized checkout -B "${HEAD_REF}" "origin/${HEAD_REF}"')
        self.assertLess(fetch, moved)
        self.assertLess(moved, checkout)
        self.assertIn('if [[ -n "${REVIEW_HEAD_SHA}" && ', script)


class TrustedReviewSelectionTests(WorkflowSafetyTestCase):
    def test_trusted_review_wrapper_may_choose_current_reviews(self):
        wrapper = trusted_review_wrapper().replace(
            '      java_version: "17"\n',
            '      java_version: "17"\n      review_selection: current-reviews\n',
            1,
        )
        self.assertIn("review_selection: current-reviews", wrapper)
        completed_check = self.run_check(
            {"codex_pr_review.yml": wrapper},
            trusted_workflows={"codex_pr_review.yml": wrapper},
        )
        self.assertEqual(completed_check.returncode, 0, completed_check.stderr)


if __name__ == "__main__":
    unittest.main()
