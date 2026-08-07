# Codex agent workflows

Private reusable GitHub Actions workflows for HMCTS Codex implementation agents.

The repository centralises the trusted orchestration, scripts and structured-output schemas used by application repositories while keeping repository-specific instructions and verification adapters in each caller. Callers must pin reusable workflows to a full commit SHA and pass only the declared secrets.

## Workflows

- `codex-implement.yml`: Jira-triggered planning, implementation, repair, verification and PR publication.
- `codex-review-feedback.yml`: trusted `/codex-review` feedback and repair processing.

## Caller requirements

Each caller repository must provide the thin wrappers and verification adapter documented in [`docs/caller-contract.md`](docs/caller-contract.md). Model-facing jobs run on a repository-scoped ARC runner. Collection, verification and publication run in fresh trusted jobs using the immutable runtime action packaged with this repository.

The coordinated Juror configuration and activation sequence is documented in [`docs/juror-rollout.md`](docs/juror-rollout.md).

## Release policy

Changes to reusable workflows require CODEOWNERS review. A push to `main` runs `Update caller workflow pins`, which uses the restricted bot identity to raise full-SHA update PRs in every onboarded Juror repository. The workflow skips repositories that have not yet merged their caller wrappers.
