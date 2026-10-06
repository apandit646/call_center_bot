"""a second calling provider: each company uses Twilio or Exotel

Revision ID: 0006_exotel_provider
Revises: 0005_inbound_calls
Create Date: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_exotel_provider"
down_revision: str | None = "0005_inbound_calls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = [
    # Existing companies keep working exactly as before: they stay on Twilio.
    sa.Column("telephony_provider", sa.String(16), nullable=False, server_default="twilio"),
    sa.Column("exotel_api_key", sa.String(128), nullable=True),
    sa.Column("exotel_api_token_enc", sa.Text(), nullable=True),
    sa.Column("exotel_account_sid", sa.String(64), nullable=True),
    sa.Column("exotel_subdomain", sa.String(64), nullable=False, server_default="api.in.exotel.com"),
    sa.Column("exotel_number", sa.String(32), nullable=True),
    sa.Column("exotel_app_id", sa.String(32), nullable=True),
    sa.Column("exotel_secret_enc", sa.Text(), nullable=True),
]


def upgrade() -> None:
    # 0001 builds tables from the *current* models, so a fresh database already has these columns.
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("companies")}
    for column in COLUMNS:
        if column.name not in existing:
            op.add_column("companies", column.copy())


def downgrade() -> None:
    for column in reversed(COLUMNS):
        op.drop_column("companies", column.name)
