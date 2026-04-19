"""
brain.py — Voice Right's Gemma + profile logic.

Responsibilities:
- Load/save a profile-scoped .voicepassport file
- Extract vocabulary from screenshots and imported text
- Generate calibration scripts from profile memory
- Reconcile STT candidates using profile memory
- Infer intent and create multi-target outputs
- Rewrite text for a specific app using the active profile
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from src.cactus import cactus_complete, cactus_destroy, cactus_init
    from src.downloads import ensure_model

    CACTUS_AVAILABLE = True
except ImportError:
    CACTUS_AVAILABLE = False


BASE_DIR = Path(__file__).resolve().parent
PROFILES_DIR = BASE_DIR / "data" / "profiles"
PROFILE_ENV = "VOICE_RIGHT_PROFILE_PATH"
PREFERENCES_PATH = BASE_DIR / "voice-right.md"
GEMMA_4_E4B = "google/gemma-4-E4B-it"
GEMMA_3_1B = "google/gemma-3-1b-it"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def slugify(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return cleaned or "default"


def profile_path_from_name(name: str) -> Path:
    return PROFILES_DIR / f"{slugify(name)}.voicepassport.json"


def current_profile_path() -> Path:
    env = os.environ.get(PROFILE_ENV)
    if env:
        return Path(env)
    return profile_path_from_name("default")


def current_profile_name() -> str:
    return current_profile_path().stem.replace(".voicepassport", "")


@dataclass
class Term:
    id: str
    text: str
    source: str
    added: str


@dataclass
class Correction:
    id: str
    wrong: str
    right: str
    confidence: float
    uses: int
    last_applied: str


@dataclass
class AppStyle:
    tone: str
    sample_size: int
    last_learned: str


@dataclass
class Calibration:
    last_run_id: str | None = None
    accuracy_before: float | None = None
    accuracy_after: float | None = None


@dataclass
class VoicePassport:
    version: str = "2.0"
    id: str = "default"
    name: str = "Profile"
    created: str = ""
    updated: str = ""
    language: str = "en"
    terms: list[Term] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    style_per_app: dict[str, AppStyle] = field(default_factory=dict)
    writing_samples: list[dict[str, Any]] = field(default_factory=list)
    reference_sources: list[dict[str, Any]] = field(default_factory=list)
    linked_accounts: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    calibration: Calibration = field(default_factory=Calibration)


def new_passport(name: str = "Profile") -> VoicePassport:
    ts = now_iso()
    profile_id = slugify(name)
    return VoicePassport(id=profile_id, name=name, created=ts, updated=ts)


def _term_from_raw(item: Any) -> Term | None:
    if isinstance(item, dict):
        text = str(item.get("text", "")).strip()
        if not text:
            return None
        return Term(
            id=str(item.get("id") or f"term_{uuid.uuid4().hex[:8]}"),
            text=text,
            source=str(item.get("source") or "import"),
            added=str(item.get("added") or now_iso()),
        )
    text = str(item or "").strip()
    if not text:
        return None
    return Term(
        id=f"term_{uuid.uuid4().hex[:8]}",
        text=text,
        source="import",
        added=now_iso(),
    )


def _correction_from_raw(item: Any) -> Correction | None:
    if not isinstance(item, dict):
        return None
    wrong = str(item.get("wrong", "")).strip()
    right = str(item.get("right", "")).strip()
    if not wrong or not right:
        return None
    return Correction(
        id=str(item.get("id") or f"correction_{uuid.uuid4().hex[:8]}"),
        wrong=wrong,
        right=right,
        confidence=float(item.get("confidence", 1.0) or 1.0),
        uses=int(item.get("uses", 1) or 1),
        last_applied=str(item.get("last_applied") or now_iso()),
    )


def load_passport(path: Path | None = None) -> VoicePassport:
    path = path or current_profile_path()
    if not path.exists():
        passport = new_passport(path.stem.replace(".voicepassport", ""))
        save_passport(passport, path)
        return passport

    raw = json.loads(path.read_text())
    raw_terms = raw.get("terms", [])
    normalized_terms = []
    for item in raw_terms if isinstance(raw_terms, list) else []:
        parsed = _term_from_raw(item)
        if parsed:
            normalized_terms.append(parsed)

    raw_corrections = raw.get("corrections", [])
    normalized_corrections = []
    for item in raw_corrections if isinstance(raw_corrections, list) else []:
        parsed = _correction_from_raw(item)
        if parsed:
            normalized_corrections.append(parsed)

    return VoicePassport(
        version=raw.get("version", "2.0"),
        id=raw.get("id", path.stem.replace(".voicepassport", "")),
        name=raw.get("name", path.stem.replace(".voicepassport", "")),
        created=raw.get("created", now_iso()),
        updated=raw.get("updated", now_iso()),
        language=raw.get("language", "en"),
        terms=normalized_terms,
        corrections=normalized_corrections,
        people=list(raw.get("people", [])),
        style_per_app={k: AppStyle(**v) for k, v in raw.get("style_per_app", {}).items()},
        writing_samples=list(raw.get("writing_samples", [])),
        reference_sources=list(raw.get("reference_sources", [])),
        linked_accounts=list(raw.get("linked_accounts", [])),
        history=list(raw.get("history", [])),
        calibration=Calibration(**raw.get("calibration", {})),
    )


def save_passport(passport: VoicePassport, path: Path | None = None) -> None:
    path = path or current_profile_path()
    passport.updated = now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(passport), indent=2))


def list_profiles() -> list[dict[str, Any]]:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for file in sorted(PROFILES_DIR.glob("*.voicepassport.json")):
        try:
            passport = load_passport(file)
            out.append(profile_summary(passport))
        except Exception:
            continue
    if not out:
        passport = new_passport("Profile")
        save_passport(passport, profile_path_from_name(passport.name))
        out.append(profile_summary(passport))
    return out


def profile_summary(passport: VoicePassport) -> dict[str, Any]:
    return {
        "id": passport.id,
        "name": passport.name,
        "terms": len(passport.terms),
        "corrections": len(passport.corrections),
        "apps": len(passport.style_per_app),
        "sources": len(passport.reference_sources),
        "people": len(passport.people),
        "path": str(profile_path_from_name(passport.id)),
    }


def load_preferences_md(path: Path = PREFERENCES_PATH) -> str:
    return path.read_text() if path.exists() else ""


_model_handle: int | None = None
_light_handle: int | None = None


def load_gemma() -> int:
    global _model_handle
    if not CACTUS_AVAILABLE:
        raise RuntimeError("Cactus Python SDK not available")
    if _model_handle is None:
        _model_handle = cactus_init(str(ensure_model(GEMMA_4_E4B)), None, False)
    return _model_handle


def load_gemma_light() -> int:
    global _light_handle
    if not CACTUS_AVAILABLE:
        raise RuntimeError("Cactus Python SDK not available")
    if _light_handle is None:
        _light_handle = cactus_init(str(ensure_model(GEMMA_3_1B)), None, False)
    return _light_handle


def unload_gemma() -> None:
    global _model_handle, _light_handle
    if CACTUS_AVAILABLE:
        if _model_handle is not None:
            cactus_destroy(_model_handle)
            _model_handle = None
        if _light_handle is not None:
            cactus_destroy(_light_handle)
            _light_handle = None


def unique_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        cleaned = item.strip()
        if not cleaned:
            continue
        low = cleaned.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(cleaned)
    return out


def add_terms(passport: VoicePassport, terms: list[str], source: str) -> list[Term]:
    existing = {term.text.lower() for term in passport.terms}
    added: list[Term] = []
    for text in unique_preserve(terms):
        if text.lower() in existing:
            continue
        term = Term(
            id=f"term_{uuid.uuid4().hex[:8]}",
            text=text,
            source=source,
            added=now_iso(),
        )
        passport.terms.append(term)
        added.append(term)
        existing.add(text.lower())
    return added


def extract_terms_from_screenshot(image_path: str) -> list[str]:
    if not CACTUS_AVAILABLE:
        raise RuntimeError("Cactus unavailable for screenshot extraction")
    model = load_gemma()
    messages = json.dumps(
        [
            {
                "role": "system",
                "content": (
                    "Extract only visible proper nouns, product names, companies, people names, "
                    "domain terms, acronyms, and recurring phrases from this screenshot. "
                    "Output one item per line. No bullets. No explanations. Do not hallucinate."
                ),
            },
            {"role": "user", "content": "Extract vocabulary from this screenshot.", "images": [str(image_path)]},
        ]
    )
    options = json.dumps({"max_tokens": 400, "temperature": 0.1})
    result = json.loads(cactus_complete(model, messages, options, None, None))
    if not result.get("success"):
        raise RuntimeError(result.get("error") or "Gemma screenshot extraction failed")
    items = []
    for line in result.get("response", "").splitlines():
        cleaned = line.strip().strip("-•*·").strip()
        if len(cleaned) >= 2:
            items.append(cleaned)
    return unique_preserve(items)


def extract_terms_from_text(content: str) -> list[str]:
    if not content.strip():
        return []

    terms: list[str] = []
    if CACTUS_AVAILABLE:
        try:
            model = load_gemma_light()
            messages = json.dumps(
                [
                    {
                        "role": "system",
                        "content": (
                            "Extract only useful reusable memory from this text: people names, company names, "
                            "product names, acronyms, project names, and uncommon domain vocabulary. "
                            "Output one item per line with no explanation."
                        ),
                    },
                    {"role": "user", "content": content[:12000]},
                ]
            )
            options = json.dumps({"max_tokens": 350, "temperature": 0.1})
            result = json.loads(cactus_complete(model, messages, options, None, None))
            if result.get("success"):
                terms.extend(
                    line.strip().strip("-•*·").strip()
                    for line in result.get("response", "").splitlines()
                    if line.strip()
                )
        except Exception:
            pass

    if not terms:
        caps = re.findall(r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}|[A-Z]{2,}(?:-[A-Z0-9]+)?)\b", content)
        emails = [part.split("@")[0] for part in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", content)]
        terms.extend(caps + emails)
    cleaned_terms = []
    for term in terms:
        cleaned = term.strip(" .,:;!?")
        if len(cleaned.split()) > 4:
            continue
        if re.search(r"\b(?:email|message|tell|signed|document|live)\b", cleaned, re.I) and len(cleaned.split()) > 2:
            continue
        cleaned_terms.append(cleaned)
    cleaned_terms = unique_preserve(cleaned_terms[:40])
    if cleaned_terms:
        return cleaned_terms

    fallback = re.findall(r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?|[A-Z][A-Za-z0-9]+[A-Z][A-Za-z0-9]*)\b", content)
    cleaned = []
    for item in fallback:
        if item.lower() in {"email", "message", "tell"}:
            continue
        if item.lower().startswith("email "):
            item = item.split(" ", 1)[1]
        cleaned.append(item)
    return unique_preserve(cleaned[:40])


def extract_people(content: str) -> list[str]:
    candidates = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", content)
    blocked = {"Email", "Message", "Tell"}
    filtered = [item for item in candidates if item.split()[0] not in blocked]
    return unique_preserve(filtered[:20])


def _learn_style_from_text(passport: VoicePassport, content: str, source_type: str) -> None:
    text = str(content or "").strip()
    if not text:
        return

    lowered = text.lower()
    has_emoji = bool(re.search(r"[\U0001F300-\U0001FAFF]", text))
    app_key = ""
    if source_type in {"gmail", "email", "eml"}:
        app_key = "email"
    elif source_type in {"message", "sms", "imessage"}:
        app_key = "message"

    if app_key:
        inferred_tone = "professional" if any(marker in lowered for marker in ("let me know", "thank you", "best,", "regards", "sincerely")) else "casual"
        if has_emoji and app_key == "message":
            inferred_tone = "casual"
        existing = passport.style_per_app.get(app_key)
        passport.style_per_app[app_key] = AppStyle(
            tone=inferred_tone,
            sample_size=(existing.sample_size if existing else 0) + 1,
            last_learned=now_iso(),
        )

    signoff_match = re.search(
        r"\n(?:best|thanks|regards|sincerely)[,\s]*\n+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if signoff_match and passport.name.lower() in {"default", "profile", "sender"}:
        signer = signoff_match.group(1).strip()
        if signer:
            passport.name = signer


def import_reference_content(
    *,
    passport: VoicePassport,
    source_name: str,
    source_type: str,
    content: str = "",
    image_path: str | None = None,
) -> dict[str, Any]:
    if image_path:
        terms = extract_terms_from_screenshot(image_path)
        summary = f"Imported screenshot {source_name}"
    else:
        terms = extract_terms_from_text(content)
        summary = f"Imported {source_type} source {source_name}"
        if content.strip():
            passport.writing_samples.append(
                {
                    "id": f"sample_{uuid.uuid4().hex[:8]}",
                    "source": source_name,
                    "type": source_type,
                    "excerpt": content[:1000],
                    "added": now_iso(),
                }
            )
            _learn_style_from_text(passport, content, source_type)

    added_terms = add_terms(passport, terms, "import")
    people = extract_people(content if content else " ".join(terms))
    passport.people = unique_preserve(passport.people + people)
    passport.reference_sources.append(
        {
            "id": f"source_{uuid.uuid4().hex[:8]}",
            "name": source_name,
            "type": source_type,
            "added": now_iso(),
            "terms_added": len(added_terms),
        }
    )
    passport.history.append({"ts": now_iso(), "event": "import", "summary": summary})
    return {
        "terms": [term.text for term in added_terms],
        "people": people,
        "summary": summary,
    }


def generate_calibration_script(terms: list[str], n_sentences: int = 5) -> list[str]:
    if not terms:
        return [
            "Voice Right listens for the words that matter.",
            "My writing style should match the selected app.",
            "This profile stores memory for future sessions.",
            "The transcript improves when context is available.",
            "Local inference keeps the audio on this device.",
        ][:n_sentences]

    if CACTUS_AVAILABLE:
        try:
            model = load_gemma_light()
            prompt = (
                f"Write {n_sentences} short spoken calibration lines using these terms: "
                f"{', '.join(terms[:15])}. Keep each under 14 words. One line each."
            )
            result = json.loads(
                cactus_complete(
                    model,
                    json.dumps([{"role": "user", "content": prompt}]),
                    json.dumps({"max_tokens": 240, "temperature": 0.6}),
                    None,
                    None,
                )
            )
            if result.get("success"):
                lines = [line.strip() for line in result.get("response", "").splitlines() if line.strip()]
                lines = [line.lstrip("0123456789.-) ").strip() for line in lines]
                lines = [line for line in lines if any(term.lower() in line.lower() for term in terms)]
                if lines:
                    return lines[:n_sentences]
        except Exception:
            pass

    out = []
    for term in terms[:n_sentences]:
        out.append(f"I am following up about {term}.")
    return out


def _apply_corrections(text: str, passport: VoicePassport) -> str:
    updated = text
    for correction in sorted(passport.corrections, key=lambda c: len(c.wrong), reverse=True):
        updated = re.sub(rf"(?i)\b{re.escape(correction.wrong)}\b", correction.right, updated)
    return updated


def _score_candidate(text: str, terms: list[str]) -> float:
    score = float(len(re.findall(r"[a-z0-9']+", text.lower())))
    lowered = text.lower()
    for term in terms:
        if term.lower() in lowered:
            score += 5.0
    return score


def reconcile_stt(parakeet: str, whisper_pass1: str, whisper_pass2: str, passport: VoicePassport) -> str:
    candidates = {
        "parakeet": parakeet or "",
        "whisper_pass1": whisper_pass1 or "",
        "whisper_pass2": whisper_pass2 or "",
    }
    nonempty = {k: v for k, v in candidates.items() if v.strip()}
    if not nonempty:
        return ""

    best_key = max(nonempty, key=lambda key: _score_candidate(nonempty[key], [term.text for term in passport.terms]) + (2 if key == "whisper_pass2" else 0))
    heuristic = _apply_corrections(nonempty[best_key], passport)

    if CACTUS_AVAILABLE:
        try:
            model = load_gemma()
            result = json.loads(
                cactus_complete(
                    model,
                    json.dumps(
                        [
                            {
                                "role": "system",
                                "content": (
                                    "Choose the best transcript candidate. Preserve user-specific terms and names. "
                                    "Return only the final transcript."
                                ),
                            },
                            {
                                "role": "user",
                                "content": json.dumps(
                                    {
                                        "terms": [term.text for term in passport.terms],
                                        "corrections": [{"wrong": c.wrong, "right": c.right} for c in passport.corrections],
                                        "candidates": {**candidates, "heuristic_pick": heuristic},
                                    }
                                ),
                            },
                        ]
                    ),
                    json.dumps({"max_tokens": 160, "temperature": 0.1}),
                    None,
                    None,
                )
            )
            if result.get("success") and result.get("response", "").strip():
                return result["response"].strip()
        except Exception:
            pass

    return heuristic


def capture_correction(original: str, edited: str, passport: VoicePassport) -> list[Correction]:
    import difflib

    new_items: list[Correction] = []
    now = now_iso()
    original_words = original.split()
    edited_words = edited.split()

    def is_safe_pair(wrong: str, right: str) -> bool:
        if not wrong or not right:
            return False
        if wrong.lower() == right.lower():
            return False
        if len(wrong) > 80 or len(right) > 80:
            return False
        if len(wrong.split()) > 8 or len(right.split()) > 8:
            return False
        return True

    def upsert_pair(wrong: str, right: str) -> Correction | None:
        if not is_safe_pair(wrong, right):
            return None
        existing = next((item for item in passport.corrections if item.wrong == wrong and item.right == right), None)
        if existing:
            existing.uses += 1
            existing.last_applied = now
            return existing
        item = Correction(
            id=f"correction_{uuid.uuid4().hex[:8]}",
            wrong=wrong,
            right=right,
            confidence=1.0,
            uses=1,
            last_applied=now,
        )
        passport.corrections.append(item)
        return item

    full_wrong = original.strip()
    full_right = edited.strip()
    if is_safe_pair(full_wrong, full_right) and " " in full_wrong and " " in full_right:
        item = upsert_pair(full_wrong, full_right)
        if item:
            new_items.append(item)
            return new_items

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=original.split(), b=edited.split()).get_opcodes():
        if tag not in {"replace", "insert", "delete"}:
            continue
        wrong = " ".join(original_words[i1:i2]).strip()
        right = " ".join(edited_words[j1:j2]).strip()
        item = upsert_pair(wrong, right)
        if item:
            new_items.append(item)
    if not new_items and is_safe_pair(original.strip(), edited.strip()):
        item = upsert_pair(original.strip(), edited.strip())
        if item:
            new_items.append(item)
    return new_items


def infer_intent(transcript: str, passport: VoicePassport) -> str:
    cleaned = transcript.strip()
    if not cleaned:
        return ""
    if CACTUS_AVAILABLE:
        try:
            model = load_gemma_light()
            result = json.loads(
                cactus_complete(
                    model,
                    json.dumps(
                        [
                            {
                                "role": "system",
                                "content": (
                                    "Summarize the user's communicative intent in one sentence. "
                                    "Mention the recipient if obvious. No bullets."
                                ),
                            },
                            {
                                "role": "user",
                                "content": json.dumps(
                                    {
                                        "transcript": cleaned,
                                        "known_people": passport.people,
                                        "known_terms": [term.text for term in passport.terms[:30]],
                                    }
                                ),
                            },
                        ]
                    ),
                    json.dumps({"max_tokens": 90, "temperature": 0.2}),
                    None,
                    None,
                )
            )
            if result.get("success") and result.get("response", "").strip():
                return result["response"].strip()
        except Exception:
            pass
    return cleaned


def style_transfer(text: str, target_app: str, passport: VoicePassport, preferences_md: str = "") -> str:
    if not text.strip():
        return ""
    style_hint = passport.style_per_app.get(target_app)
    if CACTUS_AVAILABLE:
        try:
            model = load_gemma()
            system = (
                "Rewrite the message so it sounds like the user wrote it in the target app. "
                "Keep the core meaning. Output only the final message."
            )
            payload = {
                "target_app": target_app,
                "style_hint": asdict(style_hint) if style_hint else None,
                "preferences": preferences_md[:2000],
                "people": passport.people[:20],
                "message": text,
            }
            result = json.loads(
                cactus_complete(
                    model,
                    json.dumps([{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload)}]),
                    json.dumps({"max_tokens": 240, "temperature": 0.45}),
                    None,
                    None,
                )
            )
            if result.get("success") and result.get("response", "").strip():
                return result["response"].strip()
        except Exception:
            pass
    return _fallback_style_transfer(text, target_app)


def _extract_recipient_name(text: str, passport: VoicePassport) -> str | None:
    match = re.search(r"\b(?:tell|email|message|text)\s+(?:my\s+\w+\s+)?([A-Za-z]+)\b", text, re.I)
    if match:
        return match.group(1).capitalize()
    lowered = text.lower()
    for person in passport.people:
        first = person.split()[0]
        if first.lower() in lowered:
            return first
    return None


def _message_body(text: str) -> str:
    body = re.sub(r"(?i)^\s*(tell|email|message|text)\s+(my\s+\w+\s+)?[A-Z][a-z]+\s+", "", text).strip()
    body = re.sub(r"(?i)^that\s+", "", body).strip()
    if not body:
        body = text.strip()
    body = body[0].upper() + body[1:] if body else body
    if body and not body.endswith((".", "!", "?")):
        body += "."
    return body


def _fallback_style_transfer(text: str, target_app: str) -> str:
    app = target_app.lower()
    body = _message_body(text)
    name = None
    match = re.search(r"\b(?:tell|email|message|text)\s+(?:my\s+\w+\s+)?([A-Z][a-z]+)\b", text)
    if match:
        name = match.group(1)

    if app in {"message", "imessage", "sms"}:
        prefix = f"Hey {name}, " if name else ""
        return f"{prefix}{body}"
    if app in {"email", "gmail"}:
        greeting = f"Hi {name}," if name else "Hi,"
        signoff = os.environ.get("VOICE_RIGHT_SIGNOFF", "Sender")
        return f"{greeting}\n\n{body} Let me know if you have any questions.\n\nBest,\n{signoff}"
    if app == "slack":
        return body
    if app == "discord":
        return body.rstrip(".")
    return body


def generate_target_output(transcript: str, target_app: str, passport: VoicePassport, preferences_md: str = "") -> str:
    if not transcript.strip():
        return ""
    recipient = _extract_recipient_name(transcript, passport)
    if target_app.lower() in {"email", "gmail", "message", "imessage", "sms"}:
        return _fallback_target_output(transcript, target_app, recipient, passport)
    if CACTUS_AVAILABLE:
        try:
            model = load_gemma()
            payload = {
                "target_app": target_app,
                "recipient": recipient,
                "profile_name": passport.name,
                "preferences": preferences_md[:2000],
                "message": transcript,
                "instruction": (
                    "If target_app is email, produce a polished email with greeting, body, and sign-off. "
                    "If target_app is message, produce a warm text message. "
                    "If target_app is slack or discord, keep it shorter and conversational."
                ),
            }
            result = json.loads(
                cactus_complete(
                    model,
                    json.dumps(
                        [
                            {
                                "role": "system",
                                "content": "Generate the final user-facing communication for the target app. Output only the message.",
                            },
                            {"role": "user", "content": json.dumps(payload)},
                        ]
                    ),
                    json.dumps({"max_tokens": 280, "temperature": 0.35}),
                    None,
                    None,
                )
            )
            if result.get("success") and result.get("response", "").strip():
                return result["response"].strip()
        except Exception:
            pass
    return _fallback_target_output(transcript, target_app, recipient, passport)


def _fallback_target_output(transcript: str, target_app: str, recipient: str | None, passport: VoicePassport) -> str:
    body = _message_body(transcript)
    first_name = recipient or passport.name
    signoff = passport.name if passport.name.lower() not in {"default", "profile"} else "Sender"
    app = target_app.lower()
    if app in {"email", "gmail"}:
        greeting = f"Hi {first_name}," if recipient else "Hi,"
        return f"{greeting}\n\n{body} Let me know if you have any questions. Thank you.\n\nBest,\n{signoff}"
    if app in {"message", "imessage", "sms"}:
        if recipient:
            return f"Hey {first_name}, {body}"
        return body
    if app == "slack":
        return body
    if app == "discord":
        return body.rstrip(".")
    return body


def compose_outputs(transcript: str, targets: list[str], passport: VoicePassport, preferences_md: str = "") -> dict[str, Any]:
    transcript = transcript.strip()
    intent = infer_intent(transcript, passport)
    outputs = {}
    for target in targets:
        outputs[target] = generate_target_output(transcript, target, passport, preferences_md)
    passport.history.append(
        {
            "ts": now_iso(),
            "event": "compose",
            "targets": targets,
            "transcript": transcript,
            "intent": intent,
        }
    )
    return {"intent": intent, "outputs": outputs}


def _cli() -> None:
    if len(sys.argv) < 2:
        print("usage: brain.py [profiles|create-profile|terms|import|calibrate|style|compose|correction|info]")
        return

    cmd = sys.argv[1]
    try:
        if cmd == "profiles":
            print(json.dumps(list_profiles()))
        elif cmd == "create-profile":
            name = sys.argv[2] if len(sys.argv) >= 3 else "Profile"
            passport = new_passport(name)
            save_passport(passport, profile_path_from_name(name))
            print(json.dumps(profile_summary(passport)))
        elif cmd == "terms":
            image_path = sys.argv[2]
            print(json.dumps(extract_terms_from_screenshot(image_path)))
        elif cmd == "import":
            mode = sys.argv[2]
            source_name = sys.argv[3]
            passport = load_passport()
            if mode == "image":
                result = import_reference_content(passport=passport, source_name=source_name, source_type="image", image_path=sys.argv[4])
            else:
                result = import_reference_content(passport=passport, source_name=source_name, source_type=mode, content=sys.argv[4])
            save_passport(passport)
            print(json.dumps(result))
        elif cmd == "calibrate":
            passport = load_passport()
            terms = [t.strip() for t in sys.argv[2].split(",") if t.strip()] if len(sys.argv) >= 3 else [t.text for t in passport.terms]
            print(json.dumps(generate_calibration_script(terms)))
        elif cmd == "style":
            passport = load_passport()
            print(style_transfer(sys.argv[2], sys.argv[3], passport, load_preferences_md()))
        elif cmd == "compose":
            passport = load_passport()
            targets = [item.strip() for item in sys.argv[3].split(",") if item.strip()]
            result = compose_outputs(sys.argv[2], targets, passport, load_preferences_md())
            save_passport(passport)
            print(json.dumps(result))
        elif cmd == "correction":
            passport = load_passport()
            added = capture_correction(sys.argv[2], sys.argv[3], passport)
            save_passport(passport)
            print(json.dumps([asdict(item) for item in added]))
        elif cmd == "info":
            passport = load_passport()
            print(
                json.dumps(
                    {
                        "cactus": CACTUS_AVAILABLE,
                        "profile": profile_summary(passport),
                    }
                )
            )
        else:
            raise RuntimeError(f"unknown command: {cmd}")
    finally:
        unload_gemma()


if __name__ == "__main__":
    _cli()
