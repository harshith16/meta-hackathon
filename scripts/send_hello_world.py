"""Simple test script to send a hello_world template to the configured test recipient.

Usage:
    PYTHONPATH=. python scripts/send_hello_world.py [optional_phone_number]
"""

import os
import sys

# Ensure repository root is in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config
from app.whatsapp_service import sanitize_phone_number, whatsapp_service


def main():
    recipient = sys.argv[1] if len(sys.argv) > 1 else config.WHATSAPP_TEST_RECIPIENT
    if not recipient:
        print("ERROR: No recipient specified and WHATSAPP_TEST_RECIPIENT is not set in .env")
        sys.exit(1)

    clean_recipient = sanitize_phone_number(recipient)
    print(f"Sending 'hello_world' template message to: {clean_recipient}...")

    result = whatsapp_service.send_hello_world(recipient)

    if result["success"]:
        print("\n=== WhatsApp Message Sent Successfully ===")
        print(f"Status: {result.get('status_code')} OK")
        print(f"Message ID: {result.get('message_id')}")
        print(f"Recipient: {clean_recipient}")
    else:
        print("\n=== WhatsApp Message Failed ===")
        print(f"Status: {result.get('status_code')}")
        print(f"Error: {result.get('error')}")
        if result.get("error_code"):
            print(f"Error Code: {result.get('error_code')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
