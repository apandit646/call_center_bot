"""scheduled campaign calls remember whether they are a call-back the customer asked for

Revision ID: 0012_scheduled_call_kind
Revises: 0011_issue_reports
Create Date: 2026-10-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_scheduled_call_kind"
down_revision: str | None = "0011_issue_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 builds tables from the *current* models, so a fresh database already has the column.
    bind = op.get_bind()
    if "kind" not in {c["name"] for c in sa.inspect(bind).get_columns("scheduled_calls")}:
        op.add_column("scheduled_calls", sa.Column("kind", sa.String(16), nullable=False, server_default="attempt"))
        # Call-backs already waiting when this ships: the contact carries the time the customer chose.
        op.execute("""
            UPDATE scheduled_calls sc SET kind = 'callback_time'
            FROM campaign_contacts cc
            WHERE cc.id = sc.campaign_contact_id AND cc.callback_at IS NOT NULL
              AND sc.status IN ('pending', 'queued') AND sc.scheduled_at = cc.callback_at
        """)


def downgrade() -> None:
    op.drop_column("scheduled_calls", "kind")
