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

Callers retain the trusted scripts and schemas consumed by the reusable workflow:

- `.github/schemas/codex-plan-result.schema.json`
- `.github/schemas/codex-patch-result.schema.json`
- `.github/scripts/codex-*.sh`
- `.github/scripts/collect-codex-patch-result.py`
- `.github/scripts/validate-codex-plan.py`
- `.github/scripts/codex-verify-publisher.py`
- `.github/scripts/notify-jira-automation.py`
- `bin/codex-local-pipeline.sh`

The local pipeline must be credential-free and must not fetch or execute untrusted remote content. Existing branch-required Jenkins, Sonar, functional and smoke checks remain authoritative after publication.

## Publication behaviour

Passing verification produces a ready-for-review PR. When all available repair attempts fail, the latest structurally valid patch is published as a draft with the verification failure attached. Sensitive changes are allowed but must be highlighted in the PR body.
