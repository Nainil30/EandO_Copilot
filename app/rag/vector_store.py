import os
import hashlib
from typing import List, Dict, Any
from dotenv import load_dotenv

import chromadb
from chromadb.config import Settings

from google import genai
from google.genai import types

load_dotenv()

CHROMA_DIR = os.getenv("CHROMA_DIR", ".chroma")
KB_VERSION = os.getenv("KB_VERSION", "v1")

def _client() -> chromadb.PersistentClient:
    return chromadb.PersistentClient(
        path=CHROMA_DIR,
        settings=Settings(anonymized_telemetry=False)
    )

def _genai_client() -> genai.Client:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing in .env")
    return genai.Client(api_key=key)

def embed_texts(texts: List[str], *, title: str = "eando-kb") -> List[List[float]]:
    """
    Batch embeddings via Gemini Embedding model.
    Uses task_type=RETRIEVAL_DOCUMENT for better RAG embeddings.
    """
    if not texts:
        return []

    client = _genai_client()

    # Correct model for Gemini embeddings (Developer API / v1beta)
    model = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")

    # One call for many chunks (faster + cheaper)
    resp = client.models.embed_content(
        model=model,
        contents=texts,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT",
            title=title
        )
    )

    return [e.values for e in resp.embeddings]

def stable_id(prefix: str, content: str) -> str:
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{h}"

def get_collection(name: str):
    c = _client()
    return c.get_or_create_collection(name=f"{name}_{KB_VERSION}")

def upsert_docs(collection_name: str, docs: List[Dict[str, Any]]):
    """
    docs: [{id, text, metadata}]
    """
    col = get_collection(collection_name)

    texts = [d["text"] for d in docs]
    ids = [d["id"] for d in docs]
    metas = [d.get("metadata", {}) for d in docs]

    embs = embed_texts(texts, title=f"{collection_name}_{KB_VERSION}")

    col.upsert(
        ids=ids,
        documents=texts,
        metadatas=metas,
        embeddings=embs
    )

