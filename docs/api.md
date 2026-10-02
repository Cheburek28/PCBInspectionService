# API guide (v1)

The machine-readable contract is [`openapi.json`](../openapi.json); interactive docs at `/docs` on a running
service. This guide explains how to use the API correctly.

## Basics

- Base path: `/api/v1`. Authentication: `Authorization: Bearer <api key>` (keys are created with
  `pcbis apikey create --station <name>`; one key per client station).
- IDs are UUIDv7 strings. Timestamps are ISO 8601 UTC.
- Errors are `application/problem+json` ([RFC 9457](https://www.rfc-editor.org/rfc/rfc9457)) with a stable
  `code` — see [errors.md](errors.md). Every response carries `X-Request-ID` (send your own to correlate logs).
- Uploads: JPEG or PNG, detected by content (not file name), up to `PCBIS_MAX_UPLOAD_MB` (25 MB by default).

## Concepts

| Concept | Meaning |
|---|---|
| Session | A batch of identical boards inspected against shared references. `product_code` is an opaque label |
| Side | Integer 1..8; each side has its own reference |
| Reference | Photo of a known-good board side. Exactly one *active* reference per side and session |
| Board | One physical board, identified by your `board_key` (e.g. a UUID), optional `barcode` / `serial` |
| Inspection | One photo of one side of one board. A newer photo of the same board side *supersedes* the older one |
| Defect | A region: `auto` (found by the engine) or `manual` (marked by the operator), with a verdict |

## Coordinates

All boxes are `{x, y, w, h}` in integer pixels of the **uploaded image after EXIF orientation is applied**,
origin top-left. `bbox_test` is on the inspected photo, `bbox_ref` on the reference photo. Every inspection
returns `image.width/height` so you can scale to your display.

If your client decodes images without applying EXIF orientation (e.g. Qt's `QImage::loadFromData`), either
apply it (`QImageReader::setAutoTransform(true)`) or re-encode upright pixels before uploading.

## Flow

### 1. Session

```http
POST /api/v1/sessions
{"product_code": "demo-board", "product_name": "Demo", "operator": "alice", "client_meta": {"app": "my-client"}}
→ 201 {"id": "…", "status": "open", "active_references": {}, …}
```

### 2. Reference per side

```http
POST /api/v1/sessions/{session_id}/references          (multipart)
  side=1
  image=@reference.jpg
  board={"board_key": "…", "barcode": "…"}            (optional)
  mask_strategy=full_frame | blue_fixture | polygon   (optional, server default)
  mask_polygon=[[x,y], …]                             (for polygon, uploaded-image pixels)
→ 201 {"id": "…", "side": 1, "is_active": true, "mask": {"coverage": 0.97, "url": "…/mask.png"}, …}
```

Processing is synchronous (≈1 s). A new reference for the same side deactivates the previous one.
To promote a photo you already inspected (no re-upload):

```http
POST /api/v1/sessions/{session_id}/references/from-inspection
{"from_inspection_id": "…"}
```

The mask strategy of the previous reference is kept; a polygon is carried over through the alignment transform.

### 3. Inspection

```http
POST /api/v1/sessions/{session_id}/inspections          (multipart)
Idempotency-Key: 5f0c…                                 (required, ≤ 100 chars, unique per request)
  side=1
  image=@photo.jpg
  board={"board_key": "board-0001", "barcode": "4006381333931"}
  reference_id=…                                       (optional; default: active reference of the side)
  params={"threshold": 14}                              (optional overrides, see algorithm.md)
  captured_at=2026-10-02T09:20:00Z                      (optional)
→ 202 {"id": "…", "status": "queued", "poll_url": "/api/v1/inspections/…"}   + Location header
```

**Idempotency**: retrying the same request with the same key returns the original inspection and does not
queue a second job. Reusing a key for a *different* request → `409 IDEMPOTENCY_CONFLICT`. Generate a fresh
key per photo, keep it across network retries of that photo.

### 4. Result (long-poll)

```http
GET /api/v1/inspections/{id}?wait=20
```

The server answers as soon as the status is final or after `wait` seconds (max 30 by default). Loop until
`status` is `completed`, `rejected` or `failed`.

| status | meaning | client action |
|---|---|---|
| `queued`, `processing` (`stage`: decode / analyze / save) | in progress | keep polling |
| `completed` | `defects` holds the differences (possibly empty) | show them |
| `rejected` | photo unusable, `rejection.code` says why; `defects` may still be present for debugging | ask to retake |
| `failed` | internal error, `error.code` | retry the photo or continue manually |

Example (shortened):

```json
{
  "id": "…", "status": "completed", "side": 1, "superseded": false,
  "board": {"board_key": "board-0001", "barcode": "4006381333931", "serial": null},
  "image": {"width": 3000, "height": 2000, "url": "/api/v1/inspections/…/image?kind=original"},
  "quality": {"alignment_inliers": 662, "sharpness_ratio": 1.02, "lab_shift": [-2.4, 0.3, -0.3],
              "differences_count": 1, "differences_area_ratio": 0.0002, "alignment_ok": true, "same_as_reference": false},
  "rejection": null,
  "defects": [{
    "id": "…", "source": "auto", "rank": 1, "score": 57.8, "area": 944,
    "bbox_test": {"x": 2541, "y": 1003, "w": 22, "h": 61},
    "bbox_ref":  {"x": 2549, "y": 999,  "w": 21, "h": 60},
    "verdict": "pending", "defect_type": null,
    "crop_url": "/api/v1/inspections/…/defects/…/crop?kind=pair"
  }],
  "algorithm": {"name": "classic-diff", "version": "0.1.0", "params": {"engine": {…}, "gates": {…}}},
  "timings_ms": {"queue": 140, "align": 1450, "diff": 420, "total": 2190}
}
```

### 5. Images and crops

- `GET /inspections/{id}/image?kind=original|aligned|heatmap` — `aligned` is the photo warped into the
  reference frame (working resolution), `heatmap` the difference map over it.
- `GET /inspections/{id}/defects/{defect_id}/crop?kind=pair|ref|test&pad=60&height=400` — reference and
  aligned test crops with identical geometry; `pair` puts the reference on the left. `pad` is in
  uploaded-reference pixels. Responses are cacheable.

### 6. Operator feedback

```http
PUT /api/v1/defects/{defect_id}/verdict
{"verdict": "accepted", "defect_type": "missing_component", "comment": "C12", "operator": "alice"}
```

`verdict`: `accepted` (real defect), `rejected` (false alarm) or `pending` (undo). It can be changed any
time; every change is kept in the history. `PUT` replaces `defect_type` and `comment`.

```http
POST /api/v1/inspections/{inspection_id}/defects
{"bbox_test": {"x": 1200, "y": 640, "w": 80, "h": 50}, "defect_type": "solder_bridge", "operator": "alice"}
→ 201 defect with source=manual, verdict=accepted and bbox_ref computed by the service
```

Allowed when the inspection is `completed` or `rejected` and was aligned (`409 NO_TRANSFORM` otherwise).

```http
PUT /api/v1/sessions/{session_id}/boards/{board_key}/verdict
{"verdict": "fail", "operator": "alice", "comment": "C12 missing", "serial": "00042"}
GET /api/v1/sessions/{session_id}/boards/{board_key}     → latest inspection per side
```

`defect_type` values: `missing_component`, `wrong_component`, `misplaced_component`, `solder_bridge`,
`insufficient_solder`, `tombstone`, `foreign_object`, `damage`, `contamination`, `other`.

### 7. Health and metrics

- `GET /health/live` — process is up. `GET /health/ready` — database, storage and queue reachable (503 otherwise).
- `GET /metrics` — Prometheus. Not authenticated: keep it off the public internet (the production Caddyfile blocks it).

## Client checklist

- Reuse one HTTP connection; set timeouts ≥ `wait` + 10 s for polling.
- Retry uploads on network errors / 5xx with the **same** `Idempotency-Key`; exponential backoff.
- Treat the service as advisory: if it is unreachable, let the operator continue manually.
- Queue verdict calls locally when offline and resend later — they are idempotent (`PUT`).
- Downscaling photos to the working width (3000 px by default) before upload saves bandwidth without
  changing results.
