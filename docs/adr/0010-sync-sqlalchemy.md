# ADR-0010: Synchronous SQLAlchemy in API and worker

- Status: accepted
- Date: 2026-10-02

## Context

The original plan used async SQLAlchemy in the API. Request handlers mostly do file I/O, image decoding and
short queries; the worker is synchronous anyway.

## Decision

One synchronous data layer (SQLAlchemy 2, psycopg 3) for API, worker and CLI. FastAPI runs sync endpoints
in its thread pool.

## Consequences

Half the code paths, one set of repository functions and tests. If request concurrency grows beyond the thread
pool, revisit with async endpoints for long-polling only.
