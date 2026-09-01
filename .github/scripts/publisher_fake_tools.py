#!/usr/bin/env python3

from publisher_test_constants import *  # noqa: F403


class PublisherFakeToolsMixin:
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
