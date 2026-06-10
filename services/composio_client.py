# services/composio_client.py
"""
Composio notification delivery.

Two functions:
  send_gmail(to, subject, body)
  send_whatsapp(phone, message)

Composio handles OAuth and API auth for both.
We just call the action with the content.
"""

import os
from dotenv import load_dotenv

load_dotenv()

COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY")


def send_gmail(to: str, subject: str, body: str) -> bool:
    """Send an email via Composio → Gmail."""
    try:
        from composio import ComposioToolSet, Action
        toolset = ComposioToolSet(api_key=COMPOSIO_API_KEY)
        result = toolset.execute_action(
            action=Action.GMAIL_SEND_EMAIL,
            params={
                "recipient_email": to,
                "subject": subject,
                "body": body,
            },
        )
        success = result.get("successfull", False) or result.get("success", False)
        if success:
            print(f"  [composio] ✅ Gmail sent to {to}")
        else:
            print(f"  [composio] ⚠️  Gmail result: {result}")
        return success
    except Exception as e:
        print(f"  [composio] ❌ Gmail failed: {e}")
        return False


def send_whatsapp(phone: str, message: str) -> bool:
    """Send a WhatsApp message via Composio."""
    try:
        from composio import ComposioToolSet, Action
        toolset = ComposioToolSet(api_key=COMPOSIO_API_KEY)
        result = toolset.execute_action(
            action=Action.WHATSAPP_SEND_MESSAGE,
            params={
                "phone": phone,
                "message": message,
            },
        )
        success = result.get("successfull", False) or result.get("success", False)
        if success:
            print(f"  [composio] ✅ WhatsApp sent to {phone}")
        else:
            print(f"  [composio] ⚠️  WhatsApp result: {result}")
        return success
    except Exception as e:
        print(f"  [composio] ❌ WhatsApp failed: {e}")
        return False


def send_notification(user_id: str, subject: str, message: str) -> dict:
    """
    Send both Gmail and WhatsApp in one call.
    Reads NOTIFY_EMAIL and NOTIFY_PHONE from .env per user.
    For Phase 6 dev, uses single global env vars.
    """
    # TODO: Fetch these from a User database table
    # For now, we mock a DB lookup and fallback to env
    mock_db = {
        "test-user": {
            "email": os.getenv("NOTIFY_EMAIL", ""),
            "phone": os.getenv("NOTIFY_PHONE", "")
        }
    }
    user_info = mock_db.get(user_id, {})
    email = user_info.get("email") or os.getenv("NOTIFY_EMAIL", "")
    phone = user_info.get("phone") or os.getenv("NOTIFY_PHONE", "")

    results = {}
    if email:
        results["gmail"] = send_gmail(email, subject, message)
    if phone:
        results["whatsapp"] = send_whatsapp(phone, message)

    if not email and not phone:
        print(f"  [composio] ⚠️  No NOTIFY_EMAIL or NOTIFY_PHONE in .env — skipping delivery")

    return results