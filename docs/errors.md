# Errors and rejection codes

## HTTP errors (`application/problem+json`)

```json
{"type": "https://github.com/Cheburek28/PCBInspectionService/blob/main/docs/errors.md#no_active_reference",
 "title": "No active reference for this side", "status": 409, "code": "NO_ACTIVE_REFERENCE",
 "detail": "session … has no active reference for side 2", "request_id": "4f1c…"}
```

Branch on `code`, never on `title`/`detail` (human text, may change).

### unauthorized
`401` — missing, malformed or revoked API key. Send `Authorization: Bearer pcbis_<prefix>_<secret>`.

### not_found
`404` — unknown session, reference, inspection, defect or board, or an unknown route.

### session_closed
`409` — the session was closed; open a new one.

### no_active_reference
`409` — no reference uploaded for this side yet. Upload one or pass `reference_id`.

### idempotency_conflict
`409` — the `Idempotency-Key` was already used for a different request. Use a new key per photo.

### invalid_state
`409` — e.g. adding a manual defect while the inspection is still queued.

### no_transform
`409` — the photo was not aligned with the reference (`ALIGNMENT_FAILED`), so a region cannot be mapped.

### conflict
`409` — a parallel request changed the same resource at the same moment. Safe to retry the request
(with the same `Idempotency-Key` for submissions).

### payload_too_large
`413` — upload exceeds `PCBIS_MAX_UPLOAD_MB`.

### unsupported_media_type
`415` — only JPEG and PNG (detected by content).

### image_unreadable
`422` — the file looks like JPEG/PNG but cannot be decoded.

### image_too_small
`422` — reference narrower than `PCBIS_GATE_MIN_WIDTH`.

### board_mask_not_found
`422` — the inspected area could not be found on the reference (e.g. `blue_fixture` without blue slots,
`green_board` without green solder mask or without slots around the board,
or a polygon covering < 3 % of the image).

### validation_error
`422` — malformed request; `errors` lists the fields.

### rate_limited
`429` — reserved for rate limiting.

### not_ready
`503` from `/health/ready` — `checks` shows which dependency is down.

### internal_error
`500` — unexpected; the server log has the traceback under the same `request_id`.

## Rejection codes (`inspection.rejection.code`, status `rejected`)

The photo was processed but cannot be judged. Gates are applied in this order; the first failing one wins.
Thresholds are configurable (see [configuration.md](configuration.md)) and can be overridden per request.

| Code | Condition (defaults) | Typical cause / what the operator should do |
|---|---|---|
| `IMAGE_TOO_SMALL` | width < 1500 px | client downscales too much |
| `ALIGNMENT_FAILED` | < 50 RANSAC inliers, or implausible transform (scale outside 0.8–1.25, mirrored, strong perspective) | other board, other side, board moved in the fixture |
| `IMAGE_BLURRY` | sharpness / reference sharpness < 0.90, measured where photo and reference agree | refocus, retake |
| `LIGHTING_MISMATCH` | \|ΔL\| > 8 or \|Δa\|, \|Δb\| > 5 (Lab, 8-bit scale) | lighting changed, retake |
| `TOO_MANY_DIFFERENCES` | > 60 regions or > 2 % of the inspected area | dirty board, other revision, outdated reference |

## Failure codes (`inspection.error.code`, status `failed`)

| Code | Meaning |
|---|---|
| `ANALYSIS_ERROR` | deterministic failure (stored image unreadable, engine error) — retrying the same photo will not help |
| `INTERNAL_ERROR` | infrastructure failed after all retries (database, storage) |
| `TIMEOUT` | analysis exceeded `PCBIS_TASK_TIME_LIMIT_S` |
