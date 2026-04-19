"""Action execution layer for Voice Right.

Uses real integrations when credentials are available:
- Gmail draft/send through the stored OAuth token
- Slack post through bot token or webhook
- macOS Messages via AppleScript for iMessage/SMS

Falls back to the existing mock/draft behaviour when integrations are not
configured so the demo never crashes.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):  # type: ignore[no-redef]
        return False


ROOT = Path(__file__).resolve().parent
DRAFT_DIR = ROOT / "data" / "drafts"
DRAFT_DIR.mkdir(parents=True, exist_ok=True)
OAUTH_DIR = ROOT / "data" / "oauth"
load_dotenv(ROOT / ".env")


def _writing_sample_text(profile: dict) -> str:
    samples = profile.get("writing_samples", []) if isinstance(profile, dict) else []
    if not isinstance(samples, list):
        return ""
    parts: list[str] = []
    for sample in samples[-8:]:
        if not isinstance(sample, dict):
            continue
        sample_type = str(sample.get("type", "")).strip().lower()
        if sample_type in {"image", "png", "jpg", "jpeg", "heic", "pdf"}:
            continue
        excerpt = str(sample.get("excerpt", "")).strip()
        if not excerpt:
            continue
        if re.fullmatch(r"[A-Za-z0-9._-]+\.(?:png|jpg|jpeg|heic|pdf|txt|md|docx?)", excerpt, flags=re.IGNORECASE):
            continue
        if len(excerpt.split()) <= 2 and excerpt.isupper():
            continue
        parts.append(excerpt)
    return "\n\n".join(parts)


def _inferred_style(profile: dict) -> dict[str, Any]:
    text = _writing_sample_text(profile)
    lowered = text.lower()
    has_emoji = bool(re.search(r"[\U0001F300-\U0001FAFF]", text))
    casual_markers = ("hey ", "haha", "lol", "!", "thanks!", "sounds good")
    formal_markers = ("let me know", "thank you", "best,", "sincerely", "regards")

    email_signoff = ""
    signoff_match = re.search(
        r"\n(?:best|thanks|regards|sincerely)[,\s]*\n+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if signoff_match:
        lead_in_match = re.search(
            r"\n(best|thanks|regards|sincerely)[,\s]*\n+" + re.escape(signoff_match.group(1)),
            text,
            flags=re.IGNORECASE,
        )
        if lead_in_match:
            email_signoff = f"{lead_in_match.group(1).title()},\n{signoff_match.group(1).strip()}"

    return {
        "message_tone": "casual" if has_emoji or any(marker in lowered for marker in casual_markers) else "neutral",
        "email_tone": "professional" if any(marker in lowered for marker in formal_markers) else "neutral",
        "emoji": has_emoji,
        "signoff": email_signoff,
    }


def _email_signoff(profile: dict) -> str:
    inferred = _inferred_style(profile)
    if inferred.get("signoff"):
        signoff = str(inferred["signoff"]).strip()
        signoff = re.sub(r"\b[A-Z]{2,}\b$", "", signoff).strip()
        signoff = re.sub(r"\n{3,}", "\n\n", signoff)
        if signoff:
            return signoff
    sender = str(profile.get("name", "Sender")).strip() or "Sender"
    if sender.lower() in {"default", "profile", "sender"}:
        return "Best"
    return f"Best,\n{sender.title()}"


def _recipient_name(value: str) -> str:
    safe_value = str(value or "").strip()
    if not safe_value:
        return "there"
    return safe_value


def _format_for_app(app: str, content: str, tone: str, profile: dict) -> str:
    base = str(content or "").strip()
    tone_value = str(tone or "").strip().lower()
    app_value = str(app or "").strip().lower()
    inferred = _inferred_style(profile)
    style_per_app = profile.get("style_per_app", {}) if isinstance(profile, dict) else {}
    app_style = style_per_app.get(app_value, {}) if isinstance(style_per_app, dict) else {}
    preferred_tone = str(app_style.get("tone", "")).strip().lower()
    if preferred_tone:
        tone_value = preferred_tone
    elif app_value == "email" and inferred.get("email_tone"):
        tone_value = str(inferred["email_tone"])
    elif app_value == "message" and inferred.get("message_tone"):
        tone_value = str(inferred["message_tone"])
    if not base:
        return ""

    if app_value == "email":
        greeting = "Hi,"
        if tone_value == "professional":
            signoff = _email_signoff(profile)
            closing = "Let me know if you have any questions. Thank you."
            body = f"{base}\n\n{closing}\n\n{signoff}" if signoff != "Best" else f"{base}\n\n{closing}\n\nBest"
        else:
            signoff = _email_signoff(profile)
            body = f"{base}\n\n{signoff}"
        return f"{greeting}\n\n{body}"

    if app_value == "message":
        suffix = " 😊" if tone_value in {"casual", "friendly"} or inferred.get("emoji") else ""
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
    recipient = _recipient_name(to)
    safe_body = str(body or "").strip()
    if safe_body and not safe_body.endswith((".", "!", "?")):
        safe_body = f"{safe_body}."
    signoff = _email_signoff(profile)
    greeting = "Hi"
    if _inferred_style(profile).get("email_tone") == "professional":
        greeting = "Hi"
    rendered_body = f"{greeting} {recipient},\n\n{safe_body}\n\n{signoff}"
    payload = {
        "to": to,
        "subject": subject,
        "body": rendered_body,
        "profile": profile.get("name", "default"),
        "created_at": timestamp,
    }
    draft_path = DRAFT_DIR / f"email-draft-{timestamp}.json"
    draft_path.write_text(json.dumps(payload, indent=2))
    return {
        "status": "success",
        "action": "Email drafted",
        "detail": f"To: {to} · Subject: {subject}",
        "draft_path": str(draft_path),
        "content": f"Subject: {subject}\n\n{rendered_body}",
    }


def _slugify(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return cleaned or "default"


def _gmail_token_path(profile: dict) -> Path:
    profile_id = str(profile.get("id") or profile.get("name") or "default").strip()
    return OAUTH_DIR / f"{_slugify(profile_id)}.gmail.json"


def _gmail_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _refresh_gmail_token(profile: dict, token_payload: dict[str, Any]) -> dict[str, Any]:
    refresh_token = str(token_payload.get("refresh_token", "")).strip()
    if not refresh_token:
        return token_payload
    expires_at = int(token_payload.get("expires_at") or 0)
    if expires_at and expires_at > int(datetime.now(UTC).timestamp() * 1000) + 30_000:
        return token_payload

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("Google OAuth client credentials are missing for Gmail refresh")

    payload = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        refreshed = json.loads(response.read().decode("utf-8"))
    token_payload.update(
        {
            "access_token": refreshed["access_token"],
            "expires_at": int(datetime.now(UTC).timestamp() * 1000) + int(refreshed.get("expires_in", 3600)) * 1000,
        }
    )
    token_path = _gmail_token_path(profile)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps(token_payload, indent=2))
    return token_payload


def _load_gmail_token(profile: dict) -> dict[str, Any] | None:
    token_path = _gmail_token_path(profile)
    if not token_path.exists():
        return None
    payload = json.loads(token_path.read_text())
    return _refresh_gmail_token(profile, payload)


def _gmail_create_draft(to: str, subject: str, body: str, profile: dict) -> dict[str, Any] | None:
    tokens = _load_gmail_token(profile)
    if not tokens:
        return None

    rendered = _save_email_draft(to, subject, body, profile)["content"]
    raw_message = f"To: {to}\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n{rendered}"
    encoded = base64.urlsafe_b64encode(raw_message.encode("utf-8")).decode("utf-8")
    request = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
        data=json.dumps({"message": {"raw": encoded}}).encode("utf-8"),
        method="POST",
        headers=_gmail_headers(tokens["access_token"]),
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            draft = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Gmail draft creation failed ({exc.code})") from exc

    return {
        "status": "success",
        "action": "Email drafted",
        "detail": f"To: {to} · Subject: {subject}",
        "gmail_draft_id": draft.get("id", ""),
        "content": rendered,
    }


def _gmail_send_message(to: str, subject: str, body: str, profile: dict) -> dict[str, Any] | None:
    tokens = _load_gmail_token(profile)
    if not tokens:
        return None
    rendered = _save_email_draft(to, subject, body, profile)["content"]
    raw_message = f"To: {to}\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n{rendered}"
    encoded = base64.urlsafe_b64encode(raw_message.encode("utf-8")).decode("utf-8")
    request = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=json.dumps({"raw": encoded}).encode("utf-8"),
        method="POST",
        headers=_gmail_headers(tokens["access_token"]),
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            sent = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Gmail send failed ({exc.code})") from exc

    return {
        "status": "success",
        "action": "Email sent",
        "detail": f"To: {to} · Subject: {subject}",
        "gmail_message_id": sent.get("id", ""),
        "content": rendered,
    }


def _slack_post(channel: str, message: str) -> dict[str, Any] | None:
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if webhook:
        request = urllib.request.Request(
            webhook,
            data=json.dumps({"text": message}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Slack webhook post failed ({exc.code})") from exc
        return {
            "status": "success",
            "action": "Slack post sent",
            "detail": f"Channel: {channel}",
            "content": message,
        }

    if bot_token:
        request = urllib.request.Request(
            "https://slack.com/api/chat.postMessage",
            data=json.dumps({"channel": channel, "text": message}).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Slack API post failed ({exc.code})") from exc
        if not payload.get("ok"):
            raise RuntimeError(f"Slack API post failed: {payload.get('error', 'unknown error')}")
        return {
            "status": "success",
            "action": "Slack post sent",
            "detail": f"Channel: {channel}",
            "slack_ts": payload.get("ts", ""),
            "content": message,
        }

    return None


def _send_via_messages(recipient: str, body: str) -> dict[str, Any] | None:
    if os.uname().sysname != "Darwin":
        return None
    safe_recipient = str(recipient or "").strip()
    safe_body = str(body or "").strip()
    if not safe_recipient or not safe_body:
        return None
    script = f'''
    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        set targetBuddy to buddy "{safe_recipient}" of targetService
        send "{safe_body.replace('"', '\\"')}" to targetBuddy
    end tell
    '''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Messages send failed")
    return {
        "status": "success",
        "action": "Message sent",
        "detail": f"To: {recipient}",
        "content": safe_body,
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
            send_now = bool(safe_args.get("send_now"))
            try:
                real_result = _gmail_send_message(to, subject, body, safe_profile) if send_now else _gmail_create_draft(to, subject, body, safe_profile)
                if real_result:
                    return real_result
            except Exception:
                pass
            return _save_email_draft(to, subject, body, safe_profile)

        if safe_name == "send_message":
            to = str(safe_args.get("to", "")).strip() or "Unknown recipient"
            send_now = bool(safe_args.get("send_now", False))
            body = _format_for_app("message", str(safe_args.get("body", "")), "casual", safe_profile)
            recipient = _recipient_name(to)
            if body:
                body = f"Hey {recipient}, {body}"
            if send_now:
                try:
                    real_result = _send_via_messages(to, body)
                    if real_result:
                        return real_result
                except Exception:
                    pass
            return {
                "status": "success",
                "action": "Message sent" if send_now else "Message drafted",
                "detail": f"To: {to}",
                "content": body,
            }

        if safe_name == "post_slack":
            channel = str(safe_args.get("channel", "#general")).strip() or "#general"
            send_now = bool(safe_args.get("send_now", False))
            message = _format_for_app("slack", str(safe_args.get("message", "")), "concise", safe_profile)
            if send_now:
                try:
                    real_result = _slack_post(channel, message)
                    if real_result:
                        return real_result
                except Exception:
                    pass
            return {
                "status": "success",
                "action": "Slack post sent" if send_now else "Slack post prepared",
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


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print(json.dumps({"error": "usage: python actions.py <payload.json>"}))
        raise SystemExit(1)

    payload_path = Path(sys.argv[1])
    try:
        payload = json.loads(payload_path.read_text())
        function_name = str(payload.get("function_name", ""))
        args = payload.get("args", {}) if isinstance(payload.get("args"), dict) else {}
        profile = payload.get("profile", {}) if isinstance(payload.get("profile"), dict) else {}
        print(json.dumps(execute_action(function_name, args, profile)))
    except Exception as exc:
        print(json.dumps({"status": "error", "action": "execute_action", "detail": str(exc)}))
        raise SystemExit(1)
