"""Hybrid routing for Voice Right.

FunctionGemma handles lightweight, structured local routing. Complex or
low-confidence requests fall back to Gemini cloud generation.
"""

from __future__ import annotations

import atexit
import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

from dotenv import load_dotenv


FUNCTION_GEMMA_MODEL_ID = "google/functiongemma-270m-it"
ROUTING_THRESHOLD = 0.65


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


def _local_messages(transcript: str, profile_context: dict, target_apps: list[str]) -> str:
    profile_payload = {
        "name": profile_context.get("name", "default"),
        "terms": profile_context.get("terms", []),
        "people": profile_context.get("people", []),
        "corrections": profile_context.get("corrections", {}),
    }
    messages = [
        {
            "role": "developer",
            "content": (
                "You are Voice Right's local routing engine. Decide whether the request "
                "can be handled locally with function calls. Prefer tool calls for direct "
                "actions. If no tool call is needed, return compact JSON with outputs for "
                "the requested apps. Keep your work short and structured."
            ),
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
                    },
                }
            ),
        },
    ]
    return json.dumps(messages)


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
    profile_terms = profile_context.get("terms", [])
    profile_people = profile_context.get("people", [])
    return f"""
You are Voice Right's cloud fallback router.

Transcript:
{transcript}

Profile vocabulary:
{json.dumps(profile_terms)}

Profile people:
{json.dumps(profile_people)}

Target apps:
{json.dumps(target_apps)}

Return JSON only. Include exactly one key per target app. Style each app appropriately:
- email: professional
- message: casual, warm, can include emoji
- slack: concise and direct
- discord: short and fun

If a target app is not requested, omit it.
""".strip()


def _call_gemini(transcript: str, profile_context: dict, target_apps: list[str]) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    import google.generativeai as genai  # imported lazily to keep local-only use lightweight

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")
    result = model.generate_content(
        _gemini_prompt(transcript, profile_context, target_apps),
        generation_config={"response_mime_type": "application/json"},
    )
    text = getattr(result, "text", "") or ""
    return {
        "source": "cloud",
        "confidence": 1.0,
        "routing_label": "☁️ Cloud · Gemini 2.0 Flash",
        "function_calls": [],
        "response": text.strip(),
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
        fallback_reason = f"Cloud fallback failed: {exc}"
        return {
            "source": local_result.get("source", "local"),
            "confidence": float(local_result.get("confidence", 0.0)),
            "routing_label": f"{local_result.get('routing_label', '⚠️ Local only')} · {fallback_reason}",
            "function_calls": local_result.get("function_calls", []),
            "response": str(local_result.get("response", safe_transcript)),
        }
