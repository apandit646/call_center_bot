"""call language switching: alternate languages and translations

Revision ID: 0002_call_language_switching
Revises: 0001_initial
Create Date: 2026-09-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_call_language_switching"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    # 0001 builds tables from the *current* models, so a fresh database already has these columns.
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_column("campaigns", "alt_languages"):
        op.add_column("campaigns", sa.Column("alt_languages", sa.JSON(), nullable=False, server_default='["hi-IN"]'))
    if not _has_column("campaigns", "translations"):
        op.add_column("campaigns", sa.Column("translations", sa.JSON(), nullable=False, server_default="{}"))
    if not _has_column("campaign_questions", "translations"):
        op.add_column("campaign_questions", sa.Column("translations", sa.JSON(), nullable=False, server_default="{}"))
    if not _has_column("calls", "language"):
        op.add_column("calls", sa.Column("language", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "language")
    op.drop_column("campaign_questions", "translations")
    op.drop_column("campaigns", "translations")
    op.drop_column("campaigns", "alt_languages")
