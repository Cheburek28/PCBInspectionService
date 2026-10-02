# ADR-0002: Asynchronous processing with 202 + long-polling

- Status: accepted
- Date: 2026-10-02

## Context

Analysing one side takes 2–10 s depending on hardware; client networks are unreliable.

## Decision

`POST /inspections` stores the photo, enqueues a job and returns `202` immediately. Clients poll
`GET /inspections/{id}?wait=N`; the server answers as soon as the job is final. WebSockets/SSE and webhooks were
rejected: harder for desktop clients, webhooks do not reach clients behind NAT.

## Consequences

Simple clients, no long-held upload requests. Long-polling occupies an API thread while waiting; it polls
the database every 250 ms, which is negligible at expected volumes (switch to Redis pub/sub if it is not).
