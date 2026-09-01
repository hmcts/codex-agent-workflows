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


TRUSTED_ASSOCIATIONS = {"COLLABORATOR", "MEMBER", "OWNER"}
ACTIONABLE_STATES = {"CHANGES_REQUESTED", "COMMENTED"}


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


def comments_by_review_id(
    review_comments: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for comment in review_comments:
        review_id = numeric_id(comment.get("pull_request_review_id"))
        user = comment.get("user")
        association = str(comment.get("author_association") or "").upper()
        login = user.get("login") if isinstance(user, dict) else None
        if (
            review_id is not None
            and reviewer_identity(comment) is not None
            and association in TRUSTED_ASSOCIATIONS
            and isinstance(login, str)
            and login.strip()
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
    reviews: list[dict[str, Any]], review_comments: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    grouped_comments = comments_by_review_id(review_comments)
    actionable: list[tuple[tuple[datetime, int], dict[str, Any]]] = []

    for rank, review in latest_submitted_reviews(reviews):
        review_id = rank[1]
        state = str(review.get("state") or "").upper()
        association = str(review.get("author_association") or "").upper()
        body = str(review.get("body") or "").strip()
        comments = grouped_comments.get(review_id, [])
        if (
            state in ACTIONABLE_STATES
            and association in TRUSTED_ASSOCIATIONS
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        reviews = fetch_api_collection(args.repository, args.pr_number, "reviews")
        comments = fetch_api_collection(args.repository, args.pr_number, "comments")
        selected_review, selected_comments = select_actionable_review(reviews, comments)
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
