"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-26

Creates the pgvector extension and every table from the SQLAlchemy models as the baseline.
Later schema changes should be generated with `alembic revision --autogenerate -m "..."`.
"""
from collections.abc import Sequence

from alembic import op
from app.models import Base

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
