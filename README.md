# Codex agent workflows

Private reusable GitHub Actions workflows for HMCTS Codex implementation agents.

The repository centralises the trusted orchestration used by application repositories while keeping repository-specific instructions and verification adapters in each caller. Callers must pin reusable workflows to a full commit SHA and pass only the declared secrets.

## Workflows

- `codex-implement.yml`: Jira-triggered planning, implementation, repair, verification and PR publication.
- `codex-review-feedback.yml`: trusted `/codex-review` feedback and repair processing.

## Caller requirements

Each caller repository must provide the scripts and schemas documented in [`docs/caller-contract.md`](docs/caller-contract.md). Model-facing jobs run on a repository-scoped ARC runner. Collection, verification and publication run in fresh trusted jobs.

## Release policy

Changes to reusable workflows require CODEOWNERS review. Consumers adopt releases through pull requests that update the full commit SHA in their local wrapper workflows.
