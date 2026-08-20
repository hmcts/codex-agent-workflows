#!/usr/bin/env python3
"""Regression tests for exact-revision Codex publishers."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Sequence

SCRIPT_DIR = Path(__file__).parent
JIRA_PUBLISHER = SCRIPT_DIR / "codex-jira-publish.sh"
REVIEW_PUBLISHER = SCRIPT_DIR / "codex-pr-review-publish.sh"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
NEW_SHA = "c" * 40
MOVED_SHA = "d" * 40
LOCAL_TREE_SHA = "e" * 40
OTHER_TREE_SHA = "f" * 40




class PublisherTestCase(unittest.TestCase):
    def make_fake_tools(
        self,
        root: Path,
        *,
        remote_base: str | Sequence[str],
        remote_head: str | Sequence[str],
        fail_pr_comment: bool = False,
        remote_parent: str = BASE_SHA,
        remote_tree: str = LOCAL_TREE_SHA,
        pr_exists: bool = True,
        fail_pr_create: bool = False,
        pr_head_sha: str = NEW_SHA,
        pr_base_ref: str = "master",
        pr_base_sha: object = BASE_SHA,
        pr_base_repository: str = "hmcts/example",
        pr_head_ref: str = "codex/example",
        pr_head_repository: str = "hmcts/example",
        pr_is_draft: bool = False,
        multiple_prs: bool = False,
        post_push_head: str = NEW_SHA,
    ) -> tuple[Path, Path]:
        fake_bin = root / "bin"
        fake_bin.mkdir()
        command_log = root / "git-commands.log"
        conflict_counter = root / "conflict-counter"
        base_sequence = [remote_base] if isinstance(remote_base, str) else list(remote_base)
        head_sequence = [remote_head] if isinstance(remote_head, str) else list(remote_head)
        base_responses = root / "base-responses"
        head_responses = root / "head-responses"
        base_counter = root / "base-counter"
        head_counter = root / "head-counter"
        pushed_head = root / "pushed-head"
        base_responses.write_text("\n".join(base_sequence) + "\n", encoding="utf-8")
        head_responses.write_text("\n".join(head_sequence) + "\n", encoding="utf-8")
        fake_git = fake_bin / "git"
        fake_git.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
args="$*"
printf '%s\\n' "$args" >>{str(command_log)!r}
next_response() {{
  local responses_path="$1"
  local counter_path="$2"
  local count=0
  if [[ -f "$counter_path" ]]; then
    count="$(cat "$counter_path")"
  fi
  count=$((count + 1))
  echo "$count" >"$counter_path"
  response="$(sed -n "${{count}}p" "$responses_path")"
  if [[ "$count" -gt "$(wc -l <"$responses_path")" ]]; then
    response="$(tail -n 1 "$responses_path")"
  fi
  printf '%s' "$response"
}}
if [[ "$args" == *"ls-remote"* ]]; then
  if [[ "$args" == *"refs/heads/master"* ]]; then
    response="$(next_response {str(base_responses)!r} {str(base_counter)!r})"
    if [[ -n "$response" ]]; then
      printf '%s\\trefs/heads/master\\n' "$response"
    fi
  elif [[ "$args" == *"refs/heads/codex/example"* ]]; then
    if [[ -f {str(pushed_head)!r} ]]; then
      response="$(cat {str(pushed_head)!r})"
    else
      response="$(next_response {str(head_responses)!r} {str(head_counter)!r})"
    fi
    if [[ -n "$response" ]]; then
      printf '%s\\trefs/heads/codex/example\\n' "$response"
    fi
  fi
elif [[ "$args" == *"push"* && "$args" == *"codex/example"* ]]; then
  printf '%s\\n' {post_push_head!r} >{str(pushed_head)!r}
elif [[ "$args" == *"rev-parse refs/remotes/origin/codex/example"* ]]; then
  printf '%s\\n' {HEAD_SHA!r}
elif [[ "$args" == *"rev-parse refs/remotes/origin/master"* ]]; then
  printf '%s\\n' {BASE_SHA!r}
elif [[ "$args" == *"rev-parse HEAD^{{tree}}"* ]]; then
  printf '%s\\n' {LOCAL_TREE_SHA!r}
elif [[ "$args" == *"rev-parse "*"^{{tree}}"* ]]; then
  printf '%s\\n' {remote_tree!r}
elif [[ "$args" == *"rev-list --parents -n 1"* ]]; then
  commit_sha="${{args##* }}"
  printf '%s %s\\n' "$commit_sha" {remote_parent!r}
elif [[ "$args" == *"rev-parse HEAD"* ]]; then
  printf '%s\\n' {NEW_SHA!r}
elif [[ "$args" == *"merge --no-commit --no-ff"* ]]; then
  exit 1
elif [[ "$args" == *"diff --name-only --diff-filter=U"* ]]; then
  count=0
  if [[ -f {str(conflict_counter)!r} ]]; then
    count="$(cat {str(conflict_counter)!r})"
  fi
  if [[ "$count" -eq 0 ]]; then
    printf '%s\\n' "example.txt"
  fi
  echo $((count + 1)) >{str(conflict_counter)!r}
fi
""",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)

        fake_gh = fake_bin / "gh"
        comment_body = root / "published-comment.md"
        pr_candidate = {
            "number": 42,
            "html_url": "https://github.com/hmcts/example/pull/42",
            "state": "open",
            "draft": pr_is_draft,
            "base": {
                "ref": pr_base_ref,
                "sha": pr_base_sha,
                "repo": {"full_name": pr_base_repository},
            },
            "head": {
                "ref": pr_head_ref,
                "sha": pr_head_sha,
                "repo": {"full_name": pr_head_repository},
            },
        }
        open_prs = [pr_candidate] if pr_exists else []
        if multiple_prs:
            duplicate = {**pr_candidate, "number": 43}
            duplicate["html_url"] = "https://github.com/hmcts/example/pull/43"
            open_prs.append(duplicate)
        paginated_prs = json.dumps([open_prs])
        pr_candidate_json = json.dumps(pr_candidate)
        fake_gh.write_text(
            f"""#!/usr/bin/env bash
set -euo pipefail
printf 'gh %s\n' "$*" >>{str(command_log)!r}
case "$*" in
  *"api --paginate --slurp repos/hmcts/example/pulls?state=open&per_page=100"*)
    printf '%s\n' {paginated_prs!r}
    ;;
  *"api repos/hmcts/example/pulls/42"*) printf '%s\n' {pr_candidate_json!r} ;;
  *"pr create"*)
    if [[ {str(fail_pr_create).lower()!r} == "true" ]]; then
      exit 24
    fi
    echo "https://github.com/hmcts/example/pull/42"
    ;;
  *"pr view"*) printf '42\tmaster\tcodex/example\t%s\n' {pr_head_sha!r} ;;
  *"pr comment"*)
    if [[ {str(fail_pr_comment).lower()!r} == "true" ]]; then
      exit 23
    fi
    previous=""
    for argument in "$@"; do
      if [[ "$previous" == "--body-file" ]]; then
        cp "$argument" {str(comment_body)!r}
      fi
      previous="$argument"
    done
    ;;
esac
""",
            encoding="utf-8",
        )
        fake_gh.chmod(0o755)
        return fake_bin, command_log

    @staticmethod
    def assert_no_push(commands: str) -> None:
        push_lines = [
            line
            for line in commands.splitlines()
            if line.startswith("push ") or " push " in line
        ]
        if push_lines:
            raise AssertionError(f"Unexpected Git push commands: {push_lines}")

    @staticmethod
    def assert_command_logged(commands: str, command: str) -> None:
        matching_lines = [
            line
            for line in commands.splitlines()
            if line.startswith(f"{command} ") or f" {command} " in line
        ]
        if not matching_lines:
            raise AssertionError(f"Git command was not logged: {command}")

    @staticmethod
    def run_real_git(
        cwd: Path,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env={
                **os.environ,
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
            },
            capture_output=True,
            text=True,
        )
        if check and completed.returncode != 0:
            raise AssertionError(
                f"git {' '.join(args)} failed:\n{completed.stdout}\n{completed.stderr}"
            )
        return completed

    def make_bare_remote(self, root: Path) -> tuple[Path, Path, Path]:
        remote = root / "remote.git"
        publisher = root / "publisher"
        racer = root / "racer"
        self.run_real_git(root, "init", "--bare", str(remote))
        self.run_real_git(root, "init", str(publisher))
        self.run_real_git(publisher, "config", "user.name", "Publisher")
        self.run_real_git(publisher, "config", "user.email", "publisher@example.invalid")
        (publisher / "seed.txt").write_text("seed\n", encoding="utf-8")
        self.run_real_git(publisher, "add", "seed.txt")
        self.run_real_git(publisher, "commit", "-m", "Seed")
        self.run_real_git(publisher, "branch", "-M", "master")
        self.run_real_git(publisher, "remote", "add", "origin", str(remote))
        self.run_real_git(publisher, "push", "-u", "origin", "master")
        self.run_real_git(root, "clone", str(remote), str(racer))
        self.run_real_git(racer, "config", "user.name", "Racer")
        self.run_real_git(racer, "config", "user.email", "racer@example.invalid")
        return remote, publisher, racer

    def commit_real_file(
        self,
        repository: Path,
        path: str,
        content: str,
        message: str,
    ) -> str:
        target = repository / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.run_real_git(repository, "add", path)
        self.run_real_git(repository, "commit", "-m", message)
        return self.run_real_git(repository, "rev-parse", "HEAD").stdout.strip()

    def remote_branch_sha(self, remote: Path, branch: str) -> str:
        return self.run_real_git(
            remote,
            "rev-parse",
            f"refs/heads/{branch}",
        ).stdout.strip()

    def assert_exact_lease_rejects_atomic_race(self, branch: str) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            remote, publisher, racer = self.make_bare_remote(Path(temporary_directory))
            self.run_real_git(publisher, "checkout", "-b", branch, "master")
            expected_sha = self.commit_real_file(
                publisher,
                "expected.txt",
                f"{branch} expected\n",
                "Create expected branch head",
            )
            self.run_real_git(publisher, "push", "-u", "origin", branch)
            self.commit_real_file(
                publisher,
                "publisher.txt",
                f"{branch} publisher\n",
                "Prepare publisher update",
            )

            self.run_real_git(racer, "fetch", "origin", branch)
            self.run_real_git(racer, "checkout", "-B", branch, f"origin/{branch}")
            moved_sha = self.commit_real_file(
                racer,
                "racer.txt",
                f"{branch} racer\n",
                "Move remote branch",
            )
            self.run_real_git(racer, "push", "origin", branch)

            completed = self.run_real_git(
                publisher,
                "push",
                f"--force-with-lease=refs/heads/{branch}:{expected_sha}",
                "origin",
                branch,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(self.remote_branch_sha(remote, branch), moved_sha)

    def assert_empty_lease_rejects_atomic_race(self, branch: str) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            remote, publisher, racer = self.make_bare_remote(Path(temporary_directory))
            self.run_real_git(publisher, "checkout", "-b", branch, "master")
            self.commit_real_file(
                publisher,
                "publisher.txt",
                f"{branch} publisher\n",
                "Prepare initial publication",
            )

            self.run_real_git(racer, "checkout", "-b", branch, "origin/master")
            moved_sha = self.commit_real_file(
                racer,
                "racer.txt",
                f"{branch} racer\n",
                "Create remote branch during publication",
            )
            self.run_real_git(racer, "push", "origin", branch)

            completed = self.run_real_git(
                publisher,
                "push",
                f"--force-with-lease=refs/heads/{branch}:",
                "origin",
                branch,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(self.remote_branch_sha(remote, branch), moved_sha)

    @staticmethod
    def write_patch_artifacts(root: Path, *, kind: str) -> tuple[Path, Path]:
        output = root / "output"
        verified = root / "verified"
        output.mkdir()
        verified.mkdir()
        patch = b"not-a-real-patch-but-git-is-stubbed\n"
        patch_sha = hashlib.sha256(patch).hexdigest()

        if kind == "jira":
            (output / "changes.patch").write_bytes(patch)
            (verified / "changes.patch").write_bytes(patch)
            (output / "metadata.env").write_text(
                "branch_name=codex/example\n",
                encoding="utf-8",
            )
            (verified / "verification.env").write_text(
                "branch_name=codex/example\n"
                f"base_sha={BASE_SHA}\n"
                f"patch_sha={patch_sha}\n",
                encoding="utf-8",
            )
            (verified / "codex-pr-body.md").write_text("PR body", encoding="utf-8")
        elif kind == "review":
            (output / "changes.patch").write_bytes(patch)
            (verified / "changes.patch").write_bytes(patch)
            (output / "metadata.env").write_text(
                "has_changes=true\n"
                "pr_number=42\n"
                "head_ref=codex/example\n"
                "base_ref=master\n"
                f"head_sha={HEAD_SHA}\n"
                f"base_sha={BASE_SHA}\n"
                "comment_author=reviewer\n"
                "comment_url=https://example.invalid/comment/1\n",
                encoding="utf-8",
            )
            (verified / "verification.env").write_text(
                "has_changes=true\n"
                "pr_number=42\n"
                "head_ref=codex/example\n"
                "base_ref=master\n"
                f"head_sha={HEAD_SHA}\n"
                f"base_sha={BASE_SHA}\n"
                f"patch_sha={patch_sha}\n",
                encoding="utf-8",
            )
            (output / "codex-final-message.md").write_text(
                "Updated the code.",
                encoding="utf-8",
            )
            (verified / "codex-review-comment.md").write_text(
                "Manual verification required: sensitive workflow files changed.\n",
                encoding="utf-8",
            )
        else:
            raise ValueError(f"Unsupported artifact kind: {kind}")

        return output, verified

    def run_review_with_outputs(
        self,
        remote_head: str,
        *,
        remote_base: str = BASE_SHA,
        fail_pr_comment: bool = False,
        remote_parent: str = BASE_SHA,
        remote_tree: str = LOCAL_TREE_SHA,
    ) -> tuple[subprocess.CompletedProcess[str], str, str, str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output, verified = self.write_patch_artifacts(root, kind="review")
            fake_bin, command_log = self.make_fake_tools(
                root,
                remote_base=remote_base,
                remote_head=remote_head,
                fail_pr_comment=fail_pr_comment,
                remote_parent=remote_parent,
                remote_tree=remote_tree,
            )
            github_output = root / "github-output"
            completed = subprocess.run(
                ["bash", str(REVIEW_PUBLISHER)],
                cwd=SCRIPT_DIR.parent.parent,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "GH_TOKEN": "test-token",
                    "BOT_PUBLISHER_LOGIN": "appreg-codex-bot",
                    "BOT_PUBLISHER_EMAIL": "12345+appreg-codex-bot[bot]@users.noreply.github.com",
                    "GITHUB_REPOSITORY": "hmcts/example",
                    "OUTPUT_DIR": str(output),
                    "VERIFICATION_DIR": str(verified),
                    "EXPECTED_PR_NUMBER": "42",
                    "EXPECTED_HEAD_REF": "codex/example",
                    "EXPECTED_HEAD_SHA": HEAD_SHA,
                    "DEFAULT_BRANCH": "master",
                    "EXPECTED_DEFAULT_SHA": BASE_SHA,
                    "RUNNER_TEMP": str(root / "runner"),
                    "GITHUB_OUTPUT": str(github_output),
                },
                capture_output=True,
                text=True,
            )
            commands = command_log.read_text(encoding="utf-8") if command_log.exists() else ""
            outputs = github_output.read_text(encoding="utf-8") if github_output.exists() else ""
            comment_path = root / "published-comment.md"
            comment = comment_path.read_text(encoding="utf-8") if comment_path.exists() else ""
            return completed, commands, outputs, comment

    def run_review(self, remote_head: str) -> tuple[subprocess.CompletedProcess[str], str]:
        completed, commands, _, _ = self.run_review_with_outputs(remote_head)
        return completed, commands

    def run_jira_with_outputs(
        self,
        *,
        mode: str,
        remote_base: str | Sequence[str] = BASE_SHA,
        remote_head: str | Sequence[str] = "",
        fail_notify: bool = False,
        remote_parent: str = BASE_SHA,
        remote_tree: str = LOCAL_TREE_SHA,
        pr_exists: bool = True,
        fail_pr_create: bool = False,
        pr_head_sha: str = NEW_SHA,
        pr_base_ref: str = "master",
        pr_base_sha: object = BASE_SHA,
        pr_base_repository: str = "hmcts/example",
        pr_head_ref: str = "codex/example",
        pr_head_repository: str = "hmcts/example",
        pr_is_draft: bool = False,
        multiple_prs: bool = False,
        expected_draft: bool = False,
        post_push_head: str = NEW_SHA,
    ) -> tuple[subprocess.CompletedProcess[str], str, str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            caller = root / "minimal-caller"
            caller.mkdir()
            output, verified = self.write_patch_artifacts(root, kind="jira")
            fake_bin, command_log = self.make_fake_tools(
                root,
                remote_base=remote_base,
                remote_head=remote_head,
                remote_parent=remote_parent,
                remote_tree=remote_tree,
                pr_exists=pr_exists,
                fail_pr_create=fail_pr_create,
                pr_head_sha=pr_head_sha,
                pr_base_ref=pr_base_ref,
                pr_base_sha=pr_base_sha,
                pr_base_repository=pr_base_repository,
                pr_head_ref=pr_head_ref,
                pr_head_repository=pr_head_repository,
                pr_is_draft=pr_is_draft,
                multiple_prs=multiple_prs,
                post_push_head=post_push_head,
            )
            github_output = root / "github-output"
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "GH_TOKEN": "test-token",
                "BOT_PUBLISHER_LOGIN": "appreg-codex-bot",
                "BOT_PUBLISHER_EMAIL": "12345+appreg-codex-bot[bot]@users.noreply.github.com",
                "GITHUB_REPOSITORY": "hmcts/example",
                "GITHUB_ACTOR": "tester",
                "ISSUE_KEY": "ARCPOC-1",
                "ISSUE_SUMMARY": "Example",
                "ISSUE_URL": "https://example.invalid/browse/ARCPOC-1",
                "OUTPUT_DIR": str(output),
                "VERIFICATION_DIR": str(verified),
                "EXPECTED_BRANCH_NAME": "codex/example",
                "EXPECTED_BASE_SHA": BASE_SHA,
                "JIRA_PUBLISH_MODE": mode,
                "DEFAULT_BRANCH": "master",
                "CODEX_RUNTIME_PATH": str(SCRIPT_DIR.parent.parent),
                "RUNNER_TEMP": str(root / "runner"),
                "GITHUB_OUTPUT": str(github_output),
                "PR_DRAFT": str(expected_draft).lower(),
            }
            if fail_notify:
                env["CODEX_JIRA_PR_NOTIFY_URL"] = "http://127.0.0.1:1/notify"
                env["CODEX_JIRA_PR_NOTIFY_TIMEOUT_SECONDS"] = "1"
            if mode == "repair":
                env["EXPECTED_BRANCH_HEAD_SHA"] = HEAD_SHA
            completed = subprocess.run(
                ["bash", str(JIRA_PUBLISHER)],
                cwd=caller,
                env=env,
                capture_output=True,
                text=True,
            )
            commands = command_log.read_text(encoding="utf-8") if command_log.exists() else ""
            outputs = github_output.read_text(encoding="utf-8") if github_output.exists() else ""
            return completed, commands, outputs

    def run_jira(
        self,
        *,
        mode: str,
        remote_base: str | Sequence[str] = BASE_SHA,
        remote_head: str | Sequence[str] = "",
        remote_parent: str = BASE_SHA,
        remote_tree: str = LOCAL_TREE_SHA,
        pr_exists: bool = True,
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        completed, commands, _ = self.run_jira_with_outputs(
            mode=mode,
            remote_base=remote_base,
            remote_head=remote_head,
            remote_parent=remote_parent,
            remote_tree=remote_tree,
            pr_exists=pr_exists,
        )
        return completed, commands
