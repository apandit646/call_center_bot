import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DB, ManagerUser, TenantUser, owned
from app.models import Domain
from app.schemas.support import DomainCreate, DomainOut, DomainUpdate
from app.services.audit import audit

router = APIRouter(prefix="/domains", tags=["domains"])


@router.get("", response_model=list[DomainOut])
async def list_domains(db: DB, user: TenantUser, include_inactive: bool = False):
    stmt = select(Domain).where(Domain.company_id == user.company_id).order_by(Domain.name)
    if not include_inactive:
        stmt = stmt.where(Domain.is_active.is_(True))
    return (await db.scalars(stmt)).all()


@router.post("", response_model=DomainOut, status_code=201)
async def create_domain(body: DomainCreate, db: DB, user: ManagerUser):
    if await db.scalar(select(Domain.id).where(Domain.company_id == user.company_id, Domain.code == body.code)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Domain code already exists")
    domain = Domain(**body.model_dump(), company_id=user.company_id)
    db.add(domain)
    await db.flush()
    audit(db, user.id, "create", "domain", domain.id, {"code": body.code})
    await db.commit()
    return domain


@router.patch("/{domain_id}", response_model=DomainOut)
async def update_domain(domain_id: uuid.UUID, body: DomainUpdate, db: DB, user: ManagerUser):
    domain = await owned(db, Domain, domain_id, user, "Domain")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(domain, field, value)
    audit(db, user.id, "update", "domain", domain.id, body.model_dump(exclude_unset=True))
    await db.commit()
    return domain
