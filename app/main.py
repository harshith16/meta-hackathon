"""Company brain API entrypoint.

Run with: uvicorn app.main:app --port 8000

Structured logging is configured once here; module loggers record ids
and outcomes only — never tokens, secrets, or message bodies.
"""

import logging

from fastapi import FastAPI

from app.embed import vectors_available
from app.instagram import router as instagram_router
from app.timeline import router as timeline_router
from app.upload import router as upload_router
from app.whatsapp import router as whatsapp_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="company-brain")
app.include_router(whatsapp_router)
app.include_router(instagram_router)
app.include_router(timeline_router)
app.include_router(upload_router)


@app.get("/health")
async def health():
    return {"status": "ok", "vectors": vectors_available()}


@app.get("/")
async def root():
    return {"service": "company-brain", "status": "ok"}
