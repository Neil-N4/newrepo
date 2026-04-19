"""Action execution layer for Voice Right."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DRAFT_DIR = ROOT / "data" / "drafts"
DRAFT_DIR.mkdir(parents=True, exist_ok=True)


def _email_signoff(profile: dict) -> str:
    sender = str(profile.get("name", "Sender")).strip() or "Sender"
    return f"Best,\n{sender.title()}"


def _format_for_app(app: str, content: str, tone: str, profile: dict) -> str:
    base = str(content or "").strip()
    tone_value = str(tone or "").strip().lower()
    app_value = str(app or "").strip().lower()
    if not base:
        return ""

    if app_value == "email":
        greeting = "Hi,"
        if tone_value == "professional":
            body = f"{base}\n\nLet me know if you have any questions. Thank you.\n\n{_email_signoff(profile)}"
        else:
            body = f"{base}\n\n{_email_signoff(profile)}"
        return f"{greeting}\n\n{body}"

    if app_value == "message":
        suffix = " 😊" if tone_value in {"casual", "friendly"} else ""
        return f"{base}{suffix}"

    if app_value == "slack":
        return base if len(base) < 220 else f"{base[:217]}..."

    if app_value == "discord":
        if tone_value in {"fun", "casual"}:
            return f"{base} 🚀"
        return base

    return base


def _save_email_draft(to: str, subject: str, body: str, profile: dict) -> dict[str, Any]:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "to": to,
        "subject": subject,
        "body": body,
        "profile": profile.get("name", "default"),
        "created_at": timestamp,
    }
    draft_path = DRAFT_DIR / f"email-draft-{timestamp}.json"
    draft_path.write_text(json.dumps(payload, indent=2))
    return {
        "status": "success",
        "action": "Email drafted",
        "detail": f"To: {to} · {draft_path.name}",
        "draft_path": str(draft_path),
        "content": f"Subject: {subject}\n\n{body}",
    }


def execute_action(function_name: str, args: dict, profile: dict) -> dict:
    """Execute a routed function call and return a normalized action result."""

    safe_name = str(function_name or "").strip()
    safe_args = args if isinstance(args, dict) else {}
    safe_profile = profile if isinstance(profile, dict) else {}

    try:
        if safe_name == "send_email":
            to = str(safe_args.get("to", "")).strip() or "unknown@example.com"
            subject = str(safe_args.get("subject", "Voice Right Draft")).strip()
            body = str(safe_args.get("body", "")).strip()
            return _save_email_draft(to, subject, body, safe_profile)

        if safe_name == "send_message":
            to = str(safe_args.get("to", "")).strip() or "Unknown recipient"
            body = _format_for_app("message", str(safe_args.get("body", "")), "casual", safe_profile)
            return {
                "status": "success",
                "action": "Message drafted",
                "detail": f"To: {to}",
                "content": body,
            }

        if safe_name == "post_slack":
            channel = str(safe_args.get("channel", "#general")).strip() or "#general"
            message = _format_for_app("slack", str(safe_args.get("message", "")), "concise", safe_profile)
            return {
                "status": "success",
                "action": "Slack post prepared",
                "detail": f"Channel: {channel}",
                "content": message,
            }

        if safe_name == "format_output":
            app = str(safe_args.get("app", "")).strip() or "message"
            content = str(safe_args.get("content", "")).strip()
            tone = str(safe_args.get("tone", "neutral")).strip()
            formatted = _format_for_app(app, content, tone, safe_profile)
            return {
                "status": "success",
                "action": f"{app.title()} formatted",
                "detail": f"Tone: {tone}",
                "content": formatted,
                "app": app,
            }

        return {
            "status": "error",
            "action": "Unknown action",
            "detail": f"Unsupported function: {safe_name}",
        }
    except Exception as exc:
        return {
            "status": "error",
            "action": safe_name or "Unknown action",
            "detail": str(exc),
        }
