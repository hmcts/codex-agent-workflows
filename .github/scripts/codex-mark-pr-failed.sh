#!/usr/bin/env bash

set -euo pipefail

required_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required environment variable: ${name}" >&2
    exit 1
  fi
}

for name in GH_TOKEN GITHUB_REPOSITORY PR_NUMBER EXPECTED_HEAD_SHA FAILURE_MESSAGE; do
  required_env "${name}"
done

if [[ ! "${PR_NUMBER}" =~ ^[1-9][0-9]*$ ]]; then
  echo "Invalid pull request number: ${PR_NUMBER}" >&2
  exit 1
fi
if [[ ! "${EXPECTED_HEAD_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Invalid expected pull request head SHA: ${EXPECTED_HEAD_SHA}" >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
artifact_dir="${RUNNER_TEMP:-/tmp}/codex-terminal-failure-${GITHUB_RUN_ID:-manual}-${GITHUB_RUN_ATTEMPT:-1}"
sanitized_home="${artifact_dir}/home"
sanitized_tmp="${artifact_dir}/tmp"
comment_path="${artifact_dir}/comment.md"
failure_dir="${FAILURE_DIR:-}"

gh_authenticated() {
  env -i \
    "HOME=${sanitized_home}" \
    "PATH=${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}" \
    "LANG=${LANG:-C.UTF-8}" \
    "LC_ALL=${LC_ALL:-${LANG:-C.UTF-8}}" \
    "TERM=${TERM:-xterm}" \
    "TMPDIR=${sanitized_tmp}" \
    "GH_TOKEN=${GH_TOKEN}" \
    gh "$@"
}

mkdir -p "${artifact_dir}" "${sanitized_home}" "${sanitized_tmp}"

pr_json="$(gh_authenticated api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}")"
PR_JSON="${pr_json}" python3 -I - <<'PY'
import json
import os

pull_request = json.loads(os.environ["PR_JSON"])
repository = os.environ["GITHUB_REPOSITORY"]
expected_sha = os.environ["EXPECTED_HEAD_SHA"]
if pull_request.get("state") != "open":
    raise SystemExit("Refusing to update a pull request that is not open")
head = pull_request.get("head") or {}
head_repo = head.get("repo") or {}
if head.get("sha") != expected_sha:
    raise SystemExit("Refusing to update a pull request whose head revision moved")
if head_repo.get("full_name", "").casefold() != repository.casefold():
    raise SystemExit("Refusing to update a pull request from another repository")
PY

PR_JSON="${pr_json}" COMMENT_PATH="${comment_path}" FAILURE_DIR="${failure_dir}" python3 -I - <<'PY'
import html
import json
import os
from pathlib import Path

pull_request = json.loads(os.environ["PR_JSON"])
failure_dir = Path(os.environ["FAILURE_DIR"]) if os.environ.get("FAILURE_DIR") else None
evidence = ""
if failure_dir and failure_dir.is_dir():
    for name in ("verification-failure-summary.log", "verification-failure.log"):
        candidate = failure_dir / name
        if candidate.is_file() and not candidate.is_symlink():
            evidence = candidate.read_text(encoding="utf-8", errors="replace")[-40_000:].strip()
            if evidence:
                break

body = [
    "### Automated verification",
    "",
    "**Status: failed after the available automated repair attempts.**",
    "",
    os.environ["FAILURE_MESSAGE"].strip(),
    "",
    "This pull request has been returned to draft and requires human investigation.",
]
if evidence:
    body.extend(
        [
            "",
            "<details><summary>Final verification evidence</summary>",
            "",
            f"<pre>{html.escape(evidence)}</pre>",
            "",
            "</details>",
        ]
    )
Path(os.environ["COMMENT_PATH"]).write_text("\n".join(body) + "\n", encoding="utf-8")
PY

is_draft="$(PR_JSON="${pr_json}" python3 -I -c 'import json, os; print("true" if json.loads(os.environ["PR_JSON"]).get("draft") else "false")')"
if [[ "${is_draft}" != "true" ]]; then
  gh_authenticated pr ready "${PR_NUMBER}" --undo --repo "${GITHUB_REPOSITORY}"
fi
gh_authenticated pr comment "${PR_NUMBER}" --repo "${GITHUB_REPOSITORY}" --body-file "${comment_path}"

if [[ "${NOTIFY_JIRA:-false}" == "true" ]]; then
  issue_key="${ISSUE_KEY:-}"
  issue_url="${ISSUE_URL:-}"
  if [[ -z "${issue_key}" || -z "${issue_url}" ]]; then
    jira_metadata="$(
      printf '%s' "${pr_json}" | python3 -I "${script_dir}/extract-jira-pr-metadata.py"
    )"
    IFS=$'\t' read -r issue_key issue_url <<<"${jira_metadata}"
  fi
  env -i \
    "HOME=${sanitized_home}" \
    "PATH=${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}" \
    "LANG=${LANG:-C.UTF-8}" \
    "LC_ALL=${LC_ALL:-${LANG:-C.UTF-8}}" \
    "ISSUE_KEY=${issue_key}" \
    "ISSUE_URL=${issue_url}" \
    "GITHUB_REPOSITORY=${GITHUB_REPOSITORY}" \
    "GITHUB_RUN_ID=${GITHUB_RUN_ID:-}" \
    "GITHUB_SERVER_URL=${GITHUB_SERVER_URL:-https://github.com}" \
    "CODEX_JIRA_PR_NOTIFY_URL=${CODEX_JIRA_PR_NOTIFY_URL:-}" \
    "CODEX_JIRA_PR_NOTIFY_TIMEOUT_SECONDS=${CODEX_JIRA_PR_NOTIFY_TIMEOUT_SECONDS:-10}" \
    python3 -I "${script_dir}/notify-jira-automation.py" \
      --status failed \
      --message "${FAILURE_MESSAGE} Pull request #${PR_NUMBER} was returned to draft."
fi
