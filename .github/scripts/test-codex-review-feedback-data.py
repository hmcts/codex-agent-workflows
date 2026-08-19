#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("codex-review-feedback-data.py")
SPEC = importlib.util.spec_from_file_location("codex_review_feedback_data", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def review(
    review_id: int,
    user_id: int,
    state: str,
    submitted_at: str,
    *,
    body: str = "feedback",
    login: str | None = None,
    association: str = "MEMBER",
) -> dict[str, object]:
    return {
        "id": review_id,
        "user": {
            "id": user_id,
            "node_id": f"USER_{user_id}",
            "login": login or f"reviewer-{user_id}",
        },
        "state": state,
        "submitted_at": submitted_at,
        "body": body,
        "author_association": association,
        "html_url": f"https://example.test/reviews/{review_id}",
    }


def completed(
    stdout: object, *, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["gh"],
        returncode=returncode,
        stdout=json.dumps(stdout),
        stderr=stderr,
    )


def comment(
    comment_id: int,
    review_id: int,
    user_id: int,
    *,
    body: str,
    login: str | None = None,
    association: str | None = "MEMBER",
) -> dict[str, object]:
    return {
        "id": comment_id,
        "pull_request_review_id": review_id,
        "user": {
            "id": user_id,
            "node_id": f"USER_{user_id}",
            "login": login or f"commenter-{user_id}",
        },
        "author_association": association,
        "body": body,
        "path": "src/example.py",
        "html_url": f"https://example.test/comments/{comment_id}",
    }


class ReviewSelectionTests(unittest.TestCase):
    def test_approval_supersedes_change_request_for_same_stable_reviewer(self):
        reviews = [
            review(10, 7, "CHANGES_REQUESTED", "2026-08-19T10:00:00Z"),
            review(
                11,
                7,
                "APPROVED",
                "2026-08-19T11:00:00Z",
                login="renamed-reviewer",
            ),
        ]

        selected, comments = MODULE.select_actionable_review(reviews, [])

        self.assertIsNone(selected)
        self.assertEqual(comments, [])

    def test_dismissed_review_supersedes_change_request(self):
        reviews = [
            review(20, 8, "CHANGES_REQUESTED", "2026-08-19T10:00:00Z"),
            review(21, 8, "DISMISSED", "2026-08-19T11:00:00Z"),
        ]

        selected, _ = MODULE.select_actionable_review(reviews, [])

        self.assertIsNone(selected)

    def test_latest_review_is_selected_per_reviewer_before_global_selection(self):
        reviews = [
            review(30, 1, "CHANGES_REQUESTED", "2026-08-19T08:00:00Z"),
            review(31, 2, "COMMENTED", "2026-08-19T09:00:00Z"),
            review(32, 1, "APPROVED", "2026-08-19T12:00:00Z"),
            review(33, 3, "CHANGES_REQUESTED", "2026-08-19T11:00:00Z"),
        ]

        selected, _ = MODULE.select_actionable_review(reviews, [])

        self.assertEqual(selected["id"], 33)

    def test_equal_timestamps_use_numeric_review_id_not_input_order(self):
        lower_change = review(
            40, 4, "CHANGES_REQUESTED", "2026-08-19T10:00:00Z"
        )
        higher_approval = review(41, 4, "APPROVED", "2026-08-19T10:00:00Z")

        for reviews in (
            [lower_change, higher_approval],
            [higher_approval, lower_change],
        ):
            with self.subTest(order=[item["id"] for item in reviews]):
                selected, _ = MODULE.select_actionable_review(reviews, [])
                self.assertIsNone(selected)

    def test_conflicting_duplicate_rank_and_unorderable_state_suppress_reviewer(self):
        conflicting = [
            review(50, 5, "CHANGES_REQUESTED", "2026-08-19T10:00:00Z"),
            review(50, 5, "APPROVED", "2026-08-19T10:00:00Z"),
        ]
        malformed_later_state = [
            review(60, 6, "CHANGES_REQUESTED", "2026-08-19T10:00:00Z"),
            review(61, 6, "DISMISSED", "not-a-timestamp"),
        ]

        for reviews in (conflicting, malformed_later_state):
            with self.subTest(reviews=reviews):
                selected, _ = MODULE.select_actionable_review(reviews, [])
                self.assertIsNone(selected)

    def test_pending_unsubmitted_review_does_not_supersede_submitted_feedback(self):
        reviews = [
            review(70, 7, "CHANGES_REQUESTED", "2026-08-19T10:00:00Z"),
            review(71, 7, "PENDING", ""),
        ]

        selected, _ = MODULE.select_actionable_review(reviews, [])

        self.assertEqual(selected["id"], 70)

    def test_mixed_review_thread_excludes_untrusted_and_malformed_replies(self):
        selected_review = review(
            80,
            8,
            "COMMENTED",
            "2026-08-19T10:00:00Z",
            body="",
        )
        trusted = comment(801, 80, 8, body="trusted feedback", login="reviewer")
        untrusted = comment(
            802,
            80,
            9,
            body="untrusted reply",
            login="outsider",
            association="CONTRIBUTOR",
        )
        missing_association = comment(
            803,
            80,
            10,
            body="missing association",
            association=None,
        )
        malformed = comment(804, 80, 11, body="malformed identity")
        malformed["user"] = {"login": "no-stable-id"}

        selected, comments = MODULE.select_actionable_review(
            [selected_review],
            [untrusted, missing_association, malformed, trusted],
        )

        self.assertEqual(selected["id"], 80)
        self.assertEqual([item["id"] for item in comments], [801])
        environment = MODULE.format_review_environment(selected, comments)
        self.assertIn("Author: @reviewer (MEMBER)", environment)
        self.assertIn("trusted feedback", environment)
        self.assertNotIn("untrusted reply", environment)
        self.assertNotIn("missing association", environment)
        self.assertNotIn("malformed identity", environment)

    def test_multiple_trusted_commenters_preserve_each_attribution(self):
        selected_review = review(
            90,
            9,
            "CHANGES_REQUESTED",
            "2026-08-19T10:00:00Z",
            body="",
        )
        comments = [
            comment(901, 90, 9, body="reviewer feedback", login="reviewer"),
            comment(
                902,
                90,
                10,
                body="maintainer follow-up",
                login="maintainer",
                association="OWNER",
            ),
        ]

        selected, selected_comments = MODULE.select_actionable_review(
            [selected_review], comments
        )

        self.assertEqual(selected["id"], 90)
        self.assertEqual(len(selected_comments), 2)
        environment = MODULE.format_review_environment(selected, selected_comments)
        self.assertIn("Author: @reviewer (MEMBER)", environment)
        self.assertIn("Author: @maintainer (OWNER)", environment)


class PaginatedCollectionTests(unittest.TestCase):
    def test_page_boundaries_flatten_all_records_and_select_later_page_feedback(self):
        first_review_page = [
            review(
                review_id,
                review_id,
                "COMMENTED",
                f"2026-08-18T{review_id % 24:02d}:00:00Z",
            )
            for review_id in range(1, 101)
        ]
        latest_review = review(
            1001,
            1001,
            "CHANGES_REQUESTED",
            "2026-08-19T12:00:00Z",
            body="later page feedback",
        )
        first_comment_page = [
            comment(
                comment_id,
                comment_id,
                comment_id,
                body=f"comment {comment_id}",
            )
            for comment_id in range(1, 101)
        ]
        latest_comment = comment(
            2001,
            1001,
            1001,
            body="newest inline feedback",
            login="later-page-reviewer",
        )
        latest_comment["path"] = "src/latest.py"

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            reviews_output = temporary / "reviews.json"
            comments_output = temporary / "comments.json"
            env_output = temporary / "feedback.env"
            env_output.write_text("SKIP_REASON=''\n", encoding="utf-8")
            responses = [
                completed([first_review_page, [latest_review], []]),
                completed([first_comment_page, [latest_comment], []]),
            ]

            with mock.patch.object(MODULE.subprocess, "run", side_effect=responses) as run:
                status = MODULE.main(
                    [
                        "--repository",
                        "hmcts/example",
                        "--pr-number",
                        "2",
                        "--reviews-output",
                        str(reviews_output),
                        "--comments-output",
                        str(comments_output),
                        "--env-output",
                        str(env_output),
                    ]
                )

            self.assertEqual(status, 0)
            self.assertEqual(len(json.loads(reviews_output.read_text())), 101)
            self.assertEqual(len(json.loads(comments_output.read_text())), 101)
            environment = env_output.read_text(encoding="utf-8")
            self.assertIn("REVIEW_ID=1001", environment)
            self.assertIn("later page feedback", environment)
            self.assertIn("newest inline feedback", environment)
            self.assertIn("src/latest.py", environment)
            self.assertIn("Author: @later-page-reviewer (MEMBER)", environment)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(
                run.call_args_list[0].args[0],
                [
                    "gh",
                    "api",
                    "--paginate",
                    "--slurp",
                    "repos/hmcts/example/pulls/2/reviews?per_page=100",
                ],
            )
            self.assertEqual(
                run.call_args_list[1].args[0],
                [
                    "gh",
                    "api",
                    "--paginate",
                    "--slurp",
                    "repos/hmcts/example/pulls/2/comments?per_page=100",
                ],
            )

    def test_empty_and_final_pages_produce_valid_empty_aggregate(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            outputs = [temporary / name for name in ("reviews.json", "comments.json")]
            env_output = temporary / "feedback.env"

            with mock.patch.object(
                MODULE.subprocess,
                "run",
                side_effect=[completed([[], []]), completed([])],
            ):
                status = MODULE.main(
                    [
                        "--repository",
                        "hmcts/example",
                        "--pr-number",
                        "2",
                        "--reviews-output",
                        str(outputs[0]),
                        "--comments-output",
                        str(outputs[1]),
                        "--env-output",
                        str(env_output),
                    ]
                )

            self.assertEqual(status, 0)
            self.assertEqual(json.loads(outputs[0].read_text()), [])
            self.assertEqual(json.loads(outputs[1].read_text()), [])
            self.assertEqual(
                env_output.read_text(encoding="utf-8"),
                "SKIP_REASON='no actionable trusted review feedback was found'\n",
            )

    def test_api_failure_leaves_existing_environment_and_outputs_untouched(self):
        successful_reviews = completed(
            [[review(1, 1, "COMMENTED", "2026-08-19T10:00:00Z")]]
        )
        for failed_resource, responses in (
            ("reviews", [completed([], returncode=1, stderr="HTTP 502")]),
            (
                "comments",
                [successful_reviews, completed([], returncode=1, stderr="HTTP 502")],
            ),
        ):
            with self.subTest(failed_resource=failed_resource):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    temporary = Path(temporary_directory)
                    reviews_output = temporary / "reviews.json"
                    comments_output = temporary / "comments.json"
                    env_output = temporary / "feedback.env"
                    env_output.write_text("sentinel=true\n", encoding="utf-8")

                    with mock.patch.object(
                        MODULE.subprocess, "run", side_effect=responses
                    ):
                        status = MODULE.main(
                            [
                                "--repository",
                                "hmcts/example",
                                "--pr-number",
                                "2",
                                "--reviews-output",
                                str(reviews_output),
                                "--comments-output",
                                str(comments_output),
                                "--env-output",
                                str(env_output),
                            ]
                        )

                    self.assertEqual(status, 1)
                    self.assertFalse(reviews_output.exists())
                    self.assertFalse(comments_output.exists())
                    self.assertEqual(
                        env_output.read_text(encoding="utf-8"), "sentinel=true\n"
                    )

    def test_malformed_page_fails_closed(self):
        with mock.patch.object(MODULE.subprocess, "run", return_value=completed([{}])):
            with self.assertRaisesRegex(MODULE.FeedbackDataError, "page 1.*not an array"):
                MODULE.fetch_api_collection("hmcts/example", "2", "reviews")


if __name__ == "__main__":
    unittest.main()
