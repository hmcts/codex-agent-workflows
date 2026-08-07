# Caller contract

## Required dispatch inputs

The caller forwards Jira issue key, summary, description, status, assignee and URL. It also supplies its repository-scoped runner label, Sonar configuration, required status context and trusted publisher login.

## Required secrets

- `CODEX_OPENAI_API_KEY`
- `BOT_GITHUB_TOKEN`
- `CODEX_JIRA_PR_NOTIFY_URL`
- `CODEX_SONAR_TOKEN`

The publisher token must belong to `BOT_PUBLISHER_LOGIN` and be restricted to the caller repository. Secrets are unavailable to generated code and credential-free verification jobs.

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
