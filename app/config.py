"""Environment-only configuration. No secrets live in code.

The founder pastes keys himself (Meta Model API, WhatsApp token, DB URL).
This module only reads names; it never prints values.
"""

import os

try:
    from dotenv import load_dotenv

    # Loads .env from the working directory if present. Real environment
    # variables always win (override=False); this only fills gaps so a
    # plain `.env` file just works on a dev machine.
    load_dotenv()
except ImportError:
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


DATABASE_URL = _env("DATABASE_URL", "postgresql://brain:brain@localhost:5432/brain")
OLLAMA_URL = _env("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = _env("EMBED_MODEL", "embeddinggemma")
EMBED_DIMS = 768

# Meta Model API (Muse Spark) — used only for extraction + answer synthesis.
# Fictional sample-company data only on the free-credit tier.
META_API_BASE = _env("META_API_BASE", "https://api.meta.ai/v1")
META_API_KEY = _env("META_API_KEY", "")
META_MODEL = _env("META_MODEL", "muse-spark-1.3")

# WhatsApp Cloud API (founder connects his own app + test number).
WHATSAPP_TOKEN = _env("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = _env("WHATSAPP_PHONE_ID", "")
WHATSAPP_VERIFY_TOKEN = _env("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = _env("WHATSAPP_APP_SECRET", "")

# Hierarchy role mapping (comma-separated phone lists allowed).
# WHATSAPP_MANAGER_PHONE is kept as a CEO alias (backward compat).
WHATSAPP_CEO_PHONE = _env("WHATSAPP_CEO_PHONE", "")
WHATSAPP_CFO_PHONE = _env("WHATSAPP_CFO_PHONE", "")
WHATSAPP_CTO_PHONE = _env("WHATSAPP_CTO_PHONE", "")
WHATSAPP_MANAGER_PHONE = _env("WHATSAPP_MANAGER_PHONE", "")

COMPANY_ID = _env("COMPANY_ID", "sample-company")
