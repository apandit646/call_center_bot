import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, or_, select

from app.api.deps import DB, CurrentUser, PageParams, not_found
from app.models import Customer
from app.schemas.common import Page
from app.schemas.support import CustomerCreate, CustomerOut, CustomerUpdate
from app.services.phone import normalize_phone

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=Page[CustomerOut])
async def list_customers(db: DB, _: CurrentUser, pg: PageParams, q: str | None = None):
    stmt = select(Customer)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Customer.name.ilike(like), Customer.phone.ilike(like), Customer.email.ilike(like)))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = (await db.scalars(stmt.order_by(Customer.created_at.desc()).offset(pg.offset).limit(pg.size))).all()
    return Page(items=items, total=total, page=pg.page, size=pg.size)


@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(customer_id: uuid.UUID, db: DB, _: CurrentUser):
    customer = await db.get(Customer, customer_id)
    if customer is None:
        raise not_found("Customer")
    return customer


@router.post("", response_model=CustomerOut, status_code=201)
async def create_customer(body: CustomerCreate, db: DB, _: CurrentUser):
    phone = normalize_phone(body.phone)
    if phone is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid phone number")
    if await db.scalar(select(Customer.id).where(Customer.phone == phone)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A customer with this phone already exists")
    customer = Customer(**body.model_dump(exclude={"phone"}), phone=phone)
    db.add(customer)
    await db.commit()
    return customer


@router.patch("/{customer_id}", response_model=CustomerOut)
async def update_customer(customer_id: uuid.UUID, body: CustomerUpdate, db: DB, _: CurrentUser):
    customer = await db.get(Customer, customer_id)
    if customer is None:
        raise not_found("Customer")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(customer, field, value)
    await db.commit()
    return customer
