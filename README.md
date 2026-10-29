# Company Brain — Meta Hackathon

Answers cite sources, remember superseded policies, and warn on
conflicts. WhatsApp only. Local-first: no hosting, no hosted DB,
no hosted embeddings.

## What it is

- **Graph memory, not document search.** Facts live in Postgres with
  version chains (`parent/root` ids, `memory_relations` updates /
  extends / derives). A correction never edits in place: one
  transaction inserts the new fact, flips the old fact to superseded,
  and records the edge.
- **Advanced RAG.** Candidates = semantic vectors (local
  EmbeddingGemma via Ollama, primary) + Postgres full-text + entity
  1-hop graph expansion. Ranking adds a recency/effective-date term
  and a superseded-row penalty, gates weak semantic matches first,
  and exposes `score_details` (never shown as confidence or
  freshness). Temporal `search_as_of` answers "what was true on
  date X". Role filtering is a Postgres predicate on `visible_to`.
- **Evidence in every answer.** Answer + cited fact ids + source
  documents/messages + effective-date freshness + conflict block
  when superseded rows also match. No match = an honest empty state,
  never an invented answer.
- **Meta AI tooling.** Muse Spark (`muse-spark-1.3` via
  `https://api.meta.ai/v1`) powers fact extraction and answer
  synthesis when `META_API_KEY` is set. Without a key, a
  deterministic template fallback still answers from stored facts.
  Only fictional sample-company data ever goes through the API
  (free-credit product-improvement clause).

## Run it

Prereqs: Python 3.12, Postgres 16 + pgvector, Ollama (for vectors;
the app still works without it, keyword path only).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in your own values; never commit .env
psql "$DATABASE_URL" -f schema.sql
psql "$DATABASE_URL" -f schema_media.sql
PYTHONPATH=. .venv/bin/python scripts/seed_demo.py   # fictional demo data
PYTHONPATH=. .venv/bin/uvicorn app.main:app --port 8000
```

Health: `GET /health` → `{"status": "ok", "vectors": true/false}`.
Timeline page: `GET /timeline/<memory-id>`.
Acceptance test (real DB): `PYTHONPATH=. .venv/bin/python
scripts/acceptance.py` — exits 0 only if every check passes.

## Connect WhatsApp

1. In your Meta developer app, set the webhook URL to
   `https://<your-tunnel>/webhook/whatsapp` and the verify token to
   your `WHATSAPP_VERIFY_TOKEN`.
2. Set `WHATSAPP_TOKEN` and `WHATSAPP_PHONE_ID` in `.env`.
3. Role rule (hierarchy): anyone may ask anything; answers are
   gated by the sender's role. `WHATSAPP_CEO_PHONE` (CEO / board /
   main executive) sees everything; `WHATSAPP_CFO_PHONE` sees
   finance + general; `WHATSAPP_CTO_PHONE` sees technical +
   general; everyone else is an employee and sees general only.
   Phone lists are comma-separated. `WHATSAPP_MANAGER_PHONE`
   still works as a CEO alias. Facts carry a domain tag
   (general / finance / technical) that drives visibility via
   `visible_to`; no schema change was needed for the hierarchy.

Document and image messages work on the same webhook: documents
are downloaded via the Graph API, saved under `MEDIA_DIR`, and
their text is extracted (PDF, DOCX, TXT) and fed to fact
extraction, so a forwarded document becomes answerable. The
sender gets a confirmation stating what was read — or plainly
that no text could be extracted. Images are stored with their
caption only (embeddinggemma is text-only); pixels are never
claimed as embedded.

Note: free-form replies work only within 24h of the user's last
message; outside that window WhatsApp requires a pre-approved
template — the webhook returns `template_required` instead of
faking delivery.

## Uploads

`POST /upload` (multipart `file` + optional `caption`) stores the
file under `MEDIA_DIR` (default `./media`) and records it in the
`media` table. PDF/DOCX/TXT uploads also get their text extracted,
ingested as searchable documents, and fed to fact extraction (the
receipt reports `text_extracted` and `facts_stored`). Images are
embedded via caption/text only (embeddinggemma v1 is text-only);
`embedded=false` whenever no text was available. Link media to
facts with `store.link_media_to_fact`.

## Layout (one responsibility per file)

- `app/db.py` — connection pool
- `app/store.py` — documents, chunks, facts, supersede, media
- `app/retrieve.py` — hybrid search, timeline, as-of search
- `app/answer.py` — answer shape (facts + citations + freshness +
  conflict)
- `app/llm.py` — Meta API client, extraction, synthesis, fallback
- `app/embed.py` — local EmbeddingGemma via Ollama (optional path)
- `app/whatsapp.py` — chat surface (WhatsApp only)
- `app/extract_text.py` — PDF/DOCX/TXT text extraction
- `app/timeline.py` — version-timeline page
- `app/upload.py` — file uploads
- `app/main.py` — app wiring, logging, health
- `scripts/acceptance.py` — acceptance test
- `scripts/seed_demo.py` — fictional demo seed

No code is copied from mem0, Supermemory, or EmbeddingGemma repos;
their patterns are reimplemented here in our own code.
