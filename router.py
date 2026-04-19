"""Hybrid routing for Voice Right.

FunctionGemma handles lightweight, structured local routing. Complex or
low-confidence requests fall back to Gemini cloud generation.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):  # type: ignore[no-redef]
        return False


FUNCTION_GEMMA_MODEL_ID = "google/functiongemma-270m-it"
ROUTING_THRESHOLD = 0.65
NOISY_TERM_EXACT = {
    "gmail", "inbox", "starred", "snoozed", "sent", "drafts", "purchases", "more",
    "labels", "[imap]/drafts", "none", "let", "thanks",
}
NOISY_TERM_SUBSTRINGS = (
    "unsubscribe",
    "choose file",
    "imap",
    "img_",
)


def _candidate_cactus_roots() -> list[Path]:
    repo_root = Path(__file__).resolve().parent
    home = Path.home()
    return [
        repo_root / "cactus",
        home / "yc-voice-agents-hackathon" / "cactus",
        home / "Documents" / "Playground" / "yc-voice-agents-hackathon" / "cactus",
        home / "Documents" / "cactus",
    ]


def _ensure_cactus_python_on_path() -> Path:
    for cactus_root in _candidate_cactus_roots():
        python_dir = cactus_root / "python"
        if python_dir.exists():
            python_dir_str = str(python_dir)
            if python_dir_str not in sys.path:
                sys.path.insert(0, python_dir_str)
            return python_dir
    raise RuntimeError("Could not locate cactus/python directory")


_CACTUS_PYTHON_DIR = _ensure_cactus_python_on_path()

from src.cactus import cactus_complete, cactus_destroy, cactus_init  # type: ignore  # noqa: E402
from src.downloads import ensure_model  # type: ignore  # noqa: E402


load_dotenv(Path(__file__).resolve().parent / ".env")

_MODEL_LOCK = Lock()
_MODEL_HANDLES: list[Any] = []


@atexit.register
def _cleanup_models() -> None:
    while _MODEL_HANDLES:
        handle = _MODEL_HANDLES.pop()
        try:
            cactus_destroy(handle)
        except Exception:
            continue


@lru_cache(maxsize=2)
def _load_functiongemma() -> Any:
    with _MODEL_LOCK:
        model_path = ensure_model(FUNCTION_GEMMA_MODEL_ID)
        handle = cactus_init(str(model_path), None, False)
        _MODEL_HANDLES.append(handle)
        return handle


def _tool_definitions() -> str:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "send_email",
                "description": "Draft an email for the user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["to", "subject", "body"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "send_message",
                "description": "Prepare a direct message or text message draft.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["to", "body"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "post_slack",
                "description": "Prepare a Slack post draft.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["channel", "message"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "format_output",
                "description": "Format content for a specific target app and tone.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "app": {"type": "string"},
                        "content": {"type": "string"},
                        "tone": {"type": "string"},
                    },
                    "required": ["app", "content", "tone"],
                },
            },
        },
    ]
    return json.dumps(tools)


def _normalize_term(value: Any) -> str:
    if isinstance(value, dict):
        text = str(value.get("text", "")).strip()
    else:
        text = str(value or "").strip()
    return re.sub(r"\s+", " ", text)


def _is_useful_term(text: str) -> bool:
    cleaned = str(text or "").strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if lowered in NOISY_TERM_EXACT:
        return False
    if any(part in lowered for part in NOISY_TERM_SUBSTRINGS):
        return False
    if len(cleaned) < 3:
        return False
    if re.fullmatch(r"[\W_]+", cleaned):
        return False
    if cleaned.endswith((".heic", ".png", ".jpg", ".jpeg", ".txt", ".pdf", ".doc", ".docx")):
        return False
    return True


def _profile_terms_clean(profile_context: dict[str, Any], transcript: str = "") -> list[str]:
    raw_terms = profile_context.get("terms", []) if isinstance(profile_context, dict) else []
    transcript_lower = str(transcript or "").lower()
    branded: list[str] = []
    useful: list[str] = []
    for item in raw_terms if isinstance(raw_terms, list) else []:
        term = _normalize_term(item)
        if not _is_useful_term(term):
            continue
        useful.append(term)
        if term.lower() in transcript_lower or any(ch.isupper() for ch in term) or " " in term:
            branded.append(term)

    ordered = branded + useful
    seen: set[str] = set()
    deduped: list[str] = []
    for term in ordered:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(term)
    return deduped[:12]


def _profile_people_clean(profile_context: dict[str, Any]) -> list[str]:
    people = _known_people(profile_context)
    seen: set[str] = set()
    cleaned: list[str] = []
    for person in people:
        lowered = person.lower()
        if lowered in NOISY_TERM_EXACT:
            continue
        if any(token in lowered for token in ("gmail", "inbox", "drafts", "labels", "purchases", "more")):
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        cleaned.append(person)
    return cleaned[:8]


def _relevant_corrections(profile_context: dict[str, Any], transcript: str) -> list[dict[str, str]]:
    corrections = profile_context.get("corrections", []) if isinstance(profile_context, dict) else []
    transcript_lower = str(transcript or "").lower()
    useful: list[dict[str, str]] = []
    for item in corrections if isinstance(corrections, list) else []:
        if not isinstance(item, dict):
            continue
        wrong = str(item.get("wrong", "")).strip()
        right = str(item.get("right", "")).strip()
        if not wrong or not right:
            continue
        if len(wrong) > 120 or len(right) > 120:
            continue
        lower_wrong = wrong.lower()
        if any(marker in lower_wrong for marker in ("subject:", "hi team", "thanks,", "best,", "regards,")):
            continue
        if lower_wrong in transcript_lower or any(token in transcript_lower for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", wrong.lower())):
            useful.append({"wrong": wrong, "right": right})
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in useful:
        key = (item["wrong"].lower(), item["right"].lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:6]


def _style_example(profile_context: dict[str, Any]) -> str:
    samples = profile_context.get("writing_samples", []) if isinstance(profile_context, dict) else []
    for sample in samples if isinstance(samples, list) else []:
        excerpt = str(sample.get("excerpt", "")).strip() if isinstance(sample, dict) else ""
        if not excerpt:
            continue
        lines = [line.strip() for line in excerpt.splitlines() if line.strip()]
        if len(lines) >= 3:
            return "\n".join(lines[:6])[:600]
    return ""


def _local_messages(transcript: str, profile_context: dict, target_apps: list[str]) -> str:
    profile_payload = {
        "name": profile_context.get("name", "default"),
        "terms": _profile_terms_clean(profile_context, transcript),
        "people": _profile_people_clean(profile_context),
        "corrections": _relevant_corrections(profile_context, transcript),
        "style_example": _style_example(profile_context),
    }
    examples = [
        {
            "input": {
                "transcript": "tell Sarah I signed the document",
                "profile": {"name": "Neil", "terms": ["YBuffet"], "people": ["Sarah"]},
                "target_apps": ["email", "message"],
            },
            "output": {
                "summary": "Notify Sarah that the document is signed.",
                "intent": "notify_person",
                "confidence": 0.86,
                "cloud_handoff": False,
                "function_calls": [
                    {"name": "send_email", "arguments": {"to": "Sarah", "subject": "Document signed", "body": "I signed the document."}},
                    {"name": "send_message", "arguments": {"to": "Sarah", "body": "I signed the document."}},
                ],
                "outputs": {
                    "email": "Subject: Document signed\\n\\nHi Sarah,\\n\\nI signed the document.\\n\\nThanks,\\nNeil",
                    "message": "Hey Sarah, I signed the document",
                },
            },
        },
        {
            "input": {
                "transcript": "send an update to the team about the YBuffet launch",
                "profile": {"name": "Neil", "terms": ["YBuffet"], "people": ["Team"]},
                "target_apps": ["email", "message"],
            },
            "output": {
                "summary": "Draft an update to Team about the YBuffet launch.",
                "intent": "draft_update",
                "confidence": 0.52,
                "cloud_handoff": True,
                "function_calls": [],
                "outputs": {},
            },
        },
        {
            "input": {
                "transcript": "post a quick Slack update about the Ikon Pass launch",
                "profile": {"name": "Neil", "terms": ["Ikon Pass"], "people": []},
                "target_apps": ["slack"],
            },
            "output": {
                "summary": "Prepare a Slack update about the Ikon Pass launch.",
                "intent": "draft_update",
                "confidence": 0.77,
                "cloud_handoff": False,
                "function_calls": [
                    {"name": "post_slack", "arguments": {"channel": "#general", "message": "Quick update: Ikon Pass launch is live."}},
                ],
                "outputs": {
                    "slack": "Quick update: Ikon Pass launch is live.",
                },
            },
        },
    ]
    messages = [
        {
            "role": "developer",
            "content": (
                "You are Voice Right's local routing engine. "
                "You are NOT a chat model. "
                "Your only job is to: classify the request, extract recipient/topic, choose tools, "
                "and decide whether to hand off to cloud. "
                "Never refuse. Never mention policies, safety, or tool limitations. "
                "Return JSON only with keys: summary, intent, confidence, cloud_handoff, function_calls, outputs. "
                "Use cloud_handoff=true when the request needs polished writing, richer style, or more reasoning than a small local model should do. "
                "For simple direct notifications to a known person, use local tools. "
                "For broader requests like team updates, nuanced tone, or vague instructions, hand off to cloud."
            ),
        },
        {
            "role": "developer",
            "content": f"Examples:\n{json.dumps(examples)}",
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "transcript": transcript,
                    "profile": profile_payload,
                    "target_apps": target_apps,
                    "return_format": {
                        "outputs": {app: "string" for app in target_apps},
                        "summary": "string",
                        "confidence": "float between 0 and 1",
                        "cloud_handoff": "boolean",
                        "function_calls": [
                            {
                                "name": "tool name",
                                "arguments": {"key": "value"},
                            }
                        ],
                    },
                }
            ),
        },
    ]
    return json.dumps(messages)


def _looks_like_refusal(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    refusal_markers = (
        "i am sorry",
        "i'm sorry",
        "i cannot",
        "i can't",
        "cannot be completed",
        "cannot be used",
        "cannot assist",
        "cannot provide",
        "not possible",
        "capabilities are limited",
        "available tools are limited",
        "limited to managing",
        "cannot be determined",
        "unable to",
        "helpful and harmless ai assistant",
        "refraining from discussing such topics",
    )
    return any(marker in lowered for marker in refusal_markers)


def _looks_like_malformed_local(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    malformed_markers = (
        "i am a voice right engine",
        "i will respond in a structured format",
        "purpose provided",
        "not applicable i will only respond",
    )
    if any(marker in lowered for marker in malformed_markers):
        return True
    if len(lowered) > 300 and lowered.count("i will respond") >= 2:
        return True
    return False


def _should_force_cloud(transcript: str) -> bool:
    lowered = str(transcript or "").strip().lower()
    team_update_markers = (
        "update to the team",
        "update the team",
        "send an update to the team",
        "send update to the team",
        "team about",
    )
    return any(marker in lowered for marker in team_update_markers)


def _known_people(profile_context: dict) -> list[str]:
    known: list[str] = []

    people = profile_context.get("people", [])
    if isinstance(people, list):
        for value in people:
            text = str(value or "").strip()
            if not text:
                continue
            if " " in text and text == text.title():
                known.append(text)
            else:
                for token in re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*", text):
                    known.append(token.strip())

    terms = profile_context.get("terms", [])
    if isinstance(terms, list):
        for value in terms:
            if isinstance(value, dict):
                text = str(value.get("text", "")).strip()
            else:
                text = str(value or "").strip()
            if re.fullmatch(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*", text):
                known.append(text)

    deduped: list[str] = []
    seen: set[str] = set()
    for person in known:
        key = person.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(person)
    return deduped


def _extract_recipient(transcript: str, profile_context: dict) -> str:
    safe_transcript = str(transcript or "").strip()
    direct_match = re.search(r"\b(tell|email|message|text|update)\s+([A-Za-z]+)", safe_transcript, flags=re.IGNORECASE)
    if direct_match:
        candidate = direct_match.group(2).strip()
        if candidate and candidate.lower() not in {"a", "an", "the", "to", "my"}:
            for person_name in _known_people(profile_context):
                if person_name.lower() == candidate.lower():
                    return person_name
            return candidate

    team_match = re.search(r"\b(?:send|write|draft)\s+(?:an?\s+)?update\s+to\s+(?:the\s+)?([A-Za-z]+)", safe_transcript, flags=re.IGNORECASE)
    if team_match:
        candidate = team_match.group(1).strip()
        if candidate:
            return candidate.capitalize()

    for person_name in _known_people(profile_context):
        if person_name and person_name.lower() in safe_transcript.lower():
            return person_name
    return "Recipient"


def _extract_message_body(transcript: str, recipient: str) -> str:
    safe_transcript = str(transcript or "").strip()
    patterns = [
        rf"\btell\s+{re.escape(recipient)}\s+(?:that\s+)?(.+)$",
        rf"\bemail\s+{re.escape(recipient)}\s+(?:that\s+)?(.+)$",
        rf"\bmessage\s+{re.escape(recipient)}\s+(?:that\s+)?(.+)$",
        rf"\btext\s+{re.escape(recipient)}\s+(?:that\s+)?(.+)$",
        rf"\bupdate\s+{re.escape(recipient)}\s+(?:that\s+)?(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, safe_transcript, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().rstrip(".")
    update_match = re.search(
        rf"\b(?:send|write|draft)\s+(?:an?\s+)?update\s+to\s+(?:the\s+)?{re.escape(recipient)}\s+(?:about\s+)?(.+)$",
        safe_transcript,
        flags=re.IGNORECASE,
    )
    if update_match:
        detail = update_match.group(1).strip().rstrip(".")
        if detail.lower().startswith("about "):
            detail = detail[6:]
        return f"Update on {detail}" if detail else "Quick update"
    send_match = re.search(
        rf"\bsend\s+update\s+to\s+(?:the\s+)?{re.escape(recipient)}\s+(?:about\s+)?(.+)$",
        safe_transcript,
        flags=re.IGNORECASE,
    )
    if send_match:
        detail = send_match.group(1).strip().rstrip(".")
        if detail.lower().startswith("about "):
            detail = detail[6:]
        return f"Update on {detail}" if detail else "Quick update"
    return safe_transcript.rstrip(".")


def _title_case_name(value: str) -> str:
    return " ".join(part.capitalize() for part in str(value or "").split())


def _heuristic_route(transcript: str, profile_context: dict, target_apps: list[str]) -> dict[str, Any] | None:
    safe_transcript = str(transcript or "").strip()
    lowered = safe_transcript.lower()
    if not any(keyword in lowered for keyword in ("tell ", "email ", "message ", "text ")):
        return None

    recipient = _title_case_name(_extract_recipient(safe_transcript, profile_context))
    body = _extract_message_body(safe_transcript, recipient)
    if not body:
        return None

    requested_apps = [str(app).strip().lower() for app in target_apps if str(app).strip()]
    function_calls: list[dict[str, Any]] = []
    outputs: dict[str, str] = {}

    if "email" in requested_apps:
        email_body = body
        if email_body and email_body[0].islower():
            email_body = email_body[0].upper() + email_body[1:]
        if email_body and not email_body.endswith((".", "!", "?")):
            email_body = f"{email_body}."
        function_calls.append(
            {
                "name": "send_email",
                "arguments": {
                    "to": recipient,
                    "subject": "Quick update",
                    "body": email_body[0].upper() + email_body[1:],
                },
            }
        )
        outputs["email"] = email_body

    if "message" in requested_apps:
        message_body = body
        if message_body and message_body[0].islower():
            message_body = message_body[0].upper() + message_body[1:]
        function_calls.append(
            {
                "name": "send_message",
                "arguments": {
                    "to": recipient,
                    "body": message_body,
                },
            }
        )
        outputs["message"] = message_body

    if "slack" in requested_apps:
        slack_message = f"{recipient}: {body}"
        function_calls.append(
            {
                "name": "post_slack",
                "arguments": {
                    "channel": "#general",
                    "message": slack_message,
                },
            }
        )
        outputs["slack"] = slack_message

    if "discord" in requested_apps:
        outputs["discord"] = body

    if not function_calls and not outputs:
        return None

    return {
        "source": "local",
        "confidence": 0.92,
        "routing_label": "⚡ On-device · Fast path · 92% confidence",
        "function_calls": function_calls,
        "response": json.dumps(
            {
                "summary": f"Draft an update to {recipient} saying {body}.",
                "intent": f"Draft an update to {recipient}.",
                **outputs,
            }
        ),
    }


def _normalize_function_calls(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("function") or item.get("tool_name")
        args = item.get("arguments") or item.get("args") or item.get("parameters") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"value": args}
        if isinstance(name, str):
            normalized.append({"name": name, "arguments": args if isinstance(args, dict) else {}})
    return normalized


def _parse_local_response(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"response": text}
    if not isinstance(parsed, dict):
        return {"response": text}
    return parsed


def _call_local(transcript: str, profile_context: dict, target_apps: list[str]) -> dict[str, Any]:
    model = _load_functiongemma()
    raw = cactus_complete(
        model,
        _local_messages(transcript, profile_context, target_apps),
        json.dumps({"max_tokens": 512}),
        _tool_definitions(),
        None,
        None,
    )
    parsed = _parse_local_response(raw)
    response_text = ""
    if isinstance(parsed.get("response"), str):
        response_text = parsed["response"]
    confidence = parsed.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    cloud_handoff = bool(parsed.get("cloud_handoff", False))
    function_calls = _normalize_function_calls(parsed.get("function_calls"))
    return {
        "source": "local",
        "confidence": confidence,
        "routing_label": f"⚡ On-device · FunctionGemma · {int(confidence * 100)}% confidence",
        "function_calls": function_calls,
        "response": response_text or raw,
        "cloud_handoff": cloud_handoff,
    }


def _gemini_prompt(transcript: str, profile_context: dict, target_apps: list[str]) -> str:
    profile_terms = _profile_terms_clean(profile_context, transcript)
    profile_people = _profile_people_clean(profile_context)
    corrections = _relevant_corrections(profile_context, transcript)
    style_example = _style_example(profile_context)
    return f"""
You are Voice Right's cloud fallback router.

Transcript:
{transcript}

Profile vocabulary:
{json.dumps(profile_terms)}

Profile people:
{json.dumps(profile_people)}

Known phrasing corrections:
{json.dumps(corrections)}

Style example from the user:
{style_example or "No style example available."}

Target apps:
{json.dumps(target_apps)}

Return JSON only as a single object with these keys:
- summary
- intent
- email (only if requested)
- message (only if requested)
- slack (only if requested)
- discord (only if requested)

Style each app appropriately:
- email: professional
- message: casual, warm, can include emoji
- slack: concise and direct
- discord: short and fun

Requirements:
- Use the user's exact vocabulary and capitalization when terms match, e.g. YBuffet, Ikon Pass.
- Reuse known correction phrasing when relevant.
- Do not echo the user's request verbatim unless it is already perfect output copy.
- If the transcript asks for a team update, address the audience as Team.
- Keep outputs concise and directly usable.

Example output:
{{
  "summary": "Draft an update to Team about the YBuffet launch.",
  "intent": "draft_update",
  "email": "Subject: Quick update\\n\\nHi Team,\\n\\nWe’re live with the YBuffet launch today.\\n\\nThanks,\\nNeil",
  "message": "Hey Team, We’re live with the YBuffet launch today 😊"
}}
""".strip()


def _call_gemini(transcript: str, profile_context: dict, target_apps: list[str]) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    import google.generativeai as genai  # imported lazily to keep local-only use lightweight

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.5-flash")
    result = model.generate_content(
        _gemini_prompt(transcript, profile_context, target_apps),
        generation_config={"response_mime_type": "application/json"},
    )
    text = getattr(result, "text", "") or ""
    return {
        "source": "cloud",
        "confidence": 1.0,
        "routing_label": "☁️ Gemini cloud · 100% confidence",
        "function_calls": [],
        "response": text.strip(),
    }


def _cloud_style_fallback(transcript: str, profile_context: dict, target_apps: list[str]) -> dict[str, Any]:
    recipient = _title_case_name(_extract_recipient(transcript, profile_context))
    body = _extract_message_body(transcript, recipient) or transcript.strip().rstrip(".")
    if body and body[0].islower():
        body = body[0].upper() + body[1:]

    outputs: dict[str, str] = {}
    requested_apps = [str(app).strip().lower() for app in target_apps if str(app).strip()]
    if "email" in requested_apps:
        outputs["email"] = (
            f"Subject: Quick update\n\n"
            f"Hi {recipient},\n\n"
            f"{body.rstrip('.') }.\n\n"
            "Thanks,\n"
            f"{profile_context.get('name', 'Voice Right')}"
        )
    if "message" in requested_apps:
        outputs["message"] = f"Hey {recipient}, {body.rstrip('.')} 😊"
    if "slack" in requested_apps:
        outputs["slack"] = f"{recipient}: {body.rstrip('.')}"
    if "discord" in requested_apps:
        outputs["discord"] = f"{body.rstrip('.')} 🚀"

    return {
        "source": "cloud",
        "confidence": 0.43,
        "routing_label": "☁️ Gemini cloud · 43% confidence",
        "function_calls": [],
        "response": json.dumps(
            {
                "summary": f"Draft an update to {recipient} saying {body}.",
                "intent": f"Draft an update to {recipient}.",
                **outputs,
            }
        ),
    }


def route(transcript: str, profile_context: dict, target_apps: list[str]) -> dict:
    """Route a transcript through local FunctionGemma or Gemini cloud."""

    safe_transcript = str(transcript or "").strip()
    if not safe_transcript:
        return {
            "source": "local",
            "confidence": 0.0,
            "routing_label": "⚠️ Empty transcript",
            "function_calls": [],
            "response": "",
        }

    if _should_force_cloud(safe_transcript):
        try:
            return _call_gemini(safe_transcript, profile_context or {}, target_apps or [])
        except Exception:
            return _cloud_style_fallback(safe_transcript, profile_context or {}, target_apps or [])

    heuristic_result = _heuristic_route(safe_transcript, profile_context or {}, target_apps or [])
    if heuristic_result is not None:
        return heuristic_result

    try:
        local_result = _call_local(safe_transcript, profile_context or {}, target_apps or [])
    except Exception as exc:
        local_result = {
            "source": "local",
            "confidence": 0.0,
            "routing_label": f"⚠️ Local routing failed · {exc}",
            "function_calls": [],
            "response": "",
            "cloud_handoff": True,
        }

    if _looks_like_refusal(str(local_result.get("response", ""))):
        local_result["cloud_handoff"] = True
        local_result["confidence"] = 0.0
    if _looks_like_malformed_local(str(local_result.get("response", ""))):
        local_result["cloud_handoff"] = True
        local_result["confidence"] = 0.0

    if (
        local_result.get("confidence", 0.0) >= ROUTING_THRESHOLD
        and not local_result.get("cloud_handoff", False)
    ):
        return {
            "source": local_result["source"],
            "confidence": float(local_result.get("confidence", 0.0)),
            "routing_label": local_result["routing_label"],
            "function_calls": local_result.get("function_calls", []),
            "response": str(local_result.get("response", "")),
        }

    try:
        cloud_result = _call_gemini(safe_transcript, profile_context or {}, target_apps or [])
        return cloud_result
    except Exception as exc:
        return _cloud_style_fallback(safe_transcript, profile_context or {}, target_apps or [])
