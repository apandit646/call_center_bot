"""billing: price plans, usage, invoices (Stripe)

Revision ID: 0008_billing
Revises: 0007_gaps_callbacks_email
Create Date: 2026-10-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models import BillingPlan, Invoice, InvoiceLine, UsageRecord

revision: str = "0008_billing"
down_revision: str | None = "0007_gaps_callbacks_email"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# In dependency order: usage and lines point at invoices.
TABLES = [BillingPlan.__table__, Invoice.__table__, InvoiceLine.__table__, UsageRecord.__table__]


def upgrade() -> None:
    # 0001 builds tables from the *current* models, so a fresh database already has all of this.
    # No company has a plan after this, so nobody is billed until the platform admin sets prices.
    bind = op.get_bind()
    for table in TABLES:
        table.create(bind, checkfirst=True)
    if "billing_hold" not in {c["name"] for c in sa.inspect(bind).get_columns("companies")}:
        op.add_column("companies", sa.Column("billing_hold", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("companies", "billing_hold")
    for table in reversed(TABLES):
        table.drop(op.get_bind(), checkfirst=True)
