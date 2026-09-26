"""Excel/CSV contact import: parse -> validate -> dedupe -> normalise phones -> preview -> confirm."""
import csv
import io
import re
import uuid
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CampaignContact, ContactImport, Customer
from app.models.enums import ImportStatus
from app.services.phone import normalize_phone

MAX_ROWS = 50_000
_INSERT_BATCH = 2_000
PHONE_ALIASES = {"phone", "mobile", "phone_number", "mobile_number", "contact", "number"}
NAME_ALIASES = {"name", "full_name", "customer_name"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _norm_header(h: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(h or "").strip().lower()).strip("_")


def read_rows(filename: str, data: bytes) -> list[dict[str, Any]]:
    ext = Path(filename).suffix.lower()
    if ext in {".xlsx", ".xlsm"}:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        rows = list(wb.active.iter_rows(values_only=True))
        wb.close()
    elif ext == ".csv":
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig", errors="replace"))))
    else:
        raise ValueError("Upload an .xlsx or .csv file")
    if not rows:
        raise ValueError("The file is empty")

    headers = [_norm_header(h) for h in rows[0]]
    if len(rows) - 1 > MAX_ROWS:
        raise ValueError(f"Too many rows (max {MAX_ROWS})")
    out = []
    for values in rows[1:]:
        if values is None or all(v in (None, "") for v in values):
            continue
        out.append({h: v for h, v in zip(headers, values, strict=False) if h})
    return out


async def build_preview(
    db: AsyncSession, campaign_id: uuid.UUID, filename: str, data: bytes
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Returns (valid, invalid, duplicate_count). Duplicates are counted but not listed as invalid."""
    rows = read_rows(filename, data)
    if rows and not (PHONE_ALIASES & rows[0].keys()):
        raise ValueError("Missing required 'phone' column")

    existing_phones = set(
        (
            await db.scalars(
                select(Customer.phone)
                .join(CampaignContact, CampaignContact.customer_id == Customer.id)
                .where(CampaignContact.campaign_id == campaign_id)
            )
        ).all()
    )

    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates = 0
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        raw_phone = next((row[k] for k in PHONE_ALIASES if row.get(k) not in (None, "")), None)
        name = next((str(row[k]).strip() for k in NAME_ALIASES if row.get(k)), None)
        email = str(row["email"]).strip() if row.get("email") else None
        errors = []
        phone = normalize_phone(raw_phone)
        if raw_phone in (None, ""):
            errors.append("phone is required")
        elif phone is None:
            errors.append(f"invalid phone number '{raw_phone}'")
        if email and not EMAIL_RE.match(email):
            errors.append(f"invalid email '{email}'")
        if errors:
            invalid.append({"row": i, "data": _jsonable(row), "errors": errors})
            continue
        if phone in seen or phone in existing_phones:
            duplicates += 1
            continue
        seen.add(phone)
        reserved = PHONE_ALIASES | NAME_ALIASES | {"email", "city"}
        valid.append(
            {
                "row": i,
                "name": name,
                "phone": phone,
                "email": email,
                "city": str(row["city"]).strip() if row.get("city") else None,
                "variables": {k: v for k, v in _jsonable(row).items() if k not in reserved},
            }
        )
    return valid, invalid, duplicates


async def confirm_import(db: AsyncSession, imp: ContactImport) -> int:
    """Upsert customers and attach them to the campaign. Returns number of contacts added."""
    if imp.status != ImportStatus.PREVIEW:
        raise ValueError("Import already processed")
    rows = imp.valid_data
    phones = [r["phone"] for r in rows]
    existing: dict[str, Customer] = {}
    for start in range(0, len(phones), _INSERT_BATCH):
        batch = phones[start : start + _INSERT_BATCH]
        existing.update({c.phone: c for c in (await db.scalars(select(Customer).where(Customer.phone.in_(batch)))).all()})

    for r in rows:
        customer = existing.get(r["phone"])
        if customer is None:
            customer = Customer(phone=r["phone"], name=r["name"], email=r["email"], city=r["city"])
            db.add(customer)
            existing[r["phone"]] = customer
        else:  # fill gaps, don't overwrite curated data
            customer.name = customer.name or r["name"]
            customer.email = customer.email or r["email"]
            customer.city = customer.city or r["city"]
    await db.flush()

    added = 0
    for start in range(0, len(rows), _INSERT_BATCH):  # stay under PG's bind-parameter limit
        stmt = (
            insert(CampaignContact)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "campaign_id": imp.campaign_id,
                        "customer_id": existing[r["phone"]].id,
                        "variables": r["variables"],
                    }
                    for r in rows[start : start + _INSERT_BATCH]
                ]
            )
            .on_conflict_do_nothing(index_elements=["campaign_id", "customer_id"])
            .returning(CampaignContact.id)
        )
        added += len((await db.execute(stmt)).all())
    imp.status = ImportStatus.CONFIRMED
    await db.commit()
    return added


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        if v is None or isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    return out
