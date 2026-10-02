# Feedback dataset

Every operator decision is stored so that the algorithm can be evaluated and a model trained later.

## What is stored

| Table | Content |
|---|---|
| `images` | every uploaded photo (deduplicated by SHA-256); files under `images/<sha[:2]>/<sha>.<ext>` |
| `reference_images` | references, their mask (`references/<id>/mask.png`) and where they came from |
| `inspections` | photo, reference, status, quality metrics, rejection, alignment transform, engine name/version, effective parameters, timings |
| `defects` | regions in both coordinate systems, score, verdict, defect type, comment, operator |
| `defect_events` | full history of every verdict change |
| `boards` | final board verdict (`pass` / `fail`) |

Aligned photos (`inspections/<id>/aligned.jpg`) are stored in the reference frame, so reference and
aligned test can be compared pixel by pixel.

## Labels

| Region | Meaning for training / evaluation |
|---|---|
| `auto` + `accepted` | true positive |
| `auto` + `rejected` | false positive — a valuable hard negative |
| `auto` + `pending` | not reviewed — exclude from metrics |
| `manual` | false negative — the engine missed it |
| board `pass` with no accepted regions | a fully clean example |

## Export

```bash
pcbis export --out ./dataset [--product demo-board] [--since 2026-10-01]
# in Docker: docker compose exec api pcbis export --out /data/exports/2026-10
```

Output:

```
dataset/
  annotations.json        COCO: images (inspected photos), annotations (bbox = bbox_test), categories
  metrics.json            {"tp", "fp", "fn", "pending", "precision", "recall"}
  images/test/<inspection_id>.jpg
  images/ref/<reference_id>.jpg
  images/aligned/<inspection_id>.jpg
```

Each COCO image has extra fields: `reference_file`, `aligned_file`, `side`, `product_code`, `board_key`,
`board_verdict`, `status`, `rejection_code`, `engine`. Each annotation has `attributes` with `source`,
`verdict`, `score`, `bbox_ref` and `comment`. Category = `defect_type` (or `defect` when not set).

Only the latest (non-superseded) inspection of each board side is exported, and only inspections that were
aligned with their reference.
