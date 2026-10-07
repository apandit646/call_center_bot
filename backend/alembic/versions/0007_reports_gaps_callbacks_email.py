"""unanswered questions, callbacks, and email (SendGrid) as a support channel

Revision ID: 0007_gaps_callbacks_email
Revises: 0006_exotel_provider
Create Date: 2026-10-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models import Callback, KnowledgeGap

revision: str = "0007_gaps_callbacks_email"
down_revision: str | None = "0006_exotel_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COMPANY_COLUMNS = [
    # Calls keep following the caller between English and Hindi until a company switches more languages on.
    sa.Column("inbound_languages", sa.JSON(), nullable=False, server_default="[]"),
    # Email is off for every existing company until it connects its own SendGrid account and switches it on.
    sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default="false"),
    sa.Column("email_address", sa.String(255), nullable=True),
    sa.Column("email_from_name", sa.String(128), nullable=True),
    sa.Column("sendgrid_api_key_enc", sa.Text(), nullable=True),
    sa.Column("email_webhook_token", sa.String(64), nullable=True),
]
NEW_TABLES = [KnowledgeGap.__table__, Callback.__table__]


def upgrade() -> None:
    # 0001 builds tables from the *current* models, so a fresh database already has all of this.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in NEW_TABLES:
        table.create(bind, checkfirst=True)

    existing = {c["name"] for c in inspector.get_columns("companies")}
    for column in COMPANY_COLUMNS:
        if column.name not in existing:
            op.add_column("companies", column.copy())
    uniques = {u["name"] for u in inspector.get_unique_constraints("companies")} | {i["name"] for i in inspector.get_indexes("companies")}
    for name, column in (("uq_companies_email_address", "email_address"), ("uq_companies_email_webhook_token", "email_webhook_token")):
        covered = any(column in (u.get("column_names") or []) for u in inspector.get_unique_constraints("companies"))
        if name not in uniques and not covered:
            op.create_unique_constraint(name, "companies", [column])

    # Someone who has only written by email has no phone number yet.
    phone = next(c for c in inspector.get_columns("customers") if c["name"] == "phone")
    if not phone["nullable"]:
        op.alter_column("customers", "phone", existing_type=sa.String(32), nullable=True)
    if "ix_customers_email" not in {i["name"] for i in inspector.get_indexes("customers")}:
        op.create_index("ix_customers_email", "customers", ["email"])


def downgrade() -> None:
    op.drop_index("ix_customers_email", table_name="customers")
    # customers.phone stays optional: email-only customers may exist by now, and they must not be lost.
    for name in ("uq_companies_email_webhook_token", "uq_companies_email_address"):
        op.execute(f'ALTER TABLE companies DROP CONSTRAINT IF EXISTS "{name}"')
    for column in reversed(COMPANY_COLUMNS):
        op.drop_column("companies", column.name)
    for table in reversed(NEW_TABLES):
        table.drop(op.get_bind(), checkfirst=True)
