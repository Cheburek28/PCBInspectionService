# ADR-0003: Celery with Redis for jobs

- Status: accepted
- Date: 2026-10-02

## Context

Jobs are CPU-bound (OpenCV) and must survive worker crashes, be time-limited and retried on
infrastructure errors.

## Decision

Celery 5 with a Redis broker: prefork processes, `acks_late`, hard/soft time limits, `max_tasks_per_child`.
Tasks are thin wrappers around `services.pipeline`. A PostgreSQL `SKIP LOCKED` queue was considered: one component
fewer, but time limits, retries and process recycling would have to be written by hand.

## Consequences

Redis is a required component. The queue sits behind the `TaskQueue` protocol, so tests run without a broker.
