"""Reusable Meta WhatsApp Business Cloud API Service.

Uses Meta's official Graph API directly over HTTPS.
Tokens and credentials are read from configuration/environment and never logged.
"""

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from app import config

logger = logging.getLogger(__name__)

DEFAULT_GRAPH_VERSION = "v21.0"
GRAPH_BASE_URL = "https://graph.facebook.com"


def sanitize_phone_number(phone: str) -> str:
    """Normalize phone number to digits only (E.164 without the '+' sign)."""
    return re.sub(r"[^\d]", "", phone or "")


class WhatsAppService:
    """Service to interact with Meta WhatsApp Business Cloud API over HTTPS."""

    def __init__(
        self,
        access_token: Optional[str] = None,
        phone_number_id: Optional[str] = None,
        graph_version: str = DEFAULT_GRAPH_VERSION,
        timeout: float = 30.0,
    ):
        self._access_token = access_token
        self._phone_number_id = phone_number_id
        self.graph_version = graph_version
        self.timeout = timeout

    @property
    def access_token(self) -> str:
        token = self._access_token if self._access_token is not None else (config.WHATSAPP_ACCESS_TOKEN or config.WHATSAPP_TOKEN)
        return (token or "").strip()

    @property
    def phone_number_id(self) -> str:
        pid = self._phone_number_id if self._phone_number_id is not None else (config.WHATSAPP_PHONE_NUMBER_ID or config.WHATSAPP_PHONE_ID)
        return (pid or "").strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.access_token and self.phone_number_id)

    @property
    def messages_endpoint(self) -> str:
        return f"{GRAPH_BASE_URL}/{self.graph_version}/{self.phone_number_id}/messages"

    def _get_headers(self) -> Dict[str, str]:
        token = self.access_token
        if not token:
            raise ValueError("WHATSAPP_ACCESS_TOKEN is not configured")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _parse_response(self, response: httpx.Response, recipient: str) -> Dict[str, Any]:
        status_code = response.status_code
        try:
            body = response.json()
        except Exception:
            body = {"raw_text": response.text}

        if 200 <= status_code < 300:
            messages = body.get("messages", [])
            message_id = messages[0].get("id") if messages else None
            logger.info(
                "WhatsApp message delivered successfully (status: %d, recipient: %s, message_id: %s)",
                status_code,
                recipient,
                message_id,
            )
            return {
                "success": True,
                "status_code": status_code,
                "message_id": message_id,
                "data": body,
                "error": None,
            }

        error_data = body.get("error", {}) if isinstance(body, dict) else {}
        error_msg = error_data.get("message") or f"HTTP {status_code} Error"
        error_code = error_data.get("code")
        error_subcode = error_data.get("error_subcode")
        logger.error(
            "WhatsApp API error (status: %d, code: %s, subcode: %s, message: %s)",
            status_code,
            error_code,
            error_subcode,
            error_msg,
        )
        return {
            "success": False,
            "status_code": status_code,
            "message_id": None,
            "data": body,
            "error": error_msg,
            "error_code": error_code,
            "error_subcode": error_subcode,
            "error_details": error_data,
        }

    @staticmethod
    def build_text_payload(to: str, message: str, preview_url: bool = False) -> Dict[str, Any]:
        """Construct Graph API payload for a text message."""
        clean_to = sanitize_phone_number(to)
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean_to,
            "type": "text",
            "text": {
                "preview_url": preview_url,
                "body": message,
            },
        }

    @staticmethod
    def build_template_payload(
        to: str,
        template_name: str = "hello_world",
        language_code: str = "en_US",
        components: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Construct Graph API payload for an approved template message."""
        clean_to = sanitize_phone_number(to)
        template_obj: Dict[str, Any] = {
            "name": template_name,
            "language": {"code": language_code},
        }
        if components:
            template_obj["components"] = components

        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean_to,
            "type": "template",
            "template": template_obj,
        }

    def send_text_message(self, to: str, message: str, preview_url: bool = False) -> Dict[str, Any]:
        """Send a free-form text message synchronously."""
        if not self.is_configured:
            return {
                "success": False,
                "status_code": 0,
                "message_id": None,
                "error": "WhatsApp service is not configured (missing token or phone_number_id)",
            }
        clean_to = sanitize_phone_number(to)
        payload = self.build_text_payload(clean_to, message, preview_url=preview_url)
        headers = self._get_headers()
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                resp = client.post(self.messages_endpoint, json=payload, headers=headers)
                return self._parse_response(resp, clean_to)
        except Exception as exc:
            logger.error("Failed to send WhatsApp text message: %s", exc)
            return {
                "success": False,
                "status_code": 0,
                "message_id": None,
                "error": str(exc),
            }

    async def send_text_message_async(
        self, to: str, message: str, preview_url: bool = False
    ) -> Dict[str, Any]:
        """Send a free-form text message asynchronously."""
        if not self.is_configured:
            return {
                "success": False,
                "status_code": 0,
                "message_id": None,
                "error": "WhatsApp service is not configured (missing token or phone_number_id)",
            }
        clean_to = sanitize_phone_number(to)
        payload = self.build_text_payload(clean_to, message, preview_url=preview_url)
        headers = self._get_headers()
        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
                resp = await client.post(self.messages_endpoint, json=payload, headers=headers)
                return self._parse_response(resp, clean_to)
        except Exception as exc:
            logger.error("Failed to send WhatsApp text message: %s", exc)
            return {
                "success": False,
                "status_code": 0,
                "message_id": None,
                "error": str(exc),
            }

    def send_template(
        self,
        to: str,
        template_name: str = "hello_world",
        language_code: str = "en_US",
        components: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Send an approved WhatsApp template synchronously."""
        if not self.is_configured:
            return {
                "success": False,
                "status_code": 0,
                "message_id": None,
                "error": "WhatsApp service is not configured (missing token or phone_number_id)",
            }
        clean_to = sanitize_phone_number(to)
        payload = self.build_template_payload(
            clean_to,
            template_name=template_name,
            language_code=language_code,
            components=components,
        )
        headers = self._get_headers()
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                resp = client.post(self.messages_endpoint, json=payload, headers=headers)
                return self._parse_response(resp, clean_to)
        except Exception as exc:
            logger.error("Failed to send WhatsApp template message: %s", exc)
            return {
                "success": False,
                "status_code": 0,
                "message_id": None,
                "error": str(exc),
            }

    async def send_template_async(
        self,
        to: str,
        template_name: str = "hello_world",
        language_code: str = "en_US",
        components: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Send an approved WhatsApp template asynchronously."""
        if not self.is_configured:
            return {
                "success": False,
                "status_code": 0,
                "message_id": None,
                "error": "WhatsApp service is not configured (missing token or phone_number_id)",
            }
        clean_to = sanitize_phone_number(to)
        payload = self.build_template_payload(
            clean_to,
            template_name=template_name,
            language_code=language_code,
            components=components,
        )
        headers = self._get_headers()
        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
                resp = await client.post(self.messages_endpoint, json=payload, headers=headers)
                return self._parse_response(resp, clean_to)
        except Exception as exc:
            logger.error("Failed to send WhatsApp template message: %s", exc)
            return {
                "success": False,
                "status_code": 0,
                "message_id": None,
                "error": str(exc),
            }

    def send_hello_world(self, to: Optional[str] = None) -> Dict[str, Any]:
        """Convenience method to send the 'hello_world' template to recipient or configured test recipient."""
        recipient = to or config.WHATSAPP_TEST_RECIPIENT
        if not recipient:
            return {
                "success": False,
                "status_code": 0,
                "message_id": None,
                "error": "No recipient provided and WHATSAPP_TEST_RECIPIENT is not configured",
            }
        return self.send_template(to=recipient, template_name="hello_world", language_code="en_US")


# Global default instance
whatsapp_service = WhatsAppService()


def main():
    """CLI test command to send hello_world template to configured test recipient."""
    import sys

    recipient = sys.argv[1] if len(sys.argv) > 1 else config.WHATSAPP_TEST_RECIPIENT
    if not recipient:
        print("Error: No recipient specified and WHATSAPP_TEST_RECIPIENT not set in environment.")
        sys.exit(1)

    print(f"Sending hello_world template to: {sanitize_phone_number(recipient)}...")
    result = whatsapp_service.send_hello_world(recipient)
    if result["success"]:
        print(f"Success! Message ID: {result.get('message_id')}")
        print(f"Status Code: {result.get('status_code')}")
    else:
        print(f"Failed! Status Code: {result.get('status_code')}")
        print(f"Error: {result.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
