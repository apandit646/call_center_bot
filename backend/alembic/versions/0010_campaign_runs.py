"""campaign runs: a campaign can be used again and again

Every existing campaign gets a Run 1 that owns all of its contacts (and, through them, its calls
and answers), so nothing changes for campaigns that are never run again.

Revision ID: 0010_campaign_runs
Revises: 0009_outbound_callback_time
Create Date: 2026-10-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models import CampaignRun

revision: str = "0010_campaign_runs"
down_revision: str | None = "0009_outbound_callback_time"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_UNIQUE = "uq_campaign_contacts_run_customer"


def _columns(inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    # 0001 builds tables from the *current* models, so on a fresh database only the data steps
    # (which then find no campaigns) have anything to do.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    CampaignRun.__table__.create(bind, checkfirst=True)

    if "current_run_id" not in _columns(inspector, "campaigns"):
        op.add_column("campaigns", sa.Column("current_run_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.add_column("campaigns", sa.Column("run_number", sa.Integer(), nullable=False, server_default="1"))
    if "is_active" not in _columns(inspector, "campaign_questions"):
        op.add_column("campaign_questions", sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"))
    if "run_id" not in _columns(inspector, "campaign_contacts"):
        op.add_column("campaign_contacts", sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            "fk_campaign_contacts_run", "campaign_contacts", "campaign_runs", ["run_id"], ["id"], ondelete="CASCADE"
        )
        op.create_index("ix_campaign_contacts_run_id", "campaign_contacts", ["run_id"])

    # Run 1 for every campaign that has none: it takes over the campaign's status and dates.
    op.execute("""
        INSERT INTO campaign_runs (id, campaign_id, number, source, status, started_at, completed_at, questions,
                                   skip_completed, skipped_dnc, skipped_completed, created_at, updated_at)
        SELECT gen_random_uuid(), c.id, 1, 'first', NULL, c.start_at, c.completed_at, '[]', true, 0, 0, c.created_at, now()
        FROM campaigns c
        WHERE NOT EXISTS (SELECT 1 FROM campaign_runs r WHERE r.campaign_id = c.id)
    """)
    op.execute("""
        UPDATE campaigns c SET current_run_id = r.id, run_number = r.number
        FROM campaign_runs r
        WHERE r.campaign_id = c.id AND c.current_run_id IS NULL
          AND r.number = (SELECT max(number) FROM campaign_runs WHERE campaign_id = c.id)
    """)
    op.execute("""
        UPDATE campaign_contacts cc SET run_id = c.current_run_id
        FROM campaigns c WHERE cc.campaign_id = c.id AND cc.run_id IS NULL
    """)

    # One contact per customer per RUN (it used to be per campaign).
    uniques = inspector.get_unique_constraints("campaign_contacts")
    for unique in uniques:
        if set(unique["column_names"]) == {"campaign_id", "customer_id"}:
            op.drop_constraint(unique["name"], "campaign_contacts", type_="unique")
    if NEW_UNIQUE not in {u["name"] for u in uniques}:
        op.create_unique_constraint(NEW_UNIQUE, "campaign_contacts", ["run_id", "customer_id"])


def downgrade() -> None:
    # Back to "a campaign is used once": only each campaign's latest run can be kept, because a
    # customer may appear in several runs. Earlier runs' contacts (and their calls) are removed.
    op.execute("""
        DELETE FROM campaign_contacts cc USING campaigns c
        WHERE cc.campaign_id = c.id AND cc.run_id IS NOT NULL AND cc.run_id <> c.current_run_id
    """)
    op.execute("DELETE FROM campaign_questions WHERE NOT is_active")
    # Dropping the column also drops its index, its foreign key and the per-run unique rule.
    op.execute("ALTER TABLE campaign_contacts DROP COLUMN run_id CASCADE")
    op.create_unique_constraint("campaign_contacts_campaign_id_customer_id_key", "campaign_contacts", ["campaign_id", "customer_id"])
    op.drop_column("campaign_questions", "is_active")
    op.drop_column("campaigns", "run_number")
    op.drop_column("campaigns", "current_run_id")
    op.drop_table("campaign_runs")
