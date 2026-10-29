-- Media (file/image uploads) for the company brain. Local files only; the DB
-- stores metadata + a relative path, never blobs. Images are linked into the
-- graph via the edges table (subject 'media:<id>', relationship
-- 'evidence_for', object = memory id as text). An image's embedding, when
-- present, is the embedding of its caption/extracted text ONLY — never a
-- claim that pixels were embedded. embedded=false means no text was
-- available and no vector was stored.
CREATE TABLE IF NOT EXISTS media (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id     TEXT NOT NULL,
    filename       TEXT NOT NULL,
    mime_type      TEXT NOT NULL,
    storage_path   TEXT NOT NULL,
    caption        TEXT,
    extracted_text TEXT,
    embedding      vector(768),
    embedding_model TEXT NOT NULL DEFAULT 'embeddinggemma',
    embedded       BOOLEAN NOT NULL DEFAULT FALSE,
    metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_media_company ON media (company_id);
CREATE INDEX IF NOT EXISTS idx_media_embedding ON media USING hnsw (embedding vector_cosine_ops);
