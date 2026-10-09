"""Production provider construction; tests inject gateways at domain seams."""
from sqlalchemy.orm import Session
from app.services.embedding.gemini import GeminiEmbeddingGateway
from app.services.generation.gemini import GeminiGenerationGateway
from app.services.vectorstore.pgvector_store import PgVectorStore


def generation_gateway():
    return GeminiGenerationGateway(require_accounting=True)


def embedding_gateway():
    return GeminiEmbeddingGateway(require_accounting=True)


def vector_store(db: Session):
    return PgVectorStore(db)
