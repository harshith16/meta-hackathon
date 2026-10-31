import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.whatsapp import _run_answer
from app import config

sys.stdout.reconfigure(encoding="utf-8")
q = "Isn't it mac book m4?"
reply = _run_answer(config.COMPANY_ID, "employee", q)
print("--- Question Answer ---")
print(reply)
