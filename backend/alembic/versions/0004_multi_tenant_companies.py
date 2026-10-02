"""multi-tenant: companies, company_id on tenant tables, campaign offering fields

Revision ID: 0004_multi_tenant_companies
Revises: 0003_conversational_campaigns
Create Date: 2026-10-02

Existing single-tenant data is moved into one "Default Company", so nothing is lost. The first
admin is promoted to platform super admin on the next API start (see app/seed.py).
"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0004_multi_tenant_companies"
down_revision: str | None = "0003_conversational_campaigns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# users.company_id stays nullable: platform super admins belong to no company.
TENANT_TABLES = [
    "customers", "domains", "conversations", "tickets", "knowledge_documents", "knowledge_chunks", "campaigns",
]
DEFAULT_COMPANY_NAME = "Default Company"


def _insp() -> sa.Inspector:
    # A fresh inspector each time: it caches, and this migration changes what it would see.
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _insp().get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # 0001 builds tables from the *current* models, so a fresh database already has all of this;
    # every step below only runs where the database is still on the old shape.
    if "companies" not in _insp().get_table_names():
        op.create_table(
            "companies",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("email", sa.String(255)),
            sa.Column("phone", sa.String(32)),
            sa.Column("website", sa.String(255)),
            sa.Column("address", sa.Text()),
            sa.Column("industry", sa.String(128)),
            sa.Column("description", sa.Text()),
            sa.Column("products_services", sa.Text()),
            sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Kolkata"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("max_campaigns", sa.Integer()),
            sa.Column("daily_call_limit", sa.Integer()),
            sa.Column("max_concurrent_calls", sa.Integer()),
            sa.Column("twilio_account_sid", sa.String(64)),
            sa.Column("twilio_auth_token_enc", sa.Text()),
            sa.Column("twilio_from_number", sa.String(32)),
            sa.Column("whatsapp_phone_number_id", sa.String(64), unique=True),
            sa.Column("whatsapp_access_token_enc", sa.Text()),
            sa.Column("whatsapp_app_secret_enc", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    legacy = [t for t in ["users", *TENANT_TABLES] if not _has_column(t, "company_id")]
    for table in legacy:
        op.add_column(table, sa.Column("company_id", UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(f"{table}_company_id_fkey", table, "companies", ["company_id"], ["id"])
        op.create_index(f"ix_{table}_company_id", table, ["company_id"])

    if legacy:
        if any(bind.scalar(sa.text(f"SELECT 1 FROM {t} LIMIT 1")) for t in legacy):  # noqa: S608 - fixed names
            company_id = uuid.uuid4()
            bind.execute(
                sa.text("INSERT INTO companies (id, name) VALUES (:id, :name)"),
                {"id": company_id, "name": DEFAULT_COMPANY_NAME},
            )
            for table in legacy:
                bind.execute(sa.text(f"UPDATE {table} SET company_id = :id"), {"id": company_id})  # noqa: S608
        for table in legacy:
            if table != "users":
                op.alter_column(table, "company_id", nullable=False)

    # A phone number / domain code is now unique per company, not across the whole platform.
    phone_index = next((i for i in _insp().get_indexes("customers") if i["name"] == "ix_customers_phone"), None)
    if phone_index and phone_index.get("unique"):
        op.drop_index("ix_customers_phone", table_name="customers")
        op.create_index("ix_customers_phone", "customers", ["phone"])
    if "uq_customers_company_phone" not in {u["name"] for u in _insp().get_unique_constraints("customers")}:
        op.create_unique_constraint("uq_customers_company_phone", "customers", ["company_id", "phone"])

    for constraint in _insp().get_unique_constraints("domains"):
        if constraint["column_names"] == ["code"]:
            op.drop_constraint(constraint["name"], "domains", type_="unique")
    if "uq_domains_company_code" not in {u["name"] for u in _insp().get_unique_constraints("domains")}:
        op.create_unique_constraint("uq_domains_company_code", "domains", ["company_id", "code"])

    if not _has_column("campaigns", "offering_type"):
        op.add_column("campaigns", sa.Column("offering_type", sa.String(16), nullable=False, server_default="general"))
    if not _has_column("campaigns", "offering_name"):
        op.add_column("campaigns", sa.Column("offering_name", sa.String(255), nullable=True))
    if not _has_column("campaigns", "offering_details"):
        op.add_column("campaigns", sa.Column("offering_details", sa.Text(), nullable=True))


def downgrade() -> None:
    # Only safe while a single company exists: per-company duplicates would break the old unique rules.
    op.drop_column("campaigns", "offering_details")
    op.drop_column("campaigns", "offering_name")
    op.drop_column("campaigns", "offering_type")
    op.drop_constraint("uq_domains_company_code", "domains", type_="unique")
    op.create_unique_constraint("domains_code_key", "domains", ["code"])
    op.drop_constraint("uq_customers_company_phone", "customers", type_="unique")
    op.drop_index("ix_customers_phone", table_name="customers")
    op.create_index("ix_customers_phone", "customers", ["phone"], unique=True)
    # The old version has no platform role: the platform admin goes back to being a plain admin.
    op.execute("UPDATE users SET role = 'admin' WHERE role = 'super_admin'")
    for table in ["users", *TENANT_TABLES]:
        op.drop_index(f"ix_{table}_company_id", table_name=table)
        op.drop_column(table, "company_id")
    op.drop_table("companies")
