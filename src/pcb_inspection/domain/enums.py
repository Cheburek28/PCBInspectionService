from __future__ import annotations

from enum import StrEnum


class SessionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class ReferenceSource(StrEnum):
    UPLOAD = "upload"
    INSPECTION = "inspection"


class InspectionStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"

    @property
    def is_final(self) -> bool:
        return self in FINAL_STATUSES


FINAL_STATUSES = frozenset({InspectionStatus.COMPLETED, InspectionStatus.REJECTED, InspectionStatus.FAILED})


class DefectSource(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


class Detector(StrEnum):
    """What produced an automatic region."""

    DIFF = "diff"  # difference map against the reference
    SOLDER = "solder"  # missing solder fillet
    SHIFT = "shift"  # part body moved relative to its neighbours
    SPECK = "speck"  # speck, drop or crumb on a flat area
    HAIR = "hair"  # hair or fibre


class Verdict(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class BoardVerdict(StrEnum):
    PASS = "pass"  # noqa: S105 - not a password
    FAIL = "fail"


class DefectType(StrEnum):
    MISSING_COMPONENT = "missing_component"
    WRONG_COMPONENT = "wrong_component"
    MISPLACED_COMPONENT = "misplaced_component"
    SOLDER_BRIDGE = "solder_bridge"
    INSUFFICIENT_SOLDER = "insufficient_solder"
    TOMBSTONE = "tombstone"
    FOREIGN_OBJECT = "foreign_object"
    DAMAGE = "damage"
    CONTAMINATION = "contamination"
    OTHER = "other"


class RejectionCode(StrEnum):
    IMAGE_UNREADABLE = "IMAGE_UNREADABLE"
    IMAGE_TOO_SMALL = "IMAGE_TOO_SMALL"
    ALIGNMENT_FAILED = "ALIGNMENT_FAILED"
    IMAGE_BLURRY = "IMAGE_BLURRY"
    LIGHTING_MISMATCH = "LIGHTING_MISMATCH"
    TOO_MANY_DIFFERENCES = "TOO_MANY_DIFFERENCES"


class Stage(StrEnum):
    DECODE = "decode"
    ANALYZE = "analyze"
    SAVE = "save"
