import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.whatsapp_service import whatsapp_service
from app.llm import reply_conversation
from app import config

recipient = config.WHATSAPP_TEST_RECIPIENT
print(f"Target recipient: {recipient}")

# 1. Send text message 'hi'
res1 = whatsapp_service.send_text_message(recipient, "hi")
print(f"Sent 'hi' success: {res1.get('success')}, message_id: {res1.get('message_id')}")
if not res1.get("success"):
    print(f"Error details: {res1.get('error')}")

# 2. Get conversational response from Meta Model API
llm_reply = reply_conversation("hi")
sys.stdout.reconfigure(encoding="utf-8")
print(f"Meta Model API generated reply: {llm_reply}")

# 3. Send Meta Model API reply to WhatsApp recipient
res2 = whatsapp_service.send_text_message(recipient, llm_reply)
print(f"Sent reply success: {res2.get('success')}, message_id: {res2.get('message_id')}")
if not res2.get("success"):
    print(f"Error details: {res2.get('error')}")
