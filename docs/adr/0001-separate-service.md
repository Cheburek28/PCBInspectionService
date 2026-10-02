# ADR-0001: Inspection as a separate, client-agnostic service

- Status: accepted
- Date: 2026-10-02

## Context

The algorithm was prototyped inside a production tool. Production clients run on Windows workstations,
the analysis needs CPU and a growing dataset needs central storage.

## Decision

A standalone HTTP service with its own repository. It knows nothing about any client application,
product database or fixture: product codes are opaque labels, fixture-specific logic is an optional mask strategy.

## Consequences

Any client can use it; the algorithm is versioned and tested independently; clients must handle an
unavailable service (decision support only).
