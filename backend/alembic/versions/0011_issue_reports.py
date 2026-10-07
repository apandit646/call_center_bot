"""issue reports: employees report to their company admin, company admins to the platform admin

Revision ID: 0011_issue_reports
Revises: 0010_campaign_runs
Create Date: 2026-10-07
"""
from collections.abc import Sequence

from alembic import op

from app.models import IssueComment, IssueReport

revision: str = "0011_issue_reports"
down_revision: str | None = "0010_campaign_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = [IssueReport.__table__, IssueComment.__table__]


def upgrade() -> None:
    # 0001 builds tables from the *current* models, so a fresh database already has these.
    for table in TABLES:
        table.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    for table in reversed(TABLES):
        table.drop(op.get_bind(), checkfirst=True)
