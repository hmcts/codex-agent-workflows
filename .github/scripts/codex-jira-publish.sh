#!/usr/bin/env bash

set -euo pipefail

required_env() {
  local name="$1"

  if [[ -z "${!name:-}" ]]; then
    echo "Missing required environment variable: ${name}" >&2
    exit 1
  fi
}

required_env "ISSUE_KEY"
required_env "ISSUE_SUMMARY"
required_env "ISSUE_URL"
required_env "GH_TOKEN"
required_env "BOT_PUBLISHER_LOGIN"
required_env "BOT_PUBLISHER_EMAIL"
required_env "OUTPUT_DIR"
required_env "VERIFICATION_DIR"
required_env "EXPECTED_BRANCH_NAME"
required_env "EXPECTED_BASE_SHA"
required_env "JIRA_PUBLISH_MODE"
required_env "CODEX_RUNTIME_PATH"
required_env "GITHUB_REPOSITORY"

case "${JIRA_PUBLISH_MODE}" in
  initial)
    expected_branch_head_sha=""
    ;;
  repair)
    required_env "EXPECTED_BRANCH_HEAD_SHA"
    expected_branch_head_sha="${EXPECTED_BRANCH_HEAD_SHA}"
    if [[ ! "${expected_branch_head_sha}" =~ ^[0-9a-f]{40}$ ]]; then
      echo "Invalid expected Jira repair branch SHA: ${expected_branch_head_sha}" >&2
      exit 1
    fi
    ;;
  *)
    echo "JIRA_PUBLISH_MODE must be either initial or repair." >&2
    exit 1
    ;;
esac

default_branch="${DEFAULT_BRANCH:-master}"
publisher_login="${BOT_PUBLISHER_LOGIN}"
publisher_email="${BOT_PUBLISHER_EMAIL}"
output_dir="${OUTPUT_DIR}"
metadata_path="${output_dir}/metadata.env"
patch_path="${output_dir}/changes.patch"
verification_dir="${VERIFICATION_DIR}"
verification_path="${verification_dir}/verification.env"
pr_body_path="${verification_dir}/codex-pr-body.md"
trusted_notify_path="${RUNNER_TEMP:-/tmp}/trusted-notify-jira-automation.py"
notify_source_path="${CODEX_RUNTIME_PATH}/.github/scripts/notify-jira-automation.py"
pr_recovery_path="${CODEX_RUNTIME_PATH}/.github/scripts/codex-recover-pr-state.py"
pr_recovery_output="${RUNNER_TEMP:-/tmp}/codex-jira-pr-recovery.env"
sanitized_home="${RUNNER_TEMP:-/tmp}/codex-jira-publish-home"
sanitized_tmp="${RUNNER_TEMP:-/tmp}/codex-jira-publish-tmp"

metadata_value() {
  local key="$1"
  awk -F= -v key="${key}" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "${metadata_path}"
}

verification_value() {
  local key="$1"
  awk -F= -v key="${key}" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "${verification_path}"
}

file_sha256() {
  local path="$1"

  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${path}" | awk '{print $1}'
  else
    shasum -a 256 "${path}" | awk '{print $1}'
  fi
}

git_authenticated() {
  env -i \
    "HOME=${sanitized_home}" \
    "PATH=${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}" \
    "SHELL=${SHELL:-/bin/bash}" \
    "USER=${USER:-runner}" \
    "LOGNAME=${LOGNAME:-${USER:-runner}}" \
    "LANG=${LANG:-C.UTF-8}" \
    "LC_ALL=${LC_ALL:-${LANG:-C.UTF-8}}" \
    "TERM=${TERM:-xterm}" \
    "TMPDIR=${sanitized_tmp}" \
    "GIT_CONFIG_GLOBAL=/dev/null" \
    "GIT_CONFIG_NOSYSTEM=1" \
    "GIT_TERMINAL_PROMPT=0" \
    "GH_TOKEN=${GH_TOKEN}" \
    git \
    -c core.hooksPath=/dev/null \
    -c credential.helper= \
    -c credential.helper='!f() { test "$1" = get && echo username=x-access-token && echo "password=$GH_TOKEN"; }; f' \
    -c protocol.file.allow=never \
    "$@"
}

git_local() {
  env -i \
    "HOME=${sanitized_home}" \
    "PATH=${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}" \
    "SHELL=${SHELL:-/bin/bash}" \
    "USER=${USER:-runner}" \
    "LOGNAME=${LOGNAME:-${USER:-runner}}" \
    "LANG=${LANG:-C.UTF-8}" \
    "LC_ALL=${LC_ALL:-${LANG:-C.UTF-8}}" \
    "TERM=${TERM:-xterm}" \
    "TMPDIR=${sanitized_tmp}" \
    "GIT_CONFIG_GLOBAL=/dev/null" \
    "GIT_CONFIG_NOSYSTEM=1" \
    "GIT_TERMINAL_PROMPT=0" \
    git \
    -c core.hooksPath=/dev/null \
    -c credential.helper= \
    -c protocol.file.allow=never \
    "$@"
}

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

run_notify() {
  env -i \
    "HOME=${sanitized_home}" \
    "PATH=${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}" \
    "LANG=${LANG:-C.UTF-8}" \
    "LC_ALL=${LC_ALL:-${LANG:-C.UTF-8}}" \
    "TERM=${TERM:-xterm}" \
    "TMPDIR=${sanitized_tmp}" \
    "ISSUE_KEY=${ISSUE_KEY}" \
    "ISSUE_SUMMARY=${ISSUE_SUMMARY}" \
    "ISSUE_URL=${ISSUE_URL}" \
    "GITHUB_REPOSITORY=${GITHUB_REPOSITORY}" \
    "GITHUB_ACTOR=${GITHUB_ACTOR:-}" \
    "GITHUB_RUN_ID=${GITHUB_RUN_ID:-}" \
    "GITHUB_SERVER_URL=${GITHUB_SERVER_URL:-https://github.com}" \
    "CODEX_JIRA_PR_NOTIFY_URL=${CODEX_JIRA_PR_NOTIFY_URL:-}" \
    "CODEX_JIRA_PR_NOTIFY_TIMEOUT_SECONDS=${CODEX_JIRA_PR_NOTIFY_TIMEOUT_SECONDS:-10}" \
    python3 -I "${trusted_notify_path}" "$@"
}

persist_output() {
  local key="$1"
  local value="$2"

  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf '%s=%s\n' "${key}" "${value}" >>"${GITHUB_OUTPUT}"
  fi
}

verify_default_unchanged() {
  local remote_ref
  local current_default_sha

  if [[ ! "${verified_base_sha}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Invalid expected default-branch SHA: ${verified_base_sha}" >&2
    exit 1
  fi
  if ! remote_ref="$(git_authenticated ls-remote --exit-code --heads origin "refs/heads/${default_branch}")" || [[ -z "${remote_ref}" ]]; then
    echo "::error title=Default branch unavailable::The current ${default_branch} revision could not be resolved before Jira publication." >&2
    exit 1
  fi
  current_default_sha="$(awk '{print $1}' <<<"${remote_ref}")"
  if [[ ! "${current_default_sha}" =~ ^[0-9a-f]{40}$ || "${current_default_sha}" != "${verified_base_sha}" ]]; then
    echo "::error title=Default branch moved::The ${default_branch} branch changed before Jira publication." >&2
    exit 1
  fi
}

verify_generated_branch_unchanged() {
  local remote_ref
  local current_branch_sha

  if ! remote_ref="$(git_authenticated ls-remote --exit-code --heads origin "refs/heads/${branch_name}")" || [[ -z "${remote_ref}" ]]; then
    echo "::error title=Generated branch unavailable::The current ${branch_name} revision could not be resolved before pull request creation." >&2
    exit 1
  fi
  current_branch_sha="$(awk '{print $1}' <<<"${remote_ref}")"
  if [[ ! "${current_branch_sha}" =~ ^[0-9a-f]{40}$ || "${current_branch_sha}" != "${commit_sha}" ]]; then
    echo "::error title=Generated branch moved::The ${branch_name} branch changed before pull request creation." >&2
    exit 1
  fi
}

recover_pr_state() {
  local allow_missing="${1:-false}"
  local created_pr_url="${2:-}"
  local recovery_args=(
    --repository "${GITHUB_REPOSITORY}"
    --base-ref "${default_branch}"
    --base-sha "${verified_base_sha}"
    --head-ref "${branch_name}"
    --head-sha "${commit_sha}"
    --draft "${expected_pr_draft}"
    --output "${pr_recovery_output}"
  )

  if [[ "${allow_missing}" == "true" ]]; then
    recovery_args+=(--allow-missing)
  fi
  if [[ -n "${created_pr_url}" ]]; then
    recovery_args+=(--pr-url "${created_pr_url}")
  fi
  python3 -I "${pr_recovery_path}" "${recovery_args[@]}"
}

mkdir -p "${sanitized_home}" "${sanitized_tmp}"

if [[ ! -f "${notify_source_path}" || -L "${notify_source_path}" ]]; then
  echo "Missing trusted Jira notification runtime: ${notify_source_path}" >&2
  exit 1
fi
install -m 0500 "${notify_source_path}" "${trusted_notify_path}"
if [[ ! -f "${pr_recovery_path}" || -L "${pr_recovery_path}" ]]; then
  echo "Missing trusted pull request recovery runtime: ${pr_recovery_path}" >&2
  exit 1
fi

branch_name="$(metadata_value branch_name)"
verified_branch_name="$(verification_value branch_name)"
verified_base_sha="$(verification_value base_sha)"
verified_patch_sha="$(verification_value patch_sha)"
expected_pr_draft="${PR_DRAFT:-false}"

if [[ "${expected_pr_draft}" != "true" && "${expected_pr_draft}" != "false" ]]; then
  echo "PR_DRAFT must be either true or false." >&2
  exit 1
fi

if [[ "${branch_name}" != "${EXPECTED_BRANCH_NAME}" || "${branch_name}" != "${verified_branch_name}" || "${branch_name}" != codex/* ]]; then
  echo "Refusing to publish unexpected Codex branch name: ${branch_name}" >&2
  exit 1
fi

if [[ "${verified_base_sha}" != "${EXPECTED_BASE_SHA}" ]]; then
  echo "Refusing to publish Codex patch because the verified base SHA does not match the archived source revision." >&2
  exit 1
fi

if [[ ! -s "${patch_path}" ]]; then
  echo "Missing or empty Codex patch artifact: ${patch_path}" >&2
  exit 1
fi

actual_patch_sha="$(file_sha256 "${patch_path}")"
if [[ -z "${verified_patch_sha}" || "${actual_patch_sha}" != "${verified_patch_sha}" ]]; then
  echo "Refusing to publish Codex patch because it does not match the verified patch hash." >&2
  exit 1
fi

if [[ ! -s "${pr_body_path}" ]]; then
  echo "Missing verified Codex PR body artifact: ${pr_body_path}" >&2
  exit 1
fi

commit_subject="$(
  python3 -I - <<'PY'
import os

issue_key = os.environ["ISSUE_KEY"].strip()
summary = " ".join(os.environ["ISSUE_SUMMARY"].split())
subject = f"{issue_key}: {summary}"
print(subject[:72].rstrip())
PY
)"

verify_default_unchanged

remote_branch_sha="$(git_authenticated ls-remote --heads origin "refs/heads/${branch_name}" | awk '{print $1}')"
if [[ "${JIRA_PUBLISH_MODE}" == "repair" && -z "${remote_branch_sha}" ]]; then
  echo "::error title=Generated branch missing::The remote ${branch_name} branch is unavailable, so the repaired patch cannot be published safely." >&2
  exit 1
fi

git_authenticated fetch origin "${verified_base_sha}:refs/remotes/origin/${default_branch}"
git_authenticated checkout -B "${default_branch}" "${verified_base_sha}"
git_authenticated checkout -B "${branch_name}"
git_local apply --index --binary "${patch_path}"

git_authenticated \
  -c user.name="${publisher_login}" \
  -c user.email="${publisher_email}" \
  commit \
  -m "${commit_subject}" \
  -m "Jira: ${ISSUE_URL}" \
  -m "Generated by Codex on a team-managed runner for ${ISSUE_KEY}."
commit_sha="$(git_local rev-parse HEAD)"
local_tree_sha="$(git_local rev-parse 'HEAD^{tree}')"

latest_branch_sha="$(git_authenticated ls-remote --heads origin "refs/heads/${branch_name}" | awk '{print $1}')"
if [[ "${JIRA_PUBLISH_MODE}" == "initial" ]]; then
  if [[ -z "${remote_branch_sha}" && -n "${latest_branch_sha}" ]]; then
    echo "::error title=Generated branch appeared::The remote ${branch_name} branch was created while the verified commit was being prepared. Re-run with a new generated branch or intervene manually." >&2
    exit 1
  fi
  if [[ -z "${remote_branch_sha}" ]]; then
    verify_default_unchanged
    git_authenticated push \
      --force-with-lease="refs/heads/${branch_name}:" \
      --set-upstream origin "${branch_name}"
  elif [[ "${latest_branch_sha}" == "${remote_branch_sha}" ]]; then
    git_authenticated fetch origin "+refs/heads/${branch_name}:refs/remotes/origin/${branch_name}"
    remote_commit_line="$(git_local rev-list --parents -n 1 "${remote_branch_sha}")"
    read -r -a remote_commit_parts <<<"${remote_commit_line}"
    remote_tree_sha="$(git_local rev-parse "${remote_branch_sha}^{tree}")"
    stable_branch_sha="$(git_authenticated ls-remote --heads origin "refs/heads/${branch_name}" | awk '{print $1}')"
    if [[ "${#remote_commit_parts[@]}" -ne 2 || "${remote_commit_parts[1]}" != "${verified_base_sha}" || "${remote_tree_sha}" != "${local_tree_sha}" || "${stable_branch_sha}" != "${remote_branch_sha}" ]]; then
      echo "::error title=Existing generated branch mismatch::The remote ${branch_name} branch does not have the exact verified base and generated tree. Refusing to create or recover a pull request." >&2
      exit 1
    fi
    verify_default_unchanged
    commit_sha="${remote_branch_sha}"
    echo "Recovered exact previously pushed branch ${branch_name} at ${commit_sha}."
  else
    echo "::error title=Generated branch moved::The remote ${branch_name} branch moved while recovery was being prepared. Refusing to create a pull request." >&2
    exit 1
  fi
else
  if [[ "${remote_branch_sha}" == "${expected_branch_head_sha}" && "${latest_branch_sha}" != "${expected_branch_head_sha}" ]]; then
    echo "::error title=Generated branch moved::The remote ${branch_name} branch moved while the repaired commit was being prepared. Re-run the workflow or intervene manually; the repaired patch was not published." >&2
    exit 1
  fi
  if [[ "${remote_branch_sha}" == "${expected_branch_head_sha}" ]]; then
    verify_default_unchanged
    git_authenticated push \
      --force-with-lease="refs/heads/${branch_name}:${expected_branch_head_sha}" \
      --set-upstream origin "${branch_name}"
  elif [[ "${latest_branch_sha}" == "${remote_branch_sha}" ]]; then
    git_authenticated fetch origin "+refs/heads/${branch_name}:refs/remotes/origin/${branch_name}"
    remote_commit_line="$(git_local rev-list --parents -n 1 "${remote_branch_sha}")"
    read -r -a remote_commit_parts <<<"${remote_commit_line}"
    remote_tree_sha="$(git_local rev-parse "${remote_branch_sha}^{tree}")"
    stable_branch_sha="$(git_authenticated ls-remote --heads origin "refs/heads/${branch_name}" | awk '{print $1}')"
    if [[ "${#remote_commit_parts[@]}" -ne 2 || "${remote_commit_parts[1]}" != "${expected_branch_head_sha}" || "${remote_tree_sha}" != "${local_tree_sha}" || "${stable_branch_sha}" != "${remote_branch_sha}" ]]; then
      echo "::error title=Existing repair branch mismatch::The remote ${branch_name} branch does not have the exact verified head and repaired tree. Refusing to recover publication." >&2
      exit 1
    fi
    verify_default_unchanged
    commit_sha="${remote_branch_sha}"
    echo "Recovered exact previously pushed repair branch ${branch_name} at ${commit_sha}."
  else
    echo "::error title=Generated branch moved::The remote ${branch_name} branch moved while recovery was being prepared. Refusing to recover publication." >&2
    exit 1
  fi
fi

persist_output branch_name "${branch_name}"
persist_output commit_sha "${commit_sha}"

recover_pr_state true
pr_found="$(awk -F= '$1 == "found" { print $2; exit }' "${pr_recovery_output}")"
if [[ "${pr_found}" != "true" ]]; then
  create_args=(
    pr create
    --repo "${GITHUB_REPOSITORY}"
    --base "${default_branch}"
    --head "${branch_name}"
    --title "${ISSUE_KEY}: ${ISSUE_SUMMARY}"
    --body-file "${pr_body_path}"
  )
  if [[ "${expected_pr_draft}" == "true" ]]; then
    create_args+=(--draft)
  fi
  verify_default_unchanged
  verify_generated_branch_unchanged
  created_pr_url="$(gh_authenticated "${create_args[@]}")"
  recover_pr_state false "${created_pr_url}"
fi
pr_url="$(awk -F= '$1 == "pr_url" { sub(/^[^=]*=/, ""); print; exit }' "${pr_recovery_output}")"
pr_number="$(awk -F= '$1 == "pr_number" { print $2; exit }' "${pr_recovery_output}")"
persist_output pr_url "${pr_url}"
persist_output pr_number "${pr_number}"

verification_status="passed"
if [[ "${expected_pr_draft}" == "true" ]]; then
  verification_status="failed"
fi
run_notify \
  --pr-url "${pr_url}" \
  --branch-name "${branch_name}" \
  --commit-sha "${commit_sha}" \
  --draft "${expected_pr_draft}" \
  --verification-status "${verification_status}"
