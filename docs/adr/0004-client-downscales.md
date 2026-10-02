# ADR-0004: Clients may downscale photos to the working width

- Status: accepted
- Date: 2026-10-02

## Context

Camera originals are ~12 MB (6000×4000); the engine works at 3000 px.

## Decision

The API accepts any size; documentation recommends uploading ~3000 px wide JPEGs. Coordinates are always
in the pixels of the uploaded image, so clients that downscale map boxes back with a simple factor.

## Consequences

5–7× less traffic. Full-resolution originals, if needed for training, stay with the client.
