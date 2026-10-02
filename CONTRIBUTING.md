# Contributing

Thanks for helping! Issues and pull requests are welcome.

## Setup

Requirements: [uv](https://docs.astral.sh/uv/), Docker (for integration tests and the dev stack).

```bash
make install     # dependencies + pre-commit hooks
make check       # what CI runs
make up && make smoke
```

## Workflow

1. Open or pick an issue with clear acceptance criteria (template: *Task*).
2. Branch from `main`, keep the change focused.
3. Write tests for the acceptance criteria first; synthetic boards (`pcb_inspection.synthetic`) give exact
   ground truth — never add real board photos.
4. `make check` must pass. API changes: `make openapi`; schema changes: `make migration m="..."`.
5. Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:` …) — releases
   and the changelog are generated from them.

Read [AGENTS.md](AGENTS.md) for the code map and rules; it applies to humans too.

## Licensing

Contributions are accepted under the Apache-2.0 license of the project.
