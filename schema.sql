-- Company brain schema. Postgres + pgvector. Own code, patterns only.
-- Rules baked in: append-only facts, no in-place edit, company scope pinned,
-- role filter is a Postgres predicate (never role text inside embedded chunks).
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  TEXT NOT NULL,
    custom_id   TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'unknown'
                CHECK (status IN ('unknown','queued','extracting','chunking',
                                  'embedding','indexing','done','failed')),
    processing  JSONB NOT NULL DEFAULT '[]'::jsonb,
    chunk_count INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id      UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    company_id       TEXT NOT NULL,
    position         INT NOT NULL,
    content          TEXT NOT NULL,
    embedded_content TEXT,
    embedding        vector(768),
    embedding_model  TEXT NOT NULL DEFAULT 'embeddinggemma',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memories (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id       TEXT NOT NULL,
    fact             TEXT NOT NULL,
    version          INT NOT NULL DEFAULT 1,
    is_latest        BOOLEAN NOT NULL DEFAULT TRUE,
    parent_memory_id UUID REFERENCES memories(id),
    root_memory_id   UUID REFERENCES memories(id),
    -- version relations, stored on the NEW fact keyed by the OLD fact's id:
    -- {"<old-id>": "updates" | "extends" | "derives"}
    memory_relations JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_inference     BOOLEAN NOT NULL DEFAULT FALSE,
    is_forgotten     BOOLEAN NOT NULL DEFAULT FALSE,
    is_static        BOOLEAN NOT NULL DEFAULT FALSE,
    forget_after     TIMESTAMPTZ,
    forget_reason    TEXT,
    embedding        vector(768),
    embedding_model  TEXT NOT NULL DEFAULT 'embeddinggemma',
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- role layer, enforced at query time as a predicate:
    visible_to       TEXT[] NOT NULL DEFAULT ARRAY['employee','manager'],
    effective_date   DATE,
    status           TEXT NOT NULL DEFAULT 'current'
                     CHECK (status IN ('current','superseded')),
    supersedes_id    UUID REFERENCES memories(id),
    source           TEXT NOT NULL,  -- 'document' | 'chat' | 'manual'
    source_message_id TEXT,
    hash             TEXT NOT NULL,
    text_lemmatized  TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Typed policy triples: the entity graph (policy -> limit -> role -> approver).
CREATE TABLE IF NOT EXISTS edges (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_id   UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    company_id  TEXT NOT NULL,
    subject     TEXT NOT NULL,
    relationship TEXT NOT NULL,
    object      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Evidence: every fact keeps its source(s) + relevance so answers cite them.
CREATE TABLE IF NOT EXISTS memory_sources (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_id       UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    document_id     UUID REFERENCES documents(id) ON DELETE SET NULL,
    source_type     TEXT NOT NULL,  -- 'document' | 'chat'
    source_ref      TEXT,           -- document custom_id or chat message id
    relevance_score INT NOT NULL DEFAULT 100,
    added_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memories_company_latest
    ON memories (company_id, is_latest);
CREATE INDEX IF NOT EXISTS idx_memories_visible
    ON memories USING GIN (visible_to);
CREATE INDEX IF NOT EXISTS idx_memories_embedding
    ON memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_memories_lemmatized
    ON memories USING GIN (to_tsvector('simple', COALESCE(text_lemmatized, '')));
CREATE INDEX IF NOT EXISTS idx_edges_memory
    ON edges (memory_id);
CREATE INDEX IF NOT EXISTS idx_edges_subject
    ON edges (company_id, subject);
