# Security policy

Please **do not** open public issues for vulnerabilities. Report them privately via
[GitHub security advisories](https://github.com/Cheburek28/PCBInspectionService/security/advisories/new).
You will get an answer within a week.

Supported versions: the latest minor release.

Operational notes:
- Expose the service only through TLS (see `compose.prod.yaml` / Caddy).
- API keys are stored as SHA-256 hashes; the raw key is shown once. Revoke unused keys.
- `/metrics` is unauthenticated — keep it internal.
