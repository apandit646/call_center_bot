"""Knowledge document ingestion: extract text -> chunk -> embed -> store in pgvector."""
import io
import logging
import re
import uuid
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import KnowledgeChunk, KnowledgeDocument
from app.models.enums import DocumentStatus
from app.services import storage
from app.services.gemini import get_gemini

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


def extract_text(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    if ext == ".docx":
        import docx

        document = docx.Document(io.BytesIO(data))
        return "\n\n".join(p.text for p in document.paragraphs)
    if ext in {".txt", ".md"}:
        return data.decode("utf-8", errors="replace")
    raise ValueError(f"Unsupported file type: {ext}")


def chunk_text(text: str, size: int | None = None, overlap: int | None = None) -> list[str]:
    """Paragraph-aware chunking with character overlap between consecutive chunks."""
    size = size or settings.CHUNK_SIZE_CHARS
    overlap = settings.CHUNK_OVERLAP_CHARS if overlap is None else overlap
    text = re.sub(r"[ \t]+", " ", text)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        # Hard-split paragraphs longer than one chunk.
        pieces = [para[i : i + size] for i in range(0, len(para), size - overlap)] if len(para) > size else [para]
        for piece in pieces:
            if current and len(current) + len(piece) + 2 > size:
                chunks.append(current)
                current = current[-overlap:] + "\n\n" + piece if overlap else piece
            else:
                current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


async def process_document(db: AsyncSession, document_id: uuid.UUID, raw_text: str | None = None) -> None:
    doc = await db.get(KnowledgeDocument, document_id)
    if doc is None:
        return
    doc.status = DocumentStatus.PROCESSING
    doc.error = None
    await db.commit()

    try:
        if raw_text is None:
            if not doc.storage_path:
                raise ValueError("document has no stored file")
            raw_text = extract_text(doc.source_filename or doc.storage_path, storage.read_bytes(doc.storage_path))
        chunks = chunk_text(raw_text)
        if not chunks:
            raise ValueError("no text could be extracted from the document")

        # patient: a large document can need more embeddings than the per-minute quota allows.
        vectors = await get_gemini().embed(
            [f"{doc.title}\n\n{c}" for c in chunks], "RETRIEVAL_DOCUMENT", patient=True
        )
        await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id))
        db.add_all(
            KnowledgeChunk(
                company_id=doc.company_id, document_id=doc.id, domain_id=doc.domain_id, chunk_index=i,
                content=c, embedding=v,
            )
            for i, (c, v) in enumerate(zip(chunks, vectors, strict=True))
        )
        doc.chunk_count = len(chunks)
        doc.status = DocumentStatus.READY
        await db.commit()
        log.info("Indexed document %s (%d chunks)", doc.id, len(chunks))
    except Exception as exc:
        await db.rollback()
        doc = await db.get(KnowledgeDocument, document_id)
        if doc:
            doc.status = DocumentStatus.FAILED
            doc.error = str(exc)[:2000]
            await db.commit()
        log.exception("Failed to process document %s", document_id)
