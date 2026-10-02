# ADR-0009: Pluggable engines, versioned results

- Status: accepted
- Date: 2026-10-02

## Context

A learned model will eventually replace or complement the classic algorithm.

## Decision

Engines implement the `Engine` protocol (prepare_reference, inspect) and are registered by name. Every
inspection stores engine name, version and the effective parameters.

## Consequences

Results remain reproducible and comparable across versions; the dataset can be filtered by engine.
