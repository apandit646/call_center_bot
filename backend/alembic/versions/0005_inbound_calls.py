"""inbound support calls: support_calls table, per-company inbound settings, one owner per calling number

Revision ID: 0005_inbound_calls
Revises: 0004_multi_tenant_companies
Create Date: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0005_inbound_calls"
down_revision: str | None = "0004_multi_tenant_companies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUMBER_UNIQUE = "companies_twilio_from_number_key"


def _insp() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _insp().get_columns(table)}


def upgrade() -> None:
    # 0001 builds tables from the *current* models, so a fresh database already has all of this.
    if not _has_column("companies", "inbound_allowed"):
        op.add_column("companies", sa.Column("inbound_allowed", sa.Boolean(), nullable=False, server_default=sa.true()))
    if not _has_column("companies", "inbound_enabled"):
        op.add_column("companies", sa.Column("inbound_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    if not _has_column("companies", "inbound_language"):
        op.add_column("companies", sa.Column("inbound_language", sa.String(16), nullable=False, server_default="en-IN"))
    if not _has_column("companies", "inbound_greeting"):
        op.add_column("companies", sa.Column("inbound_greeting", sa.Text(), nullable=True))

    # An inbound call finds its company by the number dialled, so a calling number has one owner.
    if not any(u["column_names"] == ["twilio_from_number"] for u in _insp().get_unique_constraints("companies")):
        op.create_unique_constraint(NUMBER_UNIQUE, "companies", ["twilio_from_number"])

    if "support_calls" not in _insp().get_table_names():
        op.create_table(
            "support_calls",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
            sa.Column("customer_id", UUID(as_uuid=True), sa.ForeignKey("customers.id"), nullable=False),
            sa.Column(
                "conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"),
                nullable=False, unique=True,
            ),
            sa.Column("provider", sa.String(32), nullable=False),
            sa.Column("provider_call_id", sa.String(128), unique=True),
            sa.Column("from_number", sa.String(32)),
            sa.Column("to_number", sa.String(32)),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("language", sa.String(16)),
            sa.Column("outcome", sa.String(32)),
            sa.Column("ticket_id", UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="SET NULL")),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("ended_at", sa.DateTime(timezone=True)),
            sa.Column("duration_seconds", sa.Integer()),
            sa.Column("recording_url", sa.String(1024)),
            sa.Column("recording_path", sa.String(512)),
            sa.Column("state", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_support_calls_company_id", "support_calls", ["company_id"])
        op.create_index("ix_support_calls_customer_id", "support_calls", ["customer_id"])
        op.create_index("ix_support_calls_status", "support_calls", ["status"])


def downgrade() -> None:
    op.drop_table("support_calls")
    op.drop_constraint(NUMBER_UNIQUE, "companies", type_="unique")
    for column in ("inbound_greeting", "inbound_language", "inbound_enabled", "inbound_allowed"):
        op.drop_column("companies", column)
