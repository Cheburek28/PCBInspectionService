"""Dataset export in COCO format plus algorithm metrics from operator feedback.

Semantics (see docs/dataset.md):
  auto + accepted  -> true positive
  auto + rejected  -> false positive (hard negative)
  manual           -> false negative (missed by the algorithm)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from pcb_inspection.db.models import Board, Inspection, InspectionSession
from pcb_inspection.domain.enums import DefectSource, DefectType, InspectionStatus, Verdict
from pcb_inspection.services.context import ServiceContext

CATEGORIES = [{"id": 1, "name": "defect"}] + [
    {"id": i, "name": t.value, "supercategory": "defect"} for i, t in enumerate(DefectType, start=2)
]
_CATEGORY_ID = {c["name"]: c["id"] for c in CATEGORIES}


@dataclass(frozen=True, slots=True)
class ExportSummary:
    images: int
    annotations: int
    metrics: dict[str, Any]


def export_coco(
    ctx: ServiceContext,
    out_dir: Path,
    product_code: str | None = None,
    since: datetime | None = None,
    include_aligned: bool = True,
) -> ExportSummary:
    out_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("test", "ref", "aligned"):
        (out_dir / "images" / sub).mkdir(parents=True, exist_ok=True)
    query = (
        select(Inspection, InspectionSession, Board)
        .join(InspectionSession, Inspection.session_id == InspectionSession.id)
        .join(Board, Inspection.board_id == Board.id)
        .where(
            ~Inspection.superseded,
            Inspection.status.in_([InspectionStatus.COMPLETED, InspectionStatus.REJECTED]),
            Inspection.transform.is_not(None),
        )
        .order_by(Inspection.created_at)
    )
    if product_code:
        query = query.where(InspectionSession.product_code == product_code)
    if since:
        query = query.where(Inspection.created_at >= since)

    coco_images: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    counts = {"tp": 0, "fp": 0, "fn": 0, "pending": 0}
    with ctx.db.session() as s:
        rows = s.execute(query).unique().all()
        for image_id, (insp, session, board) in enumerate(rows, start=1):
            test_name = f"images/test/{insp.id}.{_ext(insp.image.content_type)}"
            ref_name = f"images/ref/{insp.reference_id}.{_ext(insp.reference.image.content_type)}"
            _copy(ctx, insp.image.storage_key, out_dir / test_name)
            _copy(ctx, insp.reference.image.storage_key, out_dir / ref_name)
            aligned_name = None
            if include_aligned and insp.aligned_storage_key:
                aligned_name = f"images/aligned/{insp.id}.jpg"
                _copy(ctx, insp.aligned_storage_key, out_dir / aligned_name)
            coco_images.append(
                {
                    "id": image_id,
                    "file_name": test_name,
                    "width": insp.image.width,
                    "height": insp.image.height,
                    "inspection_id": str(insp.id),
                    "reference_file": ref_name,
                    "aligned_file": aligned_name,
                    "side": insp.side,
                    "product_code": session.product_code,
                    "board_key": board.board_key,
                    "board_verdict": board.verdict,
                    "status": insp.status,
                    "rejection_code": insp.rejection_code,
                    "engine": f"{insp.engine_name}@{insp.engine_version}",
                }
            )
            for d in insp.defects:
                _count(counts, d.source, d.verdict)
                annotations.append(
                    {
                        "id": len(annotations) + 1,
                        "image_id": image_id,
                        "category_id": _CATEGORY_ID.get(d.defect_type or "defect", 1),
                        "bbox": [d.test_x, d.test_y, d.test_w, d.test_h],
                        "area": d.test_w * d.test_h,
                        "iscrowd": 0,
                        "attributes": {
                            "defect_id": str(d.id),
                            "source": d.source,
                            "verdict": d.verdict,
                            "score": d.score,
                            "bbox_ref": [d.ref_x, d.ref_y, d.ref_w, d.ref_h],
                            "comment": d.comment,
                        },
                    }
                )
    coco = {
        "info": {"description": "PCB Inspection Service export", "date_created": datetime.now().isoformat()},
        "images": coco_images,
        "annotations": annotations,
        "categories": CATEGORIES,
    }
    (out_dir / "annotations.json").write_text(json.dumps(coco, indent=1))
    metrics = _metrics(counts)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1))
    return ExportSummary(images=len(coco_images), annotations=len(annotations), metrics=metrics)


def _count(counts: dict[str, int], source: str, verdict: str) -> None:
    if source == DefectSource.MANUAL:
        counts["fn"] += 1
    elif verdict == Verdict.ACCEPTED:
        counts["tp"] += 1
    elif verdict == Verdict.REJECTED:
        counts["fp"] += 1
    else:
        counts["pending"] += 1


def _metrics(c: dict[str, int]) -> dict[str, Any]:
    tp, fp, fn = c["tp"], c["fp"], c["fn"]
    return {
        **c,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
    }


def _ext(content_type: str) -> str:
    return "png" if content_type == "image/png" else "jpg"


def _copy(ctx: ServiceContext, key: str, dest: Path) -> None:
    if not dest.exists():
        dest.write_bytes(ctx.storage.get(key))
