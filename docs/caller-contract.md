# Caller contract

## Required dispatch inputs

The caller forwards Jira issue key, summary, description, status, assignee and URL. It also forwards the optional `initiatorDisplayName` supplied by Jira Automation, and supplies its repository-scoped runner label, Sonar configuration, required status context and the client ID of the HMCTS-owned Codex GitHub App.

The initiating display name is used only by trusted collection jobs to add traceability to the PR body. It is not included in model prompts. Missing or invalid values render as `Not supplied by Jira Automation`; callers must not substitute the assignee or reporter.

## Required secrets

- `CODEX_OPENAI_API_KEY`
- `CODEX_GITHUB_APP_PRIVATE_KEY`
- `CODEX_JIRA_PR_NOTIFY_URL`

`CODEX_SONAR_TOKEN` is optional. When configured, it enables the revision-specific Sonar API quality-gate check. When absent, the workflow records that the API check was skipped and continues to treat the repository's required Jenkins, Sonar and GitHub status checks as authoritative.

Each trusted publisher job mints a short-lived GitHub App installation token restricted to the caller repository. The App bot identity is verified and derived at runtime; no publisher PAT or stored login is required. Secrets are unavailable to generated code and credential-free verification jobs.

Callers must pass `CODEX_JIRA_PR_NOTIFY_URL` to both the implementation and PR-review reusable workflows. Terminal Jenkins, Sonar or credential-free verification failures return the PR to draft, attach the final evidence and notify Jira from a fresh trusted job.

The trusted plan-validation job retains the complete normalised `plan.json`, its SHA-256 file and the approved path list as the `codex-validated-plan` workflow artefact for 30 days. Planning or trusted validation failures attempt a terminal Jira callback containing the workflow run URL before implementation starts.

The same App may authenticate Azure Function workflow dispatches, provided it has `Actions: read and write` and is installed on every caller repository. Dispatch tokens are minted separately and restricted to the selected repository.

## Required repository files

Callers retain only repository-owned configuration and verification:

- `.github/workflows/codex_jira_dispatch.yml`
- `.github/workflows/codex_pr_review.yml`
- `bin/codex-local-pipeline.sh`
- `AGENTS.md`

Trusted planning, collection, repair, publication and security-validation scripts are loaded from this repository by `.github/actions/runtime` at an immutable commit SHA. Caller repositories must not copy or override that runtime.

The local pipeline must be credential-free and must not fetch or execute untrusted remote content. Existing branch-required Jenkins, Sonar, functional and smoke checks remain authoritative after publication.

## Publication behaviour

Passing verification produces a ready-for-review PR. When all available repair attempts fail, the latest structurally valid patch is published as a draft with the verification failure attached. Sensitive changes are allowed but must be highlighted in the PR body.
