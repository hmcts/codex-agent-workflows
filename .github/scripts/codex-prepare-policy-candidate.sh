#!/usr/bin/env bash

set -euo pipefail

for name in CANDIDATE_ROOT EXPECTED_CANDIDATE_SHA PATCH_PATH; do
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required environment variable: ${name}" >&2
    exit 1
  fi
done

if [[ -n "${TRUSTED_REPOSITORY_ROOT:-}" || -n "${EXPECTED_TRUSTED_SHA:-}" ]]; then
  for name in TRUSTED_REPOSITORY_ROOT EXPECTED_TRUSTED_SHA; do
    if [[ -z "${!name:-}" ]]; then
      echo "Missing required environment variable: ${name}" >&2
      exit 1
    fi
  done
fi

if [[ ! "${EXPECTED_CANDIDATE_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Invalid immutable candidate base SHA: ${EXPECTED_CANDIDATE_SHA}" >&2
  exit 1
fi
if [[ -n "${EXPECTED_TRUSTED_SHA:-}" && ! "${EXPECTED_TRUSTED_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Invalid immutable trusted SHA: ${EXPECTED_TRUSTED_SHA}" >&2
  exit 1
fi
if [[ ! -d "${CANDIDATE_ROOT}/.git" || -L "${CANDIDATE_ROOT}" ]]; then
  echo "Candidate policy checkout is missing or symbolic: ${CANDIDATE_ROOT}" >&2
  exit 1
fi
if [[ ! -s "${PATCH_PATH}" || -L "${PATCH_PATH}" ]]; then
  echo "Candidate policy patch is missing, empty or symbolic: ${PATCH_PATH}" >&2
  exit 1
fi

git_candidate() {
  env -i \
    "HOME=${RUNNER_TEMP:-/tmp}" \
    "PATH=${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}" \
    "LANG=${LANG:-C.UTF-8}" \
    "LC_ALL=${LC_ALL:-${LANG:-C.UTF-8}}" \
    git \
      -c core.hooksPath=/dev/null \
      -c core.fsmonitor=false \
      -c credential.helper= \
      -c protocol.file.allow=never \
      -C "${CANDIDATE_ROOT}" \
      "$@"
}

actual_sha="$(git_candidate rev-parse HEAD)"
if [[ "${actual_sha}" != "${EXPECTED_CANDIDATE_SHA}" ]]; then
  echo "Candidate policy checkout ${actual_sha} does not match ${EXPECTED_CANDIDATE_SHA}." >&2
  exit 1
fi

if [[ -n "${TRUSTED_REPOSITORY_ROOT:-}" ]]; then
  if [[ -L "${TRUSTED_REPOSITORY_ROOT}" ]]; then
    echo "Trusted policy destination is symbolic: ${TRUSTED_REPOSITORY_ROOT}" >&2
    exit 1
  fi
  if [[ -e "${TRUSTED_REPOSITORY_ROOT}" ]]; then
    if [[ ! -d "${TRUSTED_REPOSITORY_ROOT}" || -n "$(find "${TRUSTED_REPOSITORY_ROOT}" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
      echo "Trusted policy destination must be an empty real directory: ${TRUSTED_REPOSITORY_ROOT}" >&2
      exit 1
    fi
  else
    mkdir -p "${TRUSTED_REPOSITORY_ROOT}"
  fi
  if ! git_candidate cat-file -e "${EXPECTED_TRUSTED_SHA}^{commit}"; then
    echo "Immutable trusted commit is unavailable: ${EXPECTED_TRUSTED_SHA}" >&2
    exit 1
  fi
  if ! git_candidate archive --format=tar "${EXPECTED_TRUSTED_SHA}" .github/workflows |
    tar -xf - -C "${TRUSTED_REPOSITORY_ROOT}"; then
    echo "Unable to materialize immutable trusted workflows at ${EXPECTED_TRUSTED_SHA}." >&2
    exit 1
  fi
fi

git_candidate apply --index --binary "${PATCH_PATH}"
