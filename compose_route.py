"""Flask blueprint and CLI compose pipeline for Voice Right."""

from __future__ import annotations

import base64
import difflib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:
    from flask import Blueprint, jsonify, request
except ImportError:  # CLI mode does not require Flask
    Blueprint = None  # type: ignore[assignment]
    jsonify = None  # type: ignore[assignment]
    request = None  # type: ignore[assignment]

import actions
import router
import stt


compose_bp = Blueprint("compose", __name__) if Blueprint is not None else None


def _profile_terms(profile: dict[str, Any]) -> list[str]:
    terms = profile.get("terms", []) if isinstance(profile, dict) else []
    normalized: list[str] = []
    if isinstance(terms, list):
        for term in terms:
            if isinstance(term, dict):
                value = str(term.get("text", "")).strip()
            else:
                value = str(term or "").strip()
            if value:
                normalized.append(value)
    return normalized


def _profile_corrections(profile: dict[str, Any]) -> list[tuple[str, str]]:
    corrections = profile.get("corrections", []) if isinstance(profile, dict) else []
    profile_terms = {term.lower() for term in _profile_terms(profile)}
    normalized: list[tuple[str, str]] = []
    if isinstance(corrections, list):
        for item in corrections:
            if not isinstance(item, dict):
                continue
            wrong = str(item.get("wrong", "")).strip()
            right = str(item.get("right", "")).strip()
            wrong_words = re.findall(r"\S+", wrong)
            right_words = re.findall(r"\S+", right)
            if (
                wrong
                and right
                and wrong.lower() != right.lower()
                and len(wrong) <= 80
                and len(right) <= 80
                and len(wrong_words) <= 8
                and len(right_words) <= 8
                and not _introduces_unrelated_profile_term(wrong, right, profile_terms)
            ):
                normalized.append((wrong, right))
    return normalized


def _introduces_unrelated_profile_term(wrong: str, right: str, profile_terms: set[str]) -> bool:
    wrong_lower = wrong.lower()
    right_lower = right.lower()
    for term in profile_terms:
        if len(term) < 3:
            continue
        if term in right_lower and term not in wrong_lower:
            return True
    return False


def _decode_audio_input(audio_b64: str, output_dir: Path) -> Path:
    encoded = str(audio_b64 or "").strip()
    if "," in encoded:
        encoded = encoded.split(",", 1)[1]
    payload = base64.b64decode(encoded)
    if payload[:4] == b"RIFF":
        wav_path = output_dir / "input.wav"
        wav_path.write_bytes(payload)
        return wav_path
    webm_path = output_dir / "input.webm"
    webm_path.write_bytes(payload)
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


def _prefer_existing_output(existing: str, candidate: str) -> bool:
    existing_text = str(existing or "").strip()
    candidate_text = str(candidate or "").strip()
    if not existing_text:
        return False
    if not candidate_text:
        return True
    # Preserve richer model outputs that already contain clear formatting.
    formatting_markers = ("\n", "Subject:", "Hi ", "Hey ")
    existing_has_format = any(marker in existing_text for marker in formatting_markers)
    candidate_has_format = any(marker in candidate_text for marker in formatting_markers)
    if existing_has_format and not candidate_has_format:
        return True
    return len(existing_text) >= len(candidate_text) and existing_has_format == candidate_has_format


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
            if not _prefer_existing_output(outputs.get("email", ""), content):
                outputs["email"] = content
        elif name == "send_message":
            if not _prefer_existing_output(outputs.get("message", ""), content):
                outputs["message"] = content
        elif name == "post_slack":
            if not _prefer_existing_output(outputs.get("slack", ""), content):
                outputs["slack"] = content
        elif name == "format_output":
            app = str(result.get("app") or args.get("app", "")).strip().lower()
            if app and not _prefer_existing_output(outputs.get(app, ""), content):
                outputs[app] = content

def _polish_email_output(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return cleaned

    replacements = {
        "We re": "We’re",
        "I m": "I’m",
        "can t": "can’t",
        "doesn t": "doesn’t",
    }
    for wrong, right in replacements.items():
        cleaned = re.sub(rf"\b{re.escape(wrong)}\b", right, cleaned)

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if "\n" not in cleaned and cleaned.lower().startswith("subject"):
        cleaned = re.sub(r"^Subject\s*:?[ ]*", "Subject: ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(Hi|Hello|Hey)\s+", r"\n\n\1 ", cleaned, count=1)
        cleaned = re.sub(r"\b(Thanks|Best|Regards)\b", r"\n\n\1", cleaned, count=1)
        cleaned = re.sub(r"\b(Thanks|Best|Regards)\s+([A-Z][A-Za-z]+)$", r"\1,\n\2", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _polish_message_output(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return cleaned
    cleaned = re.sub(r"\bWe re\b", "We’re", cleaned)
    cleaned = re.sub(r"\bI m\b", "I’m", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"^Hey ([A-Z][A-Za-z]+) ", r"Hey \1, ", cleaned)
    if cleaned.endswith("today"):
        cleaned += "!"
    return cleaned


def _polish_output(app: str, text: str) -> str:
    app_name = str(app or "").strip().lower()
    if app_name == "email":
        return _polish_email_output(text)
    if app_name == "message":
        return _polish_message_output(text)
    return str(text or "").strip()


def _normalize_for_compare(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _looks_like_transcript_echo(value: str, transcript: str) -> bool:
    candidate = _normalize_for_compare(value)
    source = _normalize_for_compare(transcript)
    if not candidate:
        return True
    if candidate == source:
        return True
    if len(candidate) >= 12 and candidate in source:
        return True
    return False


def _render_email_from_args(args: dict[str, Any], profile: dict[str, Any]) -> str:
    recipient = str(args.get("to", "")).strip() or "there"
    subject = str(args.get("subject", "")).strip() or "Quick update"
    body = str(args.get("body", "")).strip() or "Quick update."
    if body and body[0].islower():
        body = body[0].upper() + body[1:]
    if body and not body.endswith((".", "!", "?")):
        body = f"{body}."
    signoff = actions._email_signoff(profile)  # type: ignore[attr-defined]
    return f"Subject: {subject}\n\nHi {recipient},\n\n{body}\n\n{signoff}"


def _render_message_from_args(args: dict[str, Any], transcript: str) -> str:
    recipient = str(args.get("to", "")).strip() or "there"
    body = str(args.get("body", "")).strip() or str(transcript or "").strip()
    body = re.sub(r"^(Hey|Hi)\s+[A-Z][A-Za-z]+,\s*", "", body).strip()
    if body and body[0].islower():
        body = body[0].upper() + body[1:]
    if body and not body.endswith((".", "!", "?")):
        body = f"{body}."
    return f"Hey {recipient}, {body}".strip()


def _synthesize_outputs_from_calls(
    outputs: dict[str, str],
    function_calls: list[dict[str, Any]],
    transcript: str,
    profile: dict[str, Any],
) -> dict[str, str]:
    synthesized = dict(outputs)
    for call in function_calls:
        if not isinstance(call, dict):
            continue
        name = str(call.get("name", "")).strip()
        args = call.get("arguments", {}) if isinstance(call.get("arguments"), dict) else {}
        if name == "send_email":
            current = str(synthesized.get("email", "")).strip()
            if _looks_like_transcript_echo(current, transcript):
                synthesized["email"] = _render_email_from_args(args, profile)
        elif name == "send_message":
            current = str(synthesized.get("message", "")).strip()
            if _looks_like_transcript_echo(current, transcript) or re.search(r"^(Hey|Hi)\s+(My|Name|Recipient)\b", current, flags=re.IGNORECASE):
                synthesized["message"] = _render_message_from_args(args, transcript)
        elif name == "post_slack":
            current = str(synthesized.get("slack", "")).strip()
            if _looks_like_transcript_echo(current, transcript):
                synthesized["slack"] = str(args.get("message", "")).strip() or str(transcript or "").strip()
    return synthesized


def _finalize_outputs(outputs: dict[str, str], transcript: str) -> dict[str, str]:
    finalized: dict[str, str] = {}
    for app, value in outputs.items():
        text = str(value or "").strip() or transcript
        polished = _polish_output(app, text)
        if app == "email":
            subject, body = _extract_subject_and_body(polished, transcript)
            polished = f"Subject: {subject}\n\n{body}"
        finalized[app] = polished
    return finalized


def _replace_placeholder_recipients(outputs: dict[str, str], recipient: str) -> dict[str, str]:
    if _looks_placeholder_recipient(recipient):
        return outputs
    normalized_recipient = str(recipient).strip()
    if not normalized_recipient:
        return outputs
    replaced: dict[str, str] = {}
    for app, value in outputs.items():
        text = str(value or "")
        text = re.sub(r"\b(Recipient|Name|Mode|Person|Contact)\b", normalized_recipient, text, flags=re.IGNORECASE)
        replaced[app] = text
    return replaced


def _extract_intent(raw_response: str, transcript: str) -> str:
    text = str(raw_response or "").strip()
    if not text:
        return transcript
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(parsed, dict):
        for key in ("summary", "intent", "response"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return transcript


def _canonicalize_terms(text: str, profile: dict[str, Any], *, allow_fuzzy: bool = True) -> str:
    updated = str(text or "")
    profile_terms = sorted(_profile_terms(profile), key=len, reverse=True)
    for term in profile_terms:
        pattern = re.compile(rf"\b{re.escape(term)}\b", flags=re.IGNORECASE)
        updated = pattern.sub(term, updated)

    words = re.findall(r"[A-Za-z0-9']+|[^A-Za-z0-9']+", updated)

    def normalize_phrase(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    def maybe_fuzzy_match(candidate: str, term: str) -> bool:
        candidate_norm = normalize_phrase(candidate)
        term_norm = normalize_phrase(term)
        if not candidate_norm or not term_norm:
            return False
        ratio = difflib.SequenceMatcher(None, candidate_norm, term_norm).ratio()
        return ratio >= 0.78

    replacements: list[tuple[int, int, str]] = []
    plain_words = [token for token in words if re.search(r"[A-Za-z0-9]", token)]
    if allow_fuzzy and plain_words:
        for term in profile_terms:
            term_tokens = normalize_phrase(term).split()
            if not term_tokens:
                continue
            span = len(term_tokens)
            for start in range(0, len(plain_words) - span + 1):
                candidate = " ".join(plain_words[start:start + span])
                if maybe_fuzzy_match(candidate, term):
                    replacements.append((start, start + span, term))
                    break

    if replacements:
        merged_words: list[str] = []
        idx = 0
        replacement_map = {(start, end): term for start, end, term in replacements}
        while idx < len(plain_words):
            applied = False
            for (start, end), term in replacement_map.items():
                if idx == start:
                    merged_words.append(term)
                    idx = end
                    applied = True
                    break
            if not applied:
                merged_words.append(plain_words[idx])
                idx += 1
        # Rebuild a plain-text canonicalized version when fuzzy matches were applied.
        updated = " ".join(merged_words)
    return updated


def _apply_corrections(text: str, profile: dict[str, Any]) -> str:
    updated = str(text or "")
    for wrong, right in sorted(_profile_corrections(profile), key=lambda item: len(item[0]), reverse=True):
        updated = re.sub(rf"(?i)\b{re.escape(wrong)}\b", right, updated)
    return updated


def _looks_like_low_quality_stt(text: str) -> bool:
    cleaned = str(text or "").strip()
    if not cleaned:
        return True
    lowered = cleaned.lower()
    junk = {
        "you", "thank you", "thanks", "thanks you", "yeah", "uh", "um", "okay", "ok",
        "thanks for watching", "thank you for watching", "thank you for listening",
    }
    if lowered in junk:
        return True
    words = re.findall(r"[a-zA-Z']+", cleaned)
    return len(words) <= 1


def _pick_best_transcript(stt_result: dict[str, Any], profile: dict[str, Any]) -> str:
    best = str(stt_result.get("best", "") or "").strip()
    whisper2 = str(stt_result.get("whisper_pass2", "") or "").strip()
    whisper1 = str(stt_result.get("whisper_pass1", "") or "").strip()
    parakeet = str(stt_result.get("parakeet", "") or "").strip()

    candidates = [best, whisper2, whisper1, parakeet]

    def score(candidate: str) -> tuple[int, int, int]:
        if not candidate:
            return (-1, -1, -1)
        low_quality = _looks_like_low_quality_stt(candidate)
        canonical = _canonicalize_terms(candidate, profile)
        terms = _profile_terms(profile)
        exact_hits = 0
        for term in terms:
            if term.lower() in canonical.lower():
                exact_hits += 1
        words = len(re.findall(r"[A-Za-z0-9']+", candidate))
        return (0 if low_quality else 1, exact_hits, words)

    ranked = sorted(candidates, key=score, reverse=True)
    for candidate in ranked:
        if candidate and not _looks_like_low_quality_stt(candidate):
            return candidate
    return ""


def _parse_response_payload(raw_response: str) -> dict[str, Any]:
    text = str(raw_response or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_subject_and_body(email_text: str, transcript: str) -> tuple[str, str]:
    text = str(email_text or "").strip()
    if not text:
        fallback = str(transcript or "").strip() or "Quick update"
        return ("Quick update", fallback)

    subject = "Quick update"
    subject_match = re.search(r"^Subject:\s*(.+)$", text, flags=re.IGNORECASE | re.MULTILINE)
    if subject_match:
        subject = subject_match.group(1).strip() or subject
    if re.fullmatch(r"(hi|hello|hey|name|recipient)[,!\s]*", subject, flags=re.IGNORECASE):
        subject = "Quick update"

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    body_lines: list[str] = []
    for line in lines:
        lower = line.lower()
        if lower.startswith("subject:"):
            continue
        if re.match(r"^(hi|hello|hey)\b", lower):
            continue
        if re.match(r"^(thanks|best|regards|sincerely|cheers)\b", lower):
            break
        body_lines.append(line)
    body = "\n".join(body_lines).strip() or str(transcript or "").strip() or "Quick update"
    if re.fullmatch(r"(hi|hello|hey|name|recipient)[,!\s]*", body, flags=re.IGNORECASE):
        body = str(transcript or "").strip() or "Quick update"
    return (subject, body)


def _looks_placeholder_recipient(value: str) -> bool:
    cleaned = str(value or "").strip()
    if not cleaned:
        return True
    return bool(
        re.fullmatch(
            r"(name|recipient|person|contact|someone|team member|target|unknown|n/?a|tbd|my|me|mode)",
            cleaned,
            flags=re.IGNORECASE,
        )
    )


def _infer_recipient(transcript: str, function_calls: list[dict[str, Any]], outputs: dict[str, str]) -> str:
    disallowed = {"gmail", "email", "mail", "slack", "discord", "message", "messages", "imessage", "sms", "text"}
    for call in function_calls:
        if not isinstance(call, dict):
            continue
        args = call.get("arguments", {}) if isinstance(call.get("arguments"), dict) else {}
        for key in ("to", "channel"):
            value = str(args.get(key, "")).strip()
            if value and value.lower() not in disallowed and not _looks_placeholder_recipient(value):
                return value

    raw_transcript = str(transcript or "").strip()
    lowered = raw_transcript.lower()
    if "team" in lowered:
        return "Team"
    # Self-introductions are not recipient signals.
    if re.search(r"\bmy name(?:\s+is|['’]s)\b", lowered):
        lowered = lowered.replace("my name is", "").replace("my name's", "").replace("my name s", "")
    role_match = re.search(
        r"\b(?:email|message|text|notify|tell|send)\s+(?:the\s+)?(client|teammate|manager|founder|designer|engineer|alex|sarah)\b",
        lowered,
    )
    if role_match:
        candidate = role_match.group(1).title()
        if candidate.lower() not in disallowed and not _looks_placeholder_recipient(candidate):
            return candidate
    leading_name = re.match(r"^\s*([A-Z][a-z]+)\b", raw_transcript)
    if leading_name:
        candidate = leading_name.group(1).strip()
        blocked = {"Hi", "Hey", "Hello", "Email", "Message", "Text", "Send", "Tell", "Notify", "Update"}
        if candidate not in blocked:
            if not _looks_placeholder_recipient(candidate):
                return candidate
    reviewer_match = re.search(r"\bmy\s+([a-z]+)\b", lowered)
    if reviewer_match:
        candidate = reviewer_match.group(1).title()
        if not _looks_placeholder_recipient(candidate):
            return candidate
    direct_match = re.search(r"\b(?:tell|email|message|text|notify)\s+([A-Z][a-z]+|[a-z]+)", raw_transcript)
    if direct_match:
        candidate = direct_match.group(1).title()
        if candidate.lower() not in {"my", "me", "the", "a", "an", "sent", "send", "gmail", "email", "message"} and not _looks_placeholder_recipient(candidate):
            return candidate
    to_match = re.search(r"\bto\s+([A-Z][a-z]+|team)\b", raw_transcript)
    if to_match:
        candidate = to_match.group(1).title()
        if candidate.lower() not in {"sent", "send", "gmail", "email", "message"} and not _looks_placeholder_recipient(candidate):
            return candidate

    for app_value in outputs.values():
        greeting_match = re.search(r"\b(?:Hi|Hello|Hey)\s+([A-Z][A-Za-z]+)", str(app_value or ""))
        if greeting_match:
            candidate = greeting_match.group(1)
            if candidate.lower() not in disallowed and not _looks_placeholder_recipient(candidate):
                return candidate
    return "there"


def _default_function_calls(
    target_apps: list[str],
    outputs: dict[str, str],
    transcript: str,
    recipient: str,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    selected = [str(app).strip().lower() for app in target_apps if str(app).strip()]
    if "email" in selected and outputs.get("email"):
        subject, body = _extract_subject_and_body(outputs.get("email", ""), transcript)
        calls.append(
            {
                "name": "send_email",
                "arguments": {
                    "to": recipient,
                    "subject": subject,
                    "body": body,
                },
            }
        )
    if "message" in selected and outputs.get("message"):
        message_body = re.sub(r"^(Hey|Hi)\s+[A-Z][A-Za-z]+,\s*", "", outputs.get("message", "")).strip()
        calls.append(
            {
                "name": "send_message",
                "arguments": {
                    "to": recipient,
                    "body": message_body or transcript,
                },
            }
        )
    if "slack" in selected and outputs.get("slack"):
        calls.append(
            {
                "name": "post_slack",
                "arguments": {
                    "channel": "#general",
                    "message": outputs.get("slack", "").strip() or transcript,
                },
            }
        )
    return calls


def _action_label(function_name: str) -> str:
    return {
        "send_email": "draft_and_send_email",
        "send_message": "send_message",
        "post_slack": "post_slack_message",
        "format_output": "format_output",
    }.get(function_name, function_name or "draft_output")


def _choose_route_label(confidence: float) -> str:
    if confidence >= 0.75:
        return "local"
    if confidence >= 0.45:
        return "local_with_confirmation"
    return "cloud"


def _build_action_plan(
    transcript: str,
    intent_text: str,
    target_apps: list[str],
    function_calls: list[dict[str, Any]],
    outputs: dict[str, str],
    routing_result: dict[str, Any],
) -> dict[str, Any]:
    selected_apps = [str(app).strip().lower() for app in target_apps if str(app).strip()]
    primary_app = next((app for app in selected_apps if app), "email")
    lowered_transcript = str(transcript or "").lower()
    greeting_direct_message = bool(re.match(r"^\s*(?:hi|hey|hello)\s+[a-z][a-z'-]*\b", lowered_transcript))
    preferred_name = ""
    if "message" in selected_apps and (
        re.search(r"\b(tell|text|message|dm|ping)\b", lowered_transcript)
        or greeting_direct_message
    ):
        preferred_name = "send_message"
        primary_app = "messages"
    elif "slack" in selected_apps and re.search(r"\bslack\b", lowered_transcript):
        preferred_name = "post_slack"
        primary_app = "slack"
    elif "email" in selected_apps and re.search(r"\b(email|mail)\b", lowered_transcript):
        preferred_name = "send_email"
        primary_app = "email"

    primary_call = next(
        (call for call in function_calls if str(call.get("name", "")).strip() == preferred_name),
        function_calls[0] if function_calls else {},
    )
    primary_name = str(primary_call.get("name", "")).strip() or preferred_name
    recipient = _infer_recipient(transcript, function_calls, outputs)
    confidence = float(routing_result.get("confidence", 0.0) or 0.0)
    if str(routing_result.get("source", "")).lower() == "cloud":
        confidence = max(confidence, 0.9)

    effective_name = primary_name or ("send_message" if primary_app == "messages" else (f"send_{primary_app}" if primary_app else "draft_output"))
    requires_confirmation = effective_name in {"send_email", "send_message", "post_slack"}
    route_choice = _choose_route_label(confidence)
    if requires_confirmation and route_choice == "local":
        route_choice = "local_with_confirmation"

    action_name = _action_label(effective_name)
    summary = f"{action_name.replace('_', ' ').title()} for {recipient}"
    if primary_app == "slack":
        summary = f"Post Slack update to {recipient}"
    elif primary_app == "messages":
        summary = f"Send message to {recipient}"
    elif primary_app == "email":
        summary = f"Send email to {recipient}"

    return {
        "intent": intent_text,
        "target_app": primary_app,
        "recipient": recipient,
        "requires_confirmation": requires_confirmation,
        "action": action_name,
        "confidence": confidence,
        "reasoning_level": str(routing_result.get("source", "local")),
        "route": route_choice,
        "summary": summary,
        "status": "awaiting_user_confirmation" if requires_confirmation else "execute_now",
    }


def _summarize_execution_results(
    action_results: list[dict[str, Any]],
    action_plan: dict[str, Any],
    approved: bool,
    execute: bool,
) -> dict[str, Any]:
    if action_plan.get("requires_confirmation") and not approved:
        return {
            "status": "awaiting_confirmation",
            "title": "Awaiting confirmation",
            "detail": "Review the draft, then approve it to enable send.",
        }
    if action_plan.get("requires_confirmation") and approved and not execute:
        return {
            "status": "ready_to_send",
            "title": "Draft approved",
            "detail": "Draft approved. Review the message, then click Send now.",
        }
    if not action_results:
        return {
            "status": "draft_ready",
            "title": "Draft ready",
            "detail": "Outputs are ready to review.",
        }
    successful = [result for result in action_results if str(result.get("status", "")).lower() == "success"]
    if successful:
        detail = " · ".join(str(result.get("detail", "")).strip() for result in successful if str(result.get("detail", "")).strip())
        return {
            "status": "executed",
            "title": "Execution complete",
            "detail": detail or f"{len(successful)} action(s) completed.",
        }
    first_error = action_results[0]
    return {
        "status": "error",
        "title": "Execution failed",
        "detail": str(first_error.get("detail", "Unknown error")),
    }


def compose_payload(payload: dict[str, Any]) -> dict[str, Any]:
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    target_apps = payload.get("target_apps") if isinstance(payload.get("target_apps"), list) else []
    transcript_text = str(payload.get("transcript", "") or "").strip()

    stt_result: dict[str, Any] | None = None
    if not transcript_text and payload.get("file_path"):
        try:
            file_path = Path(str(payload.get("file_path", "")))
            stt_result = stt.transcribe(str(file_path), profile.get("terms", []))
            transcript_text = _pick_best_transcript(stt_result, profile)
        except Exception as exc:
            stt_result = {
                "parakeet": "",
                "whisper_pass1": "",
                "whisper_pass2": "",
                "best": "",
                "errors": {"audio": str(exc)},
            }
    elif not transcript_text and payload.get("audio_b64"):
        try:
            with tempfile.TemporaryDirectory(prefix="voice-right-compose-") as tmp:
                tmp_dir = Path(tmp)
                input_path = _decode_audio_input(str(payload.get("audio_b64", "")), tmp_dir)
                wav_path = input_path if input_path.suffix.lower() == ".wav" else _convert_webm_to_wav(input_path, tmp_dir)
                stt_result = stt.transcribe(str(wav_path), profile.get("terms", []))
                transcript_text = _pick_best_transcript(stt_result, profile)
        except Exception as exc:
            stt_result = {
                "parakeet": "",
                "whisper_pass1": "",
                "whisper_pass2": "",
                "best": "",
                "errors": {"audio": str(exc)},
            }

    transcript_text = _apply_corrections(_canonicalize_terms(transcript_text, profile), profile)
    if stt_result is not None:
        for key in ("parakeet", "whisper_pass1", "whisper_pass2", "best"):
            stt_result[key] = _apply_corrections(_canonicalize_terms(str(stt_result.get(key, "")), profile), profile)

    routing_result = {
        "source": "local",
        "confidence": 0.0,
        "routing_label": "⚠️ Routing unavailable",
        "function_calls": [],
        "response": "",
    }
    if transcript_text:
        try:
            routing_result = router.route(
                transcript_text,
                profile,
                target_apps,
                force_cloud=bool(payload.get("force_cloud", False)),
            )
        except Exception as exc:
            routing_result["routing_label"] = f"⚠️ Routing failed · {exc}"
            routing_result["response"] = transcript_text
    else:
        routing_result["response"] = "We couldn't transcribe that audio. Try again or type your request."

    function_calls = routing_result.get("function_calls", [])
    if not isinstance(function_calls, list):
        function_calls = []
    outputs = _parse_outputs(str(routing_result.get("response", "")), target_apps, transcript_text)
    intent_text = _apply_corrections(
        _canonicalize_terms(_extract_intent(str(routing_result.get("response", "")), transcript_text), profile),
        profile,
    )
    if not function_calls:
        inferred_recipient = _infer_recipient(transcript_text, [], outputs)
        function_calls = _default_function_calls(target_apps, outputs, transcript_text, inferred_recipient)

    action_plan = _build_action_plan(
        transcript_text,
        intent_text,
        target_apps,
        function_calls,
        outputs,
        routing_result,
    )
    approved = bool(payload.get("approved", False) or payload.get("confirmed", False))
    execute = bool(payload.get("execute", False))
    action_results: list[dict[str, Any]] = []
    requires_confirmation = bool(action_plan.get("requires_confirmation"))
    should_execute = bool(function_calls) and (
        (requires_confirmation and execute)
        or (not requires_confirmation)
    )
    if should_execute:
        for call in function_calls:
            if not isinstance(call, dict):
                continue
            call_args = call.get("arguments", {}) if isinstance(call.get("arguments"), dict) else {}
            if str(call.get("name", "")).strip() in {"send_email", "send_message", "post_slack"}:
                call_args = {**call_args, "send_now": True}
            action_results.append(
                actions.execute_action(
                    str(call.get("name", "")),
                    call_args,
                    profile,
                )
            )
        action_plan["status"] = "executed"
    elif requires_confirmation and approved:
        action_plan["status"] = "ready_to_send"
    elif requires_confirmation and not approved:
        action_plan["status"] = "awaiting_user_confirmation"

    _merge_action_outputs(outputs, action_results, function_calls)
    outputs = _synthesize_outputs_from_calls(outputs, function_calls, transcript_text, profile)
    outputs = _finalize_outputs(outputs, transcript_text)
    outputs = _replace_placeholder_recipients(outputs, str(action_plan.get("recipient", "")))
    outputs = {
        app: _apply_corrections(_canonicalize_terms(value, profile, allow_fuzzy=False), profile)
        for app, value in outputs.items()
    }
    execution_result = _summarize_execution_results(action_results, action_plan, approved, execute)

    response_body = {
        "transcript": transcript_text,
        "intent": intent_text,
        "routing": {
            "source": routing_result.get("source", "local"),
            "confidence": float(routing_result.get("confidence", 0.0) or 0.0),
            "label": routing_result.get("routing_label", "⚠️ Routing unavailable"),
        },
        "action_plan": action_plan,
        "execution_result": execution_result,
        "outputs": outputs,
        "actions": action_results,
        "function_calls": function_calls,
    }
    if stt_result is not None:
        response_body["stt"] = stt_result
    return response_body


if compose_bp is not None:
    @compose_bp.post("/api/compose")
    def compose() -> Any:
        return jsonify(compose_payload(request.get_json(silent=True) or {}))


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print(json.dumps({"error": "usage: python compose_route.py <payload.json>"}))
        raise SystemExit(1)

    payload_path = Path(sys.argv[1])
    try:
        payload = json.loads(payload_path.read_text())
        print(json.dumps(compose_payload(payload)))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(1)
