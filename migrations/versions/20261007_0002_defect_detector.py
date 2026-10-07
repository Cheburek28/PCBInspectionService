"""defect detector: what produced an automatic region

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-07 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("defects", sa.Column("detector", sa.String(length=16), nullable=True))
    # every automatic region so far came from the difference map
    op.execute("UPDATE defects SET detector = 'diff' WHERE source = 'auto'")


def downgrade() -> None:
    op.drop_column("defects", "detector")
