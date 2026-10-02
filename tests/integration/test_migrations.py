from __future__ import annotations

import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from pcb_inspection.db.models import Base
from tests.integration.conftest import alembic_config


@pytest.fixture
def scratch_db(postgres_url: str) -> str:
    """A fresh empty database in the same server."""
    name = f"m_{uuid.uuid4().hex[:8]}"
    admin = create_engine(postgres_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return make_url(postgres_url).set(database=name).render_as_string(hide_password=False)


def test_upgrade_downgrade_upgrade(scratch_db: str) -> None:
    cfg = alembic_config(scratch_db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    engine = create_engine(scratch_db)
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    command.upgrade(cfg, "head")
    assert {"sessions", "inspections", "defects", "defect_events"} <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_models_match_migrations(migrated_url: str) -> None:
    engine = create_engine(migrated_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == [], "models changed without a migration: run `make migration m=...`"


def test_only_one_active_reference_per_side(migrated_url: str) -> None:
    engine = create_engine(migrated_url)
    sid, key, img = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO api_keys (id, station, prefix, key_hash) VALUES (:k, 's', :p, 'h')"),
            {"k": key, "p": uuid.uuid4().hex[:12]},
        )
        conn.execute(
            text(
                "INSERT INTO sessions (id, api_key_id, product_code, status, client_meta) "
                "VALUES (:s, :k, 'p', 'open', '{}')"
            ),
            {"s": sid, "k": key},
        )
        conn.execute(
            text(
                "INSERT INTO images (id, sha256, storage_key, content_type, width, height, bytes) "
                "VALUES (:i, :h, 'k', 'image/jpeg', 1, 1, 1)"
            ),
            {"i": img, "h": uuid.uuid4().hex},
        )
    insert = text(
        "INSERT INTO reference_images (id, session_id, side, image_id, mask_storage_key, mask_strategy, "
        "mask_coverage, source, is_active) VALUES (:id, :s, 1, :i, 'm', 'full_frame', 1, 'upload', true)"
    )
    with engine.begin() as conn:
        conn.execute(insert, {"id": uuid.uuid4(), "s": sid, "i": img})
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert, {"id": uuid.uuid4(), "s": sid, "i": img})
    engine.dispose()
