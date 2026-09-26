import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import DocumentStatus
from app.schemas.common import ORMModel
from app.schemas.support import DomainBrief


class DocumentOut(ORMModel):
    id: uuid.UUID
    title: str
    domain: DomainBrief
    source_filename: str | None
    mime_type: str | None
    status: DocumentStatus
    error: str | None
    chunk_count: int
    created_at: datetime
    updated_at: datetime


class TextDocumentCreate(BaseModel):
    domain_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=10)


class SearchRequest(BaseModel):
    domain_id: uuid.UUID
    query: str = Field(min_length=2)
    top_k: int = Field(default=5, ge=1, le=20)


class SearchHit(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    content: str
    similarity: float
