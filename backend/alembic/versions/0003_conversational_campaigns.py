"""conversational campaigns: AI-driven natural conversation instead of a fixed script

Revision ID: 0003_conversational_campaigns
Revises: 0002_call_language_switching
Create Date: 2026-09-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_conversational_campaigns"
down_revision: str | None = "0002_call_language_switching"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 builds tables from the current models, so a fresh database already has the column.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("campaigns")}
    if "conversational" not in columns:
        op.add_column("campaigns", sa.Column("conversational", sa.Boolean(), nullable=False, server_default=sa.true()))
    if "agent_prompt" not in columns:
        op.add_column("campaigns", sa.Column("agent_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "agent_prompt")
    op.drop_column("campaigns", "conversational")
