"""campaign calls: the time a customer asked to be called back at

Revision ID: 0009_outbound_callback_time
Revises: 0008_billing
Create Date: 2026-10-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_outbound_callback_time"
down_revision: str | None = "0008_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = [
    sa.Column("callback_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("callback_count", sa.Integer(), nullable=False, server_default="0"),
]


def upgrade() -> None:
    # 0001 builds tables from the *current* models, so a fresh database already has these columns.
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("campaign_contacts")}
    for column in COLUMNS:
        if column.name not in existing:
            op.add_column("campaign_contacts", column.copy())


def downgrade() -> None:
    for column in reversed(COLUMNS):
        op.drop_column("campaign_contacts", column.name)
