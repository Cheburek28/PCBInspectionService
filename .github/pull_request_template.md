## What and why

<!-- One or two sentences. Link the issue: Closes #123 -->

## Checklist

- [ ] `make check` passes locally
- [ ] Tests cover the acceptance criteria of the issue
- [ ] API changed → `make openapi`, `docs/api.md` updated
- [ ] Database changed → Alembic migration added (`make migration m=...`)
- [ ] User-visible change → conventional commit title (`feat:`, `fix:` …) so it lands in the changelog
- [ ] No real board photos, customer names or secrets added
