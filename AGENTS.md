# AGENTS.md

Instructions for anyone changing this code — AI coding agents and humans alike. `CLAUDE.md` is a symlink to this file.

PCB Inspection Service compares a photo of a circuit board with a reference photo of the same board side,
returns the differing regions, rejects unusable photos, and stores operator feedback as a training dataset.

## Commands

```bash
make install        # uv sync + pre-commit hooks
make check          # EVERYTHING CI checks (lint, mypy --strict, import contracts, tests + 90% coverage gate)
make test-unit      # fast (~10 s), no Docker
make test-int       # PostgreSQL/Redis via testcontainers — needs Docker
make openapi        # regenerate openapi.json after any API change
make migration m="describe change"   # Alembic autogenerate; review the file, never edit applied revisions
make up && make smoke                # full stack in Docker + end-to-end check
uv run pcbis bench ref.jpg a.jpg b.jpg   # run the engine on local photos, no database
```

`make check` must pass before you consider a task done. CI runs the same targets plus the Docker smoke test.

## Map

```
src/pcb_inspection/
  engine/     PURE algorithm: numpy in → dataclasses out. No I/O, no DB, no settings import.
    base.py       Engine protocol, EngineResult, InspectParams, MaskSpec, QualityMetrics
    geometry.py   BBox, projective transforms between uploaded/working/reference/test frames
    imaging.py    decode (EXIF applied!), encode, resize
    classic/      classic-diff: align.py, mask.py, quality.py, diff.py, engine.py
    crops.py      side-by-side difference crops
  domain/     enums, quality gates (gates.py), errors (errors.py → HTTP status/code), uuid7
  db/         SQLAlchemy models + Database (session = unit of work)
  storage/    BlobStorage protocol: local filesystem, S3
  queue/      TaskQueue protocol: Celery (prod), Inline/Recording (tests)
  services/   use cases — the only layer combining db + storage + queue + engine
    pipeline.py   worker side of an inspection (claim → analyze → gates → save)
  api/        FastAPI: schemas.py is the public contract; routers are thin
  worker/     Celery app and the single thin task
  cli/        `pcbis` command
  synthetic.py  deterministic synthetic boards with ground truth (tests, demos, smoke)
migrations/   Alembic
tests/unit, tests/integration, scripts/smoke.py
```

## Rules

1. **Layers** (enforced by import-linter, see `pyproject.toml`):
   `cli → api | worker → services → db | storage | queue → domain → engine`. Do not bypass with local imports.
2. **Engine stays pure.** If you need configuration inside the engine, add a field to `InspectParams`.
3. **Public API change** ⇒ `make openapi`, update `docs/api.md` (and `docs/errors.md` for new codes),
   add a `feat:`/`fix:` commit. Breaking changes need a new API version (`/api/v2`), CI runs `oasdiff`.
4. **Database change** ⇒ edit `db/models.py`, `make migration m=...`, review the generated file,
   cover it in integration tests. `tests/integration/test_migrations.py` fails if models and migrations diverge.
5. **Coordinates**: always pixels of the *uploaded* image *after EXIF orientation*. `bbox_test` is on the
   inspected photo, `bbox_ref` on the reference photo. Working-resolution coordinates never leave the engine.
6. **Errors**: raise a `domain.errors.AppError` subclass; never return ad-hoc error JSON.
7. **Determinism**: tests use `pcb_inspection.synthetic` with fixed seeds. No network, no sleeps except
   in long-poll tests. Never commit real board photos, customer names or secrets.
8. **Typing**: `mypy --strict` must pass. OpenCV stubs are loose — wrap results in `np.asarray(..., dtype)`
   rather than adding `type: ignore`.
9. Keep modules small and single-purpose; prefer a new module over growing a 400-line file.
10. Decisions live in `docs/adr/`. Do not silently reverse one — write a new ADR.

## Definition of done

- Acceptance criteria of the issue are expressed as tests and pass.
- `make check` is green; `make smoke` passes if the change touches API, worker or Docker.
- Docs updated (api.md / errors.md / configuration.md / algorithm.md as relevant).
- Commit messages follow Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).

## Gotchas

- `TestClient` + `InlineQueue` runs the pipeline inside the POST request: the `202` body may already say
  `completed`. Use the `queued_api` fixture to test queued states.
- Celery tasks must stay thin; logic belongs in `services/pipeline.py` where it is tested without a broker.
- The reference cache (`ServiceContext.ref_cache`) is per process. Workers rebuild prepared references from
  stored image + mask; masks are never recomputed after a reference is created.
- `blue_fixture` masks are specific to fixtures with blue slots around the board; `green_board` finds a green
  board in slots of any other colour. The default is `full_frame`.
