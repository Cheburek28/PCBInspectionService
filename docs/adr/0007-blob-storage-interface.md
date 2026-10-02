# ADR-0007: Blob storage behind an interface

- Status: accepted
- Date: 2026-10-02

## Context

A single VPS is simplest with a Docker volume; larger installs prefer S3-compatible storage.

## Decision

`BlobStorage` protocol with `LocalStorage` (atomic write-then-rename) and `S3Storage`. Keys are validated
relative paths.

## Consequences

Switching storage is a configuration change. Both backends run the same contract tests.
