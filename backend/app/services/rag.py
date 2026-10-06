"""Company- and domain-scoped retrieval.

Each company's knowledge is its own collection: every search is locked to the company first, so a
bot answering for one company can never see another company's documents. Within the company, a
billing question only ever searches billing documents.
"""
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import KnowledgeChunk, KnowledgeDocument
from app.services.gemini import get_gemini


log = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    content: str
    similarity: float


async def retrieve(
    db: AsyncSession,
    query: str,
    company_id: uuid.UUID,
    domain_id: uuid.UUID | None,
    top_k: int | None = None,
    min_similarity: float | None = None,
) -> list[RetrievedChunk]:
    top_k = top_k or settings.RAG_TOP_K
    min_similarity = settings.RAG_MIN_SIMILARITY if min_similarity is None else min_similarity
    gemini = get_gemini()
    if not gemini.enabled:
        # Offline hashed bag-of-words vectors score far lower than real embeddings.
        min_similarity = min(min_similarity, 0.1)

    try:
        query_vec = await gemini.embed_one(query, "RETRIEVAL_QUERY")
    except Exception as exc:
        # e.g. the embedding quota is used up. A live chat or call must not crash on that: carry on
        # with no knowledge found, and the assistant asks for details or raises a ticket instead.
        log.error("Knowledge search skipped, the question could not be embedded: %s", str(exc)[:200])
        return []
    distance = KnowledgeChunk.embedding.cosine_distance(query_vec)
    stmt = (
        select(KnowledgeChunk, KnowledgeDocument.title, distance.label("distance"))
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        # The company filter is the isolation boundary; the domain filter narrows within it
        # (domain_id None = the company's whole knowledge base).
        .where(KnowledgeChunk.company_id == company_id, *([KnowledgeChunk.domain_id == domain_id] if domain_id else []))
        .order_by(distance)
        .limit(top_k)
    )
    rows = (await db.execute(stmt)).all()
    results = [
        RetrievedChunk(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            document_title=title,
            content=chunk.content,
            similarity=1.0 - float(dist),
        )
        for chunk, title, dist in rows
    ]
    return [r for r in results if r.similarity >= min_similarity]


def format_context(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "(no relevant knowledge base content found)"
    return "\n\n".join(f"[{i + 1}] ({c.document_title})\n{c.content}" for i, c in enumerate(chunks))
