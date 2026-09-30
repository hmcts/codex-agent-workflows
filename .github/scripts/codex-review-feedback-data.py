#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TRUSTED_PERMISSIONS = {"write", "maintain", "admin"}
ACTIONABLE_STATES = {"CHANGES_REQUESTED", "COMMENTED"}
SELECTIONS = ("latest-review", "current-reviews")
MAX_CURRENT_FEEDBACK_BYTES = 64 * 1024


class FeedbackDataError(RuntimeError):
    pass


def numeric_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isascii() and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def reviewer_identity(review: dict[str, Any]) -> tuple[str, object] | None:
    user = review.get("user")
    if not isinstance(user, dict):
        return None

    user_id = numeric_id(user.get("id"))
    if user_id is not None:
        return ("id", user_id)

    node_id = user.get("node_id")
    if isinstance(node_id, str) and node_id.strip():
        return ("node_id", node_id.strip())
    return None


def reviewer_login(review: dict[str, Any]) -> str | None:
    if reviewer_identity(review) is None:
        return None
    user = review.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    if not isinstance(login, str) or not login.strip():
        return None
    return login.strip()


def is_trusted_reviewer(
    review: dict[str, Any], trusted_logins: set[str]
) -> bool:
    login = reviewer_login(review)
    return login is not None and login.casefold() in trusted_logins


def submitted_rank(review: dict[str, Any]) -> tuple[datetime, int] | None:
    review_id = numeric_id(review.get("id"))
    submitted_at = review.get("submitted_at")
    if review_id is None or not isinstance(submitted_at, str) or not submitted_at:
        return None

    timestamp = submitted_at[:-1] + "+00:00" if submitted_at.endswith("Z") else submitted_at
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return (parsed.astimezone(timezone.utc), review_id)


def fetch_api_collection(repository: str, pr_number: str, resource: str) -> list[dict[str, Any]]:
    endpoint = f"repos/{repository}/pulls/{pr_number}/{resource}?per_page=100"
    completed = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", endpoint],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"gh exited with status {completed.returncode}"
        raise FeedbackDataError(f"GitHub API request failed for {resource}: {detail}")

    try:
        pages = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise FeedbackDataError(
            f"GitHub API returned invalid JSON for {resource}: {exc}"
        ) from exc
    if not isinstance(pages, list):
        raise FeedbackDataError(f"GitHub API page envelope for {resource} is not an array")

    records: list[dict[str, Any]] = []
    for page_number, page in enumerate(pages, start=1):
        if not isinstance(page, list):
            raise FeedbackDataError(
                f"GitHub API page {page_number} for {resource} is not an array"
            )
        for item_number, item in enumerate(page, start=1):
            if not isinstance(item, dict):
                raise FeedbackDataError(
                    f"GitHub API item {item_number} on page {page_number} "
                    f"for {resource} is not an object"
                )
            records.append(item)
    return records


def fetch_repository_permission(repository: str, login: str) -> str | None:
    completed = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repository}/collaborators/{login}/permission",
            "--jq",
            ".permission",
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    permission = completed.stdout.strip().lower()
    return permission if permission else None


def resolve_trusted_logins(
    repository: str,
    reviews: list[dict[str, Any]],
    review_comments: list[dict[str, Any]],
) -> set[str]:
    # An author association is not evidence of write access: MEMBER covers
    # every organisation member. Only reviewers the repository reports as
    # writers are trusted.
    comments_for_review = {
        review_id
        for comment in review_comments
        if (review_id := numeric_id(comment.get("pull_request_review_id"))) is not None
    }
    candidate_review_ids: set[int] = set()
    candidate_logins: set[str] = set()

    for rank, review in latest_submitted_reviews(reviews):
        review_id = rank[1]
        state = str(review.get("state") or "").upper()
        body = str(review.get("body") or "").strip()
        if state not in ACTIONABLE_STATES or not (body or review_id in comments_for_review):
            continue
        candidate_review_ids.add(review_id)
        if login := reviewer_login(review):
            candidate_logins.add(login)

    for comment in review_comments:
        review_id = numeric_id(comment.get("pull_request_review_id"))
        if review_id not in candidate_review_ids:
            continue
        if login := reviewer_login(comment):
            candidate_logins.add(login)

    trusted_logins: set[str] = set()
    for login in sorted(candidate_logins, key=str.casefold):
        if fetch_repository_permission(repository, login) in TRUSTED_PERMISSIONS:
            trusted_logins.add(login.casefold())
    return trusted_logins


def comments_by_review_id(
    review_comments: list[dict[str, Any]],
    trusted_logins: set[str] | None = None,
) -> dict[int, list[dict[str, Any]]]:
    trusted_logins = trusted_logins or set()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for comment in review_comments:
        review_id = numeric_id(comment.get("pull_request_review_id"))
        if (
            review_id is not None
            and reviewer_login(comment) is not None
            and is_trusted_reviewer(comment, trusted_logins)
        ):
            grouped.setdefault(review_id, []).append(comment)
    return grouped


def latest_submitted_reviews(
    reviews: list[dict[str, Any]],
) -> list[tuple[tuple[datetime, int], dict[str, Any]]]:
    latest: dict[
        tuple[str, object], tuple[tuple[datetime, int], dict[str, Any]]
    ] = {}
    unorderable_reviewers: set[tuple[str, object]] = set()

    for review in reviews:
        identity = reviewer_identity(review)
        if identity is None:
            continue

        state = str(review.get("state") or "").upper()
        rank = submitted_rank(review)
        if rank is None:
            # GitHub pending reviews are not submitted and cannot supersede feedback.
            if state != "PENDING":
                unorderable_reviewers.add(identity)
            continue

        current = latest.get(identity)
        if current is None or rank > current[0]:
            latest[identity] = (rank, review)
        elif rank == current[0]:
            current_state = str(current[1].get("state") or "").upper()
            if state != current_state:
                unorderable_reviewers.add(identity)

    return [
        ranked_review
        for identity, ranked_review in latest.items()
        if identity not in unorderable_reviewers
    ]


def select_actionable_review(
    reviews: list[dict[str, Any]],
    review_comments: list[dict[str, Any]],
    trusted_logins: set[str] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    trusted_logins = trusted_logins or set()
    grouped_comments = comments_by_review_id(review_comments, trusted_logins)
    actionable: list[tuple[tuple[datetime, int], dict[str, Any]]] = []

    for rank, review in latest_submitted_reviews(reviews):
        review_id = rank[1]
        state = str(review.get("state") or "").upper()
        body = str(review.get("body") or "").strip()
        comments = grouped_comments.get(review_id, [])
        if (
            state in ACTIONABLE_STATES
            and is_trusted_reviewer(review, trusted_logins)
            and (body or comments)
        ):
            actionable.append((rank, review))

    if not actionable:
        return None, []
    _, selected = max(actionable, key=lambda ranked_review: ranked_review[0])
    selected_id = numeric_id(selected.get("id"))
    return selected, grouped_comments.get(selected_id, []) if selected_id else []


def format_review_environment(
    review: dict[str, Any] | None, comments: list[dict[str, Any]]
) -> str:
    if review is None:
        return f"SKIP_REASON={shlex.quote('no actionable trusted review feedback was found')}\n"

    formatted_comments = []
    for index, comment in enumerate(comments, start=1):
        user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
        author = str(user.get("login") or "").strip()
        association = str(comment.get("author_association") or "").upper()
        path = str(comment.get("path") or "").strip()
        url = str(comment.get("html_url") or "").strip()
        diff_hunk = str(comment.get("diff_hunk") or "").strip()
        body = str(comment.get("body") or "").strip()

        parts = [
            f"Inline comment {index}:",
            f"Author: @{author} ({association})",
        ]
        if url:
            parts.append(f"URL: {url}")
        if path:
            parts.append(f"File path: {path}")
        if diff_hunk:
            parts.append(f"Diff hunk:\n{diff_hunk}")
        if body:
            parts.append(f"Comment:\n{body}")
        formatted_comments.append("\n".join(parts))

    user = review.get("user") if isinstance(review.get("user"), dict) else {}
    values = {
        "COMMENT_KIND": "pull_request_review",
        "COMMENT_AUTHOR": str(user.get("login") or ""),
        "COMMENT_BODY": str(review.get("body") or "").strip(),
        "COMMENT_URL": str(review.get("html_url") or ""),
        "REVIEW_STATE": str(review.get("state") or ""),
        "REVIEW_ID": str(numeric_id(review.get("id")) or ""),
        "REVIEW_COMMENTS": "\n".join(formatted_comments),
    }
    return "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items())


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    timestamp = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def current_candidate_reviews(
    reviews: list[dict[str, Any]], head_sha: str, command_time: datetime
) -> list[tuple[tuple[datetime, int], dict[str, Any]]]:
    """Actionable reviews of the current head, submitted by the time of the
    command, that the same reviewer has not since approved."""
    submitted: list[tuple[tuple[datetime, int], tuple[str, object], dict[str, Any]]] = []
    approvals: dict[tuple[str, object], tuple[datetime, int]] = {}
    for review in reviews:
        identity = reviewer_identity(review)
        rank = submitted_rank(review)
        if identity is None or rank is None or rank[0] > command_time:
            continue
        submitted.append((rank, identity, review))
        if str(review.get("state") or "").upper() == "APPROVED":
            if identity not in approvals or rank > approvals[identity]:
                approvals[identity] = rank

    candidates = []
    for rank, identity, review in sorted(submitted, key=lambda item: item[0]):
        if str(review.get("state") or "").upper() not in ACTIONABLE_STATES:
            continue
        if identity in approvals and rank < approvals[identity]:
            continue
        if review.get("commit_id") != head_sha:
            continue
        candidates.append((rank, review))
    return candidates


def current_review_comments(
    review_comments: list[dict[str, Any]],
    review_ids: set[int],
    head_sha: str,
    command_time: datetime,
) -> list[dict[str, Any]]:
    """Inline comments of the given reviews that still apply to the current
    head and were neither written nor edited after the command."""
    current = []
    for comment in review_comments:
        if numeric_id(comment.get("pull_request_review_id")) not in review_ids:
            continue
        if comment.get("commit_id") != head_sha:
            continue
        created = parse_timestamp(comment.get("created_at"))
        updated = parse_timestamp(comment.get("updated_at") or comment.get("created_at"))
        if created is None or updated is None or created > command_time or updated > command_time:
            continue
        if (
            comment.get("line") is None
            and comment.get("position") is None
            and comment.get("subject_type") != "file"
        ):
            continue
        current.append(comment)
    return current


def resolve_current_trusted_logins(
    repository: str,
    candidates: list[tuple[tuple[datetime, int], dict[str, Any]]],
    review_comments: list[dict[str, Any]],
    head_sha: str,
    command_time: datetime,
) -> set[str]:
    review_ids = {rank[1] for rank, _ in candidates}
    logins = {login for _, review in candidates if (login := reviewer_login(review))}
    for comment in current_review_comments(review_comments, review_ids, head_sha, command_time):
        if login := reviewer_login(comment):
            logins.add(login)
    trusted_logins: set[str] = set()
    for login in sorted(logins, key=str.casefold):
        if fetch_repository_permission(repository, login) in TRUSTED_PERMISSIONS:
            trusted_logins.add(login.casefold())
    return trusted_logins


def select_current_reviews(
    reviews: list[dict[str, Any]],
    review_comments: list[dict[str, Any]],
    trusted_logins: set[str],
    head_sha: str,
    command_time: datetime,
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    trusted = [
        (rank, review)
        for rank, review in current_candidate_reviews(reviews, head_sha, command_time)
        if is_trusted_reviewer(review, trusted_logins)
    ]
    review_ids = {rank[1] for rank, _ in trusted}
    grouped: dict[int, list[dict[str, Any]]] = {}
    for comment in current_review_comments(review_comments, review_ids, head_sha, command_time):
        if is_trusted_reviewer(comment, trusted_logins):
            grouped.setdefault(numeric_id(comment.get("pull_request_review_id")), []).append(comment)

    selected = []
    for rank, review in trusted:
        comments = grouped.get(rank[1], [])
        if str(review.get("body") or "").strip() or comments:
            selected.append((review, comments))
    return selected


def format_current_environment(
    selected: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    command_author: str,
    command_url: str,
    head_sha: str,
) -> str:
    if not selected:
        reason = "no current trusted review feedback was found on the PR head"
        return f"SKIP_REASON={shlex.quote(reason)}\n"

    blocks = []
    for review_index, (review, comments) in enumerate(selected, start=1):
        user = review.get("user") if isinstance(review.get("user"), dict) else {}
        parts = [
            f"Review {review_index}:",
            f"Author: @{str(user.get('login') or '').strip()}",
            f"State: {str(review.get('state') or '').upper()}",
        ]
        if url := str(review.get("html_url") or "").strip():
            parts.append(f"URL: {url}")
        if body := str(review.get("body") or "").strip():
            parts.append(f"Review body:\n{body}")
        for comment_index, comment in enumerate(comments, start=1):
            commenter = comment.get("user") if isinstance(comment.get("user"), dict) else {}
            parts.append(f"Inline comment {review_index}.{comment_index}:")
            parts.append(f"Author: @{str(commenter.get('login') or '').strip()}")
            if url := str(comment.get("html_url") or "").strip():
                parts.append(f"URL: {url}")
            if path := str(comment.get("path") or "").strip():
                parts.append(f"File path: {path}")
            if diff_hunk := str(comment.get("diff_hunk") or "").strip():
                parts.append(f"Diff hunk:\n{diff_hunk}")
            if body := str(comment.get("body") or "").strip():
                parts.append(f"Comment:\n{body}")
        blocks.append("\n".join(parts))

    review_text = "\n\n".join(blocks)
    if len(review_text.encode("utf-8")) > MAX_CURRENT_FEEDBACK_BYTES:
        raise FeedbackDataError(
            "current review feedback exceeds the 64 KiB prompt limit; resolve some of it before retrying"
        )
    values = {
        "COMMENT_KIND": "pull_request_reviews",
        "COMMENT_AUTHOR": command_author,
        "COMMENT_BODY": f"Address the feedback in all {len(selected)} review(s) listed below.",
        "COMMENT_URL": command_url,
        "REVIEW_STATE": "",
        "REVIEW_ID": "",
        "REVIEW_COMMENTS": review_text,
        "REVIEW_HEAD_SHA": head_sha,
    }
    return "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items())


def fetch_head_sha(repository: str, pr_number: str) -> str:
    completed = subprocess.run(
        ["gh", "api", f"repos/{repository}/pulls/{pr_number}", "--jq", ".head.sha"],
        capture_output=True,
        text=True,
    )
    head_sha = completed.stdout.strip()
    if completed.returncode != 0 or len(head_sha) != 40 or any(
        character not in "0123456789abcdef" for character in head_sha
    ):
        raise FeedbackDataError("GitHub API did not return the pull request head revision")
    return head_sha


def collect_current_reviews(
    args: argparse.Namespace,
    reviews: list[dict[str, Any]],
    comments: list[dict[str, Any]],
) -> str:
    command_time = parse_timestamp(args.command_created_at)
    if command_time is None or not args.command_author:
        raise FeedbackDataError("the /codex-review command time and author are required")
    if fetch_repository_permission(args.repository, args.command_author) not in TRUSTED_PERMISSIONS:
        reason = "the /codex-review author does not have write access to this repository"
        return f"SKIP_REASON={shlex.quote(reason)}\n"
    head_sha = fetch_head_sha(args.repository, args.pr_number)
    candidates = current_candidate_reviews(reviews, head_sha, command_time)
    trusted_logins = resolve_current_trusted_logins(
        args.repository, candidates, comments, head_sha, command_time
    )
    selected = select_current_reviews(reviews, comments, trusted_logins, head_sha, command_time)
    return format_current_environment(selected, args.command_author, args.command_url, head_sha)


def write_json_atomic(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as output_file:
        temporary_path = Path(output_file.name)
        json.dump(records, output_file, separators=(",", ":"))
        output_file.write("\n")
    os.replace(temporary_path, path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect and reduce trusted pull request review feedback."
    )
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", required=True)
    parser.add_argument("--reviews-output", type=Path, required=True)
    parser.add_argument("--comments-output", type=Path, required=True)
    parser.add_argument("--env-output", type=Path, required=True)
    parser.add_argument("--selection", choices=SELECTIONS, default="latest-review")
    parser.add_argument("--command-author", default="")
    parser.add_argument("--command-url", default="")
    parser.add_argument("--command-created-at", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        reviews = fetch_api_collection(args.repository, args.pr_number, "reviews")
        comments = fetch_api_collection(args.repository, args.pr_number, "comments")
        if args.selection == "current-reviews":
            environment = collect_current_reviews(args, reviews, comments)
        else:
            trusted_logins = resolve_trusted_logins(args.repository, reviews, comments)
            selected_review, selected_comments = select_actionable_review(
                reviews, comments, trusted_logins
            )
            environment = format_review_environment(selected_review, selected_comments)

        write_json_atomic(args.reviews_output, reviews)
        write_json_atomic(args.comments_output, comments)
        with args.env_output.open("a", encoding="utf-8") as env_file:
            env_file.write(environment)
    except (FeedbackDataError, OSError) as exc:
        print(f"Unable to prepare pull request review feedback: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
