# ADR-0006: The service advises, a human decides

- Status: accepted
- Date: 2026-10-02

## Context

The engine is not calibrated for every product; false positives are expected.

## Decision

The service never sets a board verdict on its own. Clients send the operator's decisions; the engine's
output is a suggestion.

## Consequences

Clients must keep working when the service is down. Every operator decision becomes training data.
