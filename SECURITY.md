# Security

Report suspected vulnerabilities through the HMCTS security reporting process rather than a public issue.

The reusable workflows preserve these boundaries:

- Codex runs as an unprivileged user behind the official credential proxy.
- The Codex Action is the final step in every model-facing job.
- Structured output is collected in a fresh trusted job.
- Repository code runs only in credential-free verification jobs.
- Publishing uses a dedicated repository-restricted machine identity.
- Caller workflows pin this repository to a full commit SHA.
