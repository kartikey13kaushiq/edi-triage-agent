-- Runbook corpus for retrieval-augmented diagnosis.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS runbook_chunks (
    chunk_id  text PRIMARY KEY,
    source    text NOT NULL,
    section   text NOT NULL,
    content   text NOT NULL,
    embedding vector(512) NOT NULL
);

CREATE INDEX IF NOT EXISTS runbook_chunks_embedding_hnsw
    ON runbook_chunks USING hnsw (embedding vector_cosine_ops);

-- The agent connects as a role that can read the corpus and write only its own
-- checkpoints. It has no grants on any operational (partner / transfer) schema.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'triage_agent') THEN
        CREATE ROLE triage_agent LOGIN PASSWORD 'triage_agent';
    END IF;
END $$;
GRANT SELECT ON runbook_chunks TO triage_agent;
GRANT CREATE ON SCHEMA public TO triage_agent;  -- LangGraph checkpointer creates its tables on setup()
