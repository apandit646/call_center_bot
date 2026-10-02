from typing import Any

from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CompanyOwned, Timestamps, UUIDPk


class Customer(UUIDPk, CompanyOwned, Timestamps, Base):
    __tablename__ = "customers"
    # The same person can be a customer of several companies; each company has its own record.
    __table_args__ = (UniqueConstraint("company_id", "phone", name="uq_customers_company_phone"),)

    name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(32), index=True)  # E.164
    email: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(128))
    whatsapp_id: Mapped[str | None] = mapped_column(String(64), index=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
