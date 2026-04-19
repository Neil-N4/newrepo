"""Dual-STT pipeline for Voice Right."""

from __future__ import annotations

import atexit
import json
import sys
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any


PROMPT_TOKEN = "<|startoftranscript|><|en|><|transcribe|><|notimestamps|>"
PARAKEET_MODEL_ID = "nvidia/parakeet-ctc-1.1b"
WHISPER_MODEL_ID = "openai/whisper-small"


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

from src.cactus import cactus_destroy, cactus_init, cactus_transcribe  # type: ignore  # noqa: E402
from src.downloads import ensure_model  # type: ignore  # noqa: E402


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


@lru_cache(maxsize=4)
def _load_model(model_id: str) -> Any:
    with _MODEL_LOCK:
        model_path = ensure_model(model_id)
        handle = cactus_init(str(model_path), None, False)
        _MODEL_HANDLES.append(handle)
        return handle


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    if isinstance(value, dict):
        for key in ("text", "transcript", "response", "transcription"):
            field = value.get(key)
            if isinstance(field, str) and field.strip():
                return field.strip()
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(parsed, dict):
        for key in ("text", "transcript", "response", "transcription"):
            field = parsed.get(key)
            if isinstance(field, str) and field.strip():
                return field.strip()
        return ""
    return text


def _whisper_options(vocabulary: list[str] | None) -> str:
    payload: dict[str, Any] = {}
    vocab = []
    for term in (vocabulary or []):
        if isinstance(term, dict):
            value = str(term.get("text", "")).strip()
        else:
            value = str(term).strip()
        if value:
            vocab.append(value)
    if vocab:
        payload["custom_vocabulary"] = vocab
        payload["vocabulary_boost"] = 0.5
    return json.dumps(payload)


def _run_transcribe(
    model_id: str,
    audio_path: str,
    prompt: str | None,
    options_json: str,
) -> str:
    model = _load_model(model_id)
    raw = cactus_transcribe(
        model,
        audio_path,
        prompt,
        options_json,
        None,
        None,
    )
    return _normalize_text(raw)


def transcribe(audio_path: str, vocabulary: list[str] | None = None) -> dict:
    errors: dict[str, str] = {}
    result = {
        "parakeet": "",
        "whisper_pass1": "",
        "whisper_pass2": "",
        "best": "",
        "errors": errors,
    }

    path = Path(audio_path)
    if not path.exists():
        errors["audio"] = f"Audio file not found: {audio_path}"
        return result

    whisper_options = _whisper_options(vocabulary)

    try:
        result["parakeet"] = _run_transcribe(
            PARAKEET_MODEL_ID,
            str(path),
            None,
            json.dumps({}),
        )
    except Exception as exc:
        errors["parakeet"] = str(exc)

    try:
        result["whisper_pass1"] = _run_transcribe(
            WHISPER_MODEL_ID,
            str(path),
            PROMPT_TOKEN,
            whisper_options,
        )
    except Exception as exc:
        errors["whisper_pass1"] = str(exc)

    pass2_prompt = PROMPT_TOKEN
    if result["whisper_pass1"]:
        pass2_prompt = f"{PROMPT_TOKEN} {result['whisper_pass1']}"
    try:
        result["whisper_pass2"] = _run_transcribe(
            WHISPER_MODEL_ID,
            str(path),
            pass2_prompt,
            whisper_options,
        )
    except Exception as exc:
        errors["whisper_pass2"] = str(exc)

    result["best"] = result["whisper_pass2"] or result["whisper_pass1"] or result["parakeet"]
    return result
