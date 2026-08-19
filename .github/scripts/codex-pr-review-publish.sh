#!/usr/bin/env bash

set -euo pipefail

required_env() {
  local name="$1"

  if [[ -z "${!name:-}" ]]; then
    echo "Missing required environment variable: ${name}" >&2
    exit 1
  fi
}

required_env "GH_TOKEN"
required_env "BOT_PUBLISHER_LOGIN"
required_env "BOT_PUBLISHER_EMAIL"
required_env "GITHUB_REPOSITORY"
required_env "OUTPUT_DIR"
required_env "VERIFICATION_DIR"
required_env "EXPECTED_PR_NUMBER"
required_env "EXPECTED_HEAD_REF"
required_env "EXPECTED_HEAD_SHA"
required_env "DEFAULT_BRANCH"
required_env "EXPECTED_DEFAULT_SHA"

output_dir="${OUTPUT_DIR}"
publisher_login="${BOT_PUBLISHER_LOGIN}"
publisher_email="${BOT_PUBLISHER_EMAIL}"
metadata_path="${output_dir}/metadata.env"
patch_path="${output_dir}/changes.patch"
final_message_path="${output_dir}/codex-final-message.md"
verification_dir="${VERIFICATION_DIR}"
verification_path="${verification_dir}/verification.env"
verified_comment_path="${verification_dir}/codex-review-comment.md"
comment_body_path="${RUNNER_TEMP:-/tmp}/codex-review-publication-comment.md"
sanitized_home="${RUNNER_TEMP:-/tmp}/codex-review-publish-home"
sanitized_tmp="${RUNNER_TEMP:-/tmp}/codex-review-publish-tmp"

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

  if [[ ! "${EXPECTED_DEFAULT_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Invalid expected default-branch SHA: ${EXPECTED_DEFAULT_SHA}" >&2
    exit 1
  fi
  if ! remote_ref="$(git_authenticated ls-remote --exit-code --heads origin "refs/heads/${DEFAULT_BRANCH}")"; then
    echo "::error title=Default branch unavailable::The current ${DEFAULT_BRANCH} revision could not be resolved before review publication." >&2
    exit 1
  fi
  current_default_sha="$(awk '{print $1}' <<<"${remote_ref}")"
  if [[ ! "${current_default_sha}" =~ ^[0-9a-f]{40}$ || "${current_default_sha}" != "${EXPECTED_DEFAULT_SHA}" ]]; then
    echo "::error title=Default branch moved::The ${DEFAULT_BRANCH} workflow tree changed before review publication." >&2
    exit 1
  fi
}

mkdir -p "${sanitized_home}" "${sanitized_tmp}"

has_changes="$(metadata_value has_changes)"
pr_number="$(metadata_value pr_number)"
head_ref="$(metadata_value head_ref)"
head_sha="$(metadata_value head_sha)"
verified_has_changes="$(verification_value has_changes)"
verified_pr_number="$(verification_value pr_number)"
verified_head_ref="$(verification_value head_ref)"
verified_head_sha="$(verification_value head_sha)"

if [[ "${has_changes}" != "true" ]]; then
  if [[ -z "${pr_number}" || -z "${head_ref}" ]]; then
    echo "Codex review generation skipped before selecting a PR branch."
    exit 0
  fi
  if [[ "${pr_number}" != "${EXPECTED_PR_NUMBER}" || "${head_ref}" != "${EXPECTED_HEAD_REF}" || "${head_sha}" != "${EXPECTED_HEAD_SHA}" || "${head_ref}" != codex/* ]]; then
    echo "Refusing to publish unexpected review artifact metadata." >&2
    exit 1
  fi
  if [[ "${verified_has_changes}" != "false" || "${verified_pr_number}" != "${pr_number}" || "${verified_head_ref}" != "${head_ref}" || "${verified_head_sha}" != "${head_sha}" ]]; then
    echo "Refusing to publish unverified no-change review result." >&2
    exit 1
  fi
  verify_default_unchanged
  persist_output pr_number "${pr_number}"
  persist_output branch_name "${head_ref}"
  if [[ -s "${verified_comment_path}" ]]; then
    gh_authenticated pr comment "${pr_number}" --repo "${GITHUB_REPOSITORY}" --body-file "${verified_comment_path}"
  fi
  echo "Codex produced no review-feedback changes for PR #${pr_number}."
  exit 0
fi

if [[ "${pr_number}" != "${EXPECTED_PR_NUMBER}" || "${head_ref}" != "${EXPECTED_HEAD_REF}" || "${head_sha}" != "${EXPECTED_HEAD_SHA}" || "${head_ref}" != codex/* ]]; then
  echo "Refusing to publish unexpected review artifact metadata." >&2
  exit 1
fi

if [[ "${verified_has_changes}" != "true" || "${verified_pr_number}" != "${pr_number}" || "${verified_head_ref}" != "${head_ref}" || "${verified_head_sha}" != "${head_sha}" ]]; then
  echo "Refusing to publish unexpected review artifact metadata." >&2
  exit 1
fi

if [[ ! -s "${patch_path}" ]]; then
  echo "Missing or empty Codex review patch artifact: ${patch_path}" >&2
  exit 1
fi

verified_patch_sha="$(verification_value patch_sha)"
actual_patch_sha="$(file_sha256 "${patch_path}")"
if [[ -z "${verified_patch_sha}" || "${actual_patch_sha}" != "${verified_patch_sha}" ]]; then
  echo "Refusing to publish Codex review patch because it does not match the verified patch hash." >&2
  exit 1
fi

commit_subject="Address Codex review feedback on PR #${EXPECTED_PR_NUMBER}"
commit_subject="${commit_subject:0:72}"

remote_head_sha="$(git_authenticated ls-remote --heads origin "refs/heads/${head_ref}" | awk '{print $1}')"
if [[ -z "${remote_head_sha}" ]]; then
  echo "::error title=PR branch missing::The remote ${head_ref} branch is unavailable, so review feedback cannot be published safely." >&2
  exit 1
fi
git_authenticated fetch origin "${verified_head_sha}:refs/remotes/origin/${head_ref}"
git_authenticated checkout -B "${head_ref}" "${verified_head_sha}"
git_local apply --index --binary "${patch_path}"

comment_url="$(metadata_value comment_url)"
comment_author="$(metadata_value comment_author)"

git_authenticated \
  -c user.name="${publisher_login}" \
  -c user.email="${publisher_email}" \
  commit \
  -m "${commit_subject}" \
  -m "Feedback: ${comment_url}" \
  -m "Generated by Codex on a team-managed runner."
commit_sha="$(git_local rev-parse HEAD)"
local_tree_sha="$(git_local rev-parse 'HEAD^{tree}')"

latest_head_sha="$(git_authenticated ls-remote --heads origin "refs/heads/${head_ref}" | awk '{print $1}')"
verify_default_unchanged
if [[ "${remote_head_sha}" == "${verified_head_sha}" ]]; then
  if [[ "${latest_head_sha}" != "${verified_head_sha}" ]]; then
    echo "::error title=PR branch moved::The remote ${head_ref} branch moved while the verified feedback commit was being prepared." >&2
    exit 1
  fi
  git_authenticated push --force-with-lease="refs/heads/${head_ref}:${verified_head_sha}" origin "${head_ref}"
elif [[ "${latest_head_sha}" == "${remote_head_sha}" ]]; then
  git_authenticated fetch origin "+refs/heads/${head_ref}:refs/remotes/origin/${head_ref}"
  remote_commit_line="$(git_local rev-list --parents -n 1 "${remote_head_sha}")"
  read -r -a remote_commit_parts <<<"${remote_commit_line}"
  remote_tree_sha="$(git_local rev-parse "${remote_head_sha}^{tree}")"
  stable_head_sha="$(git_authenticated ls-remote --heads origin "refs/heads/${head_ref}" | awk '{print $1}')"
  if [[ "${#remote_commit_parts[@]}" -ne 2 || "${remote_commit_parts[1]}" != "${verified_head_sha}" || "${remote_tree_sha}" != "${local_tree_sha}" || "${stable_head_sha}" != "${remote_head_sha}" ]]; then
    echo "::error title=Existing review branch mismatch::The remote ${head_ref} branch does not have the exact verified head and generated feedback tree. Refusing to recover publication." >&2
    exit 1
  fi
  verify_default_unchanged
  commit_sha="${remote_head_sha}"
  echo "Recovered exact previously pushed review-feedback commit ${commit_sha}."
else
  echo "::error title=PR branch moved::The remote ${head_ref} branch moved while recovery was being prepared." >&2
  exit 1
fi
persist_output pr_number "${pr_number}"
persist_output branch_name "${head_ref}"
persist_output commit_sha "${commit_sha}"

{
  echo "Codex pushed an update for review feedback from @${comment_author}."
  echo
  echo "Feedback: ${comment_url}"
  echo
  echo "Commit: ${commit_sha}"
  echo
  echo "Codex final message:"
  echo
  sed -n '1,200p' "${final_message_path}"
} >"${comment_body_path}"

if [[ -s "${verified_comment_path}" ]]; then
  printf '\n\n' >>"${comment_body_path}"
  cat "${verified_comment_path}" >>"${comment_body_path}"
fi

gh_authenticated pr comment "${pr_number}" --repo "${GITHUB_REPOSITORY}" --body-file "${comment_body_path}"
