"""
ingest.py — Load PDFs, chunk text, embed with sentence-transformers, store in pgvector.

Usage:
    python ingest.py --file path/to/doc.pdf
    python ingest.py --dir path/to/pdf_folder/
"""

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

# ── Config ────────────────────────────────────────────────────────────────────
DB_URL = os.getenv("DATABASE_URL", "postgresql://rag:rag@localhost:5432/rag")
EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")  # 384-dim
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "512"))   # tokens ≈ chars / 4
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "64"))

# ── DB helpers ─────────────────────────────────────────────────────────────────

DDL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT      NOT NULL,
    page        INT       NOT NULL,
    chunk_index INT       NOT NULL,
    content     TEXT      NOT NULL,
    embedding   VECTOR(384)
);

CREATE INDEX IF NOT EXISTS documents_embedding_idx
    ON documents USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
"""


def get_conn():
    return psycopg2.connect(DB_URL)


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()


def upsert_chunks(conn, rows):
    """rows: list of (source, page, chunk_index, content, embedding)"""
    sql = """
        INSERT INTO documents (source, page, chunk_index, content, embedding)
        VALUES %s
        ON CONFLICT DO NOTHING
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, rows, template="(%s, %s, %s, %s, %s::vector)")
    conn.commit()


# ── Text helpers ───────────────────────────────────────────────────────────────

def extract_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """Return [(page_number, text), ...]."""
    reader = PdfReader(str(pdf_path))
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        # Normalise whitespace
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            pages.append((i + 1, text))
    return pages


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Sliding-window character-level chunker."""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        # Try to break at sentence boundary
        if end < len(text):
            boundary = text.rfind(". ", start, end)
            if boundary != -1 and boundary > start + overlap:
                end = boundary + 1  # include the period
        chunks.append(text[start:end].strip())
        start = end - overlap
    return [c for c in chunks if c]


# ── Main ──────────────────────────────────────────────────────────────────────

def ingest_pdf(pdf_path: Path, model: SentenceTransformer, conn) -> int:
    source = pdf_path.name
    pages = extract_pages(pdf_path)
    rows = []
    for page_num, page_text in pages:
        chunks = chunk_text(page_text)
        for idx, chunk in enumerate(chunks):
            rows.append((source, page_num, idx, chunk, None))  # embedding TBD

    if not rows:
        print(f"  [skip] {source}: no extractable text")
        return 0

    # Batch-embed all chunks at once (much faster than one-by-one)
    texts = [r[3] for r in rows]
    embeddings = model.encode(texts, batch_size=64, show_progress_bar=False)

    rows_with_embeddings = [
        (r[0], r[1], r[2], r[3], emb.tolist())
        for r, emb in zip(rows, embeddings)
    ]
    upsert_chunks(conn, rows_with_embeddings)
    return len(rows_with_embeddings)


def main():
    parser = argparse.ArgumentParser(description="Ingest PDFs into pgvector")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="Single PDF file")
    group.add_argument("--dir", help="Directory of PDF files")
    args = parser.parse_args()

    print(f"Loading embedding model: {EMBED_MODEL}")
    model = SentenceTransformer(EMBED_MODEL)

    conn = get_conn()
    ensure_schema(conn)

    pdfs: list[Path] = []
    if args.file:
        pdfs = [Path(args.file)]
    else:
        pdfs = sorted(Path(args.dir).glob("**/*.pdf"))

    if not pdfs:
        print("No PDF files found.")
        sys.exit(1)

    total = 0
    for pdf in pdfs:
        print(f"Ingesting {pdf.name} ...", end=" ", flush=True)
        n = ingest_pdf(pdf, model, conn)
        print(f"{n} chunks stored")
        total += n

    conn.close()
    print(f"\nDone. {total} total chunks stored in pgvector.")


if __name__ == "__main__":
    main()
