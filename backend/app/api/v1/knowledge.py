import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select

from app.api.deps import DB, ManagerUser, PageParams, TenantUser, owned
from app.models import Domain, KnowledgeDocument
from app.models.enums import DocumentStatus
from app.schemas.common import Page
from app.schemas.knowledge import DocumentOut, SearchHit, SearchRequest, TextDocumentCreate
from app.services import storage
from app.services.audit import audit
from app.services.document_processing import SUPPORTED_EXTENSIONS
from app.services.queue import Queues, enqueue
from app.services.rag import retrieve

# Each company has its own knowledge base: documents and their chunks carry the company id, and
# both this API and the bot's retrieval only ever read the caller's company.
router = APIRouter(prefix="/knowledge", tags=["knowledge base"])
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@router.get("/documents", response_model=Page[DocumentOut])
async def list_documents(db: DB, user: TenantUser, pg: PageParams, domain_id: uuid.UUID | None = None):
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.company_id == user.company_id)
    if domain_id:
        stmt = stmt.where(KnowledgeDocument.domain_id == domain_id)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = (await db.scalars(stmt.order_by(KnowledgeDocument.created_at.desc()).offset(pg.offset).limit(pg.size))).all()
    return Page(items=items, total=total, page=pg.page, size=pg.size)


@router.post("/documents/upload", response_model=DocumentOut, status_code=201)
async def upload_document(
    db: DB,
    user: ManagerUser,
    domain_id: uuid.UUID = Form(...),
    title: str | None = Form(None),
    file: UploadFile = File(...),
):
    await owned(db, Domain, domain_id, user, "Domain")
    filename = file.filename or "document"
    if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Supported types: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large (max 20 MB)")

    doc = KnowledgeDocument(
        company_id=user.company_id,
        domain_id=domain_id,
        title=title or Path(filename).stem,
        source_filename=filename,
        storage_path=storage.save_bytes("knowledge", filename, data),
        mime_type=file.content_type,
        uploaded_by_id=user.id,
    )
    db.add(doc)
    await db.flush()
    audit(db, user.id, "upload", "knowledge_document", doc.id, {"filename": filename})
    await db.commit()
    await enqueue(Queues.KNOWLEDGE_INGEST, {"document_id": str(doc.id)})
    return await db.get(KnowledgeDocument, doc.id, populate_existing=True)


@router.post("/documents/text", response_model=DocumentOut, status_code=201)
async def create_text_document(body: TextDocumentCreate, db: DB, user: ManagerUser):
    """Paste FAQ/policy text directly instead of uploading a file."""
    await owned(db, Domain, body.domain_id, user, "Domain")
    doc = KnowledgeDocument(
        company_id=user.company_id,
        domain_id=body.domain_id,
        title=body.title,
        source_filename=f"{body.title}.txt",
        storage_path=storage.save_bytes("knowledge", f"{body.title}.txt", body.content.encode()),
        mime_type="text/plain",
        uploaded_by_id=user.id,
    )
    db.add(doc)
    await db.commit()
    await enqueue(Queues.KNOWLEDGE_INGEST, {"document_id": str(doc.id)})
    return await db.get(KnowledgeDocument, doc.id, populate_existing=True)


@router.post("/documents/{document_id}/reprocess", response_model=DocumentOut)
async def reprocess_document(document_id: uuid.UUID, db: DB, user: ManagerUser):
    doc = await owned(db, KnowledgeDocument, document_id, user, "Document")
    doc.status = DocumentStatus.PENDING
    await db.commit()
    await enqueue(Queues.KNOWLEDGE_INGEST, {"document_id": str(doc.id)})
    # Re-read it: the update changed updated_at in the database, and the response includes it.
    return await db.get(KnowledgeDocument, doc.id, populate_existing=True)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: uuid.UUID, db: DB, user: ManagerUser):
    doc = await owned(db, KnowledgeDocument, document_id, user, "Document")
    path = doc.storage_path
    await db.delete(doc)  # chunks cascade
    audit(db, user.id, "delete", "knowledge_document", document_id, {"title": doc.title})
    await db.commit()
    storage.delete(path)


@router.post("/search", response_model=list[SearchHit])
async def test_search(body: SearchRequest, db: DB, user: TenantUser):
    """Try retrieval exactly as the WhatsApp bot would (company- and domain-filtered), without a similarity cut-off."""
    await owned(db, Domain, body.domain_id, user, "Domain")
    hits = await retrieve(db, body.query, user.company_id, body.domain_id, top_k=body.top_k, min_similarity=-1.0)
    return [SearchHit(**h.__dict__) for h in hits]
