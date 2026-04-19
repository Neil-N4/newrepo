"""Flask blueprint that composes STT, routing, and action execution."""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

import actions
import router
import stt


compose_bp = Blueprint("compose", __name__)


def _decode_audio_to_webm(audio_b64: str, output_dir: Path) -> Path:
    encoded = str(audio_b64 or "").strip()
    if "," in encoded:
        encoded = encoded.split(",", 1)[1]
    webm_path = output_dir / "input.webm"
    webm_path.write_bytes(base64.b64decode(encoded))
    return webm_path


def _convert_webm_to_wav(webm_path: Path, output_dir: Path) -> Path:
    wav_path = output_dir / "input.wav"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(webm_path),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-sample_fmt",
        "s16",
        str(wav_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg conversion failed")
    return wav_path


def _parse_outputs(raw_response: str, target_apps: list[str], transcript: str) -> dict[str, str]:
    apps = [str(app).strip().lower() for app in target_apps if str(app).strip()]
    outputs: dict[str, str] = {app: "" for app in apps}
    text = str(raw_response or "").strip()
    if not text:
        return outputs

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        for app in apps:
            value = parsed.get(app)
            if isinstance(value, str):
                outputs[app] = value.strip()
        return outputs

    for app in apps:
        outputs[app] = text or transcript
    return outputs


def _merge_action_outputs(
    outputs: dict[str, str],
    action_results: list[dict[str, Any]],
    function_calls: list[dict[str, Any]],
) -> None:
    for call, result in zip(function_calls, action_results):
        name = str(call.get("name", "")).strip()
        args = call.get("arguments", {}) if isinstance(call.get("arguments"), dict) else {}
        content = str(result.get("content", "")).strip()
        if not content:
            continue

        if name == "send_email":
            outputs["email"] = content
        elif name == "send_message":
            outputs["message"] = content
        elif name == "post_slack":
            outputs["slack"] = content
        elif name == "format_output":
            app = str(result.get("app") or args.get("app", "")).strip().lower()
            if app:
                outputs[app] = content


def _finalize_outputs(outputs: dict[str, str], transcript: str) -> dict[str, str]:
    finalized: dict[str, str] = {}
    for app, value in outputs.items():
        text = str(value or "").strip()
        finalized[app] = text or transcript
    return finalized


@compose_bp.post("/api/compose")
def compose() -> Any:
    payload = request.get_json(silent=True) or {}
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    target_apps = payload.get("target_apps") if isinstance(payload.get("target_apps"), list) else []
    transcript_text = str(payload.get("transcript", "") or "").strip()

    stt_result: dict[str, Any] | None = None
    if not transcript_text and payload.get("audio_b64"):
        try:
            with tempfile.TemporaryDirectory(prefix="voice-right-compose-") as tmp:
                tmp_dir = Path(tmp)
                webm_path = _decode_audio_to_webm(str(payload.get("audio_b64", "")), tmp_dir)
                wav_path = _convert_webm_to_wav(webm_path, tmp_dir)
                stt_result = stt.transcribe(str(wav_path), profile.get("terms", []))
                transcript_text = str(stt_result.get("best", "") or "").strip()
        except Exception as exc:
            stt_result = {
                "parakeet": "",
                "whisper_pass1": "",
                "whisper_pass2": "",
                "best": "",
                "errors": {"audio": str(exc)},
            }

    routing_result = {
        "source": "local",
        "confidence": 0.0,
        "routing_label": "⚠️ Routing unavailable",
        "function_calls": [],
        "response": "",
    }
    if transcript_text:
        try:
            routing_result = router.route(transcript_text, profile, target_apps)
        except Exception as exc:
            routing_result["routing_label"] = f"⚠️ Routing failed · {exc}"
            routing_result["response"] = transcript_text

    function_calls = routing_result.get("function_calls", [])
    if not isinstance(function_calls, list):
        function_calls = []

    action_results: list[dict[str, Any]] = []
    for call in function_calls:
        if not isinstance(call, dict):
            continue
        action_results.append(
            actions.execute_action(
                str(call.get("name", "")),
                call.get("arguments", {}) if isinstance(call.get("arguments"), dict) else {},
                profile,
            )
        )

    outputs = _parse_outputs(str(routing_result.get("response", "")), target_apps, transcript_text)
    _merge_action_outputs(outputs, action_results, function_calls)
    outputs = _finalize_outputs(outputs, transcript_text)

    response_body = {
        "transcript": transcript_text,
        "routing": {
            "source": routing_result.get("source", "local"),
            "confidence": float(routing_result.get("confidence", 0.0) or 0.0),
            "label": routing_result.get("routing_label", "⚠️ Routing unavailable"),
        },
        "outputs": outputs,
        "actions": action_results,
        "function_calls": function_calls,
    }
    if stt_result is not None:
        response_body["stt"] = stt_result
    return jsonify(response_body)
