"""SQLAlchemy ORM models. Any change here needs an Alembic migration (``make migration m=...``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from pcb_inspection.domain.ids import uuid7

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Json = dict[str, Any]


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {Json: JSONB, datetime: DateTime(timezone=True)}  # noqa: RUF012


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid7)


def _created() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = _pk()
    station: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    revoked_at: Mapped[datetime | None]


class InspectionSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _pk()
    api_key_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("api_keys.id"))
    product_code: Mapped[str] = mapped_column(String(100), index=True)
    product_name: Mapped[str | None] = mapped_column(String(200))
    operator: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="open")
    client_meta: Mapped[Json] = mapped_column(default=dict)
    created_at: Mapped[datetime] = _created()
    closed_at: Mapped[datetime | None]

    api_key: Mapped[ApiKey] = relationship(lazy="joined")


class Image(Base):
    __tablename__ = "images"

    id: Mapped[uuid.UUID] = _pk()
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    storage_key: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(50))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _created()


class Reference(Base):
    __tablename__ = "reference_images"
    __table_args__ = (
        Index(
            "uq_reference_images_active_side",
            "session_id",
            "side",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    side: Mapped[int] = mapped_column(SmallInteger)
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id"))
    mask_storage_key: Mapped[str] = mapped_column(String(300))
    mask_strategy: Mapped[str] = mapped_column(String(32))
    mask_polygon: Mapped[list[list[float]] | None] = mapped_column(JSONB)
    mask_coverage: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16))
    source_inspection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "inspections.id", use_alter=True, name="fk_reference_images_source_inspection_id_inspections"
        )
    )
    board_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("boards.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()

    image: Mapped[Image] = relationship(lazy="joined")


class Board(Base):
    __tablename__ = "boards"
    __table_args__ = (UniqueConstraint("session_id", "board_key", name="uq_boards_session_board_key"),)

    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"))
    board_key: Mapped[str] = mapped_column(String(100))
    barcode: Mapped[str | None] = mapped_column(String(100), index=True)
    serial: Mapped[str | None] = mapped_column(String(100))
    verdict: Mapped[str | None] = mapped_column(String(8))
    verdict_operator: Mapped[str | None] = mapped_column(String(100))
    verdict_comment: Mapped[str | None] = mapped_column(Text)
    verdict_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()


class Inspection(Base):
    __tablename__ = "inspections"
    __table_args__ = (
        UniqueConstraint("api_key_id", "idempotency_key", name="uq_inspections_api_key_idempotency_key"),
        Index("ix_inspections_board_side", "board_id", "side"),
    )

    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    board_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("boards.id"))
    side: Mapped[int] = mapped_column(SmallInteger)
    reference_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reference_images.id"))
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id"))
    api_key_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("api_keys.id"))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String(16))
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)
    rejection_code: Mapped[str | None] = mapped_column(String(32), index=True)
    rejection: Mapped[Json | None]
    quality: Mapped[Json | None]
    transform: Mapped[Json | None]
    aligned_storage_key: Mapped[str | None] = mapped_column(String(300))
    heatmap_storage_key: Mapped[str | None] = mapped_column(String(300))
    engine_name: Mapped[str] = mapped_column(String(50))
    engine_version: Mapped[str | None] = mapped_column(String(20))
    params: Mapped[Json] = mapped_column(default=dict)
    timings: Mapped[Json | None]
    error: Mapped[Json | None]
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    captured_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    board: Mapped[Board] = relationship(lazy="joined")
    image: Mapped[Image] = relationship(lazy="joined")
    reference: Mapped[Reference] = relationship(lazy="joined", foreign_keys=[reference_id])
    defects: Mapped[list[Defect]] = relationship(
        back_populates="inspection", order_by="Defect.rank", lazy="selectin", cascade="all, delete-orphan"
    )


class Defect(Base):
    __tablename__ = "defects"

    id: Mapped[uuid.UUID] = _pk()
    inspection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inspections.id"), index=True)
    source: Mapped[str] = mapped_column(String(8))
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float)
    area: Mapped[int | None] = mapped_column(Integer)
    test_x: Mapped[int] = mapped_column(Integer)
    test_y: Mapped[int] = mapped_column(Integer)
    test_w: Mapped[int] = mapped_column(Integer)
    test_h: Mapped[int] = mapped_column(Integer)
    ref_x: Mapped[int] = mapped_column(Integer)
    ref_y: Mapped[int] = mapped_column(Integer)
    ref_w: Mapped[int] = mapped_column(Integer)
    ref_h: Mapped[int] = mapped_column(Integer)
    verdict: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    defect_type: Mapped[str | None] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(Text)
    verdict_operator: Mapped[str | None] = mapped_column(String(100))
    verdict_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()

    inspection: Mapped[Inspection] = relationship(back_populates="defects")


class DefectEvent(Base):
    __tablename__ = "defect_events"

    id: Mapped[uuid.UUID] = _pk()
    defect_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("defects.id"), index=True)
    event: Mapped[str] = mapped_column(String(32))
    old: Mapped[Json | None]
    new: Mapped[Json | None]
    operator: Mapped[str | None] = mapped_column(String(100))
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("api_keys.id"))
    at: Mapped[datetime] = _created()
