import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import DB, AdminUser, ManagerUser, PageParams, not_found
from app.core.security import hash_password
from app.models import AgentProfile, User
from app.models.enums import UserRole
from app.schemas.common import Page
from app.schemas.identity import AgentProfileSchema, UserCreate, UserOut, UserUpdate
from app.services.audit import audit

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=Page[UserOut])
async def list_users(db: DB, _: ManagerUser, pg: PageParams, role: UserRole | None = None, q: str | None = None):
    stmt = select(User)
    if role:
        stmt = stmt.where(User.role == role)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(User.email.ilike(like) | User.full_name.ilike(like))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = (await db.scalars(stmt.order_by(User.created_at.desc()).offset(pg.offset).limit(pg.size))).all()
    return Page(items=items, total=total, page=pg.page, size=pg.size)


@router.get("/agents", response_model=list[UserOut])
async def list_agents(db: DB, _: ManagerUser):
    """Active users tickets can be assigned to."""
    return (
        await db.scalars(
            select(User)
            .where(User.is_active.is_(True), User.role.in_([UserRole.AGENT, UserRole.SUPERVISOR]))
            .order_by(User.full_name)
        )
    ).all()


@router.post("", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, db: DB, admin: AdminUser):
    email = body.email.lower()
    if await db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
    user = User(email=email, full_name=body.full_name, hashed_password=hash_password(body.password), role=body.role)
    db.add(user)
    await db.flush()
    if body.role == UserRole.AGENT or body.agent_profile:
        profile = body.agent_profile or AgentProfileSchema()
        db.add(AgentProfile(user_id=user.id, **profile.model_dump()))
    audit(db, admin.id, "create", "user", user.id, {"email": email, "role": body.role})
    await db.commit()
    return await db.get(User, user.id, populate_existing=True)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(user_id: uuid.UUID, body: UserUpdate, db: DB, admin: AdminUser):
    user = await db.get(User, user_id)
    if user is None:
        raise not_found("User")
    if user.id == admin.id and (body.is_active is False or (body.role and body.role != UserRole.ADMIN)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate or demote yourself")
    changes = body.model_dump(exclude_unset=True, exclude={"password", "agent_profile"})
    for field, value in changes.items():
        setattr(user, field, value)
    if body.password:
        user.hashed_password = hash_password(body.password)
    if body.agent_profile:
        if user.agent_profile:
            for field, value in body.agent_profile.model_dump().items():
                setattr(user.agent_profile, field, value)
        else:
            db.add(AgentProfile(user_id=user.id, **body.agent_profile.model_dump()))
    audit(db, admin.id, "update", "user", user.id, {k: str(v) for k, v in changes.items()})
    await db.commit()
    return await db.get(User, user.id, populate_existing=True)
