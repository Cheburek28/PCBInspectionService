# ADR-0008: References belong to a session

- Status: accepted
- Date: 2026-10-02

## Context

On the shop floor the first good board of a batch becomes the reference; revisions differ between batches.

## Decision

One active reference per side and session (partial unique index); older references are kept. Any inspected
photo can be promoted without re-upload.

## Consequences

A product-level reference library and multi-reference pools are future extensions.
