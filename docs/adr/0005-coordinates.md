# ADR-0005: Coordinates in uploaded pixels after EXIF orientation, both frames

- Status: accepted
- Date: 2026-10-02

## Context

Clients draw on the inspected photo; the dataset needs regions in both images; EXIF orientation is a
classic source of misplaced boxes.

## Decision

Each region has `bbox_test` and `bbox_ref` in integer pixels of the respective uploaded image after EXIF
orientation. Working-resolution coordinates never leave the engine. The transform reference→test is stored.

## Consequences

Manual regions can be mapped to the reference; crops of both images have identical geometry.
