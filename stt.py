from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

from brain import load_passport, reconcile_stt

try:
    from src.cactus import cactus_init, cactus_transcribe, cactus_destroy
    from src.downloads import ensure_model
    CACTUS_AVAILABLE = True
except ImportError:
    CACTUS_AVAILABLE = False


PARAKEET_MODEL = "nvidia/parakeet-ctc-1.1b"
WHISPER_MODEL = "openai/whisper-small"
VOCAB_BOOST = 0.5

_parakeet = None
_whisper = None


def load_model(name: str):
    if not CACTUS_AVAILABLE:
        raise RuntimeError("Cactus Python SDK not available")
    return cactus_init(str(ensure_model(name)), None, False)


def parakeet():
    global _parakeet
    if _parakeet is None:
        _parakeet = load_model(PARAKEET_MODEL)
    return _parakeet


def whisper():
    global _whisper
    if _whisper is None:
        _whisper = load_model(WHISPER_MODEL)
    return _whisper


def cleanup():
    global _parakeet, _whisper
    if _parakeet is not None:
        cactus_destroy(_parakeet)
        _parakeet = None
    if _whisper is not None:
        cactus_destroy(_whisper)
        _whisper = None


def clean(raw: str) -> str:
    text = raw.strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
            for key in ("transcription", "text", "response"):
                if isinstance(payload.get(key), str):
                    return payload[key].strip()
        except json.JSONDecodeError:
            pass
    return text


def transcribe(audio_path: str) -> dict:
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(audio_path)
    passport = load_passport()
    terms = [term.text for term in passport.terms]
    options = json.dumps({"custom_vocabulary": terms, "vocabulary_boost": VOCAB_BOOST})

    p = clean(cactus_transcribe(parakeet(), str(path), None, json.dumps({}), None, None))
    w1 = clean(cactus_transcribe(whisper(), str(path), None, options, None, None))
    w2 = clean(cactus_transcribe(whisper(), str(path), w1 or None, options, None, None))
    final = reconcile_stt(p, w1, w2, passport)
    return {
        "parakeet": p,
        "whisper_pass1": w1,
        "whisper_pass2": w2,
        "final": final,
        "terms_used": terms,
    }


def accuracy(expected: str, actual: str) -> int:
    a = re.findall(r"[a-z0-9']+", expected.lower())
    b = re.findall(r"[a-z0-9']+", actual.lower())
    if not a:
        return 0
    return round(difflib.SequenceMatcher(a=a, b=b).ratio() * 100)


def benchmark(audio_path: str, expected: str) -> dict:
    passport = load_passport()
    result = transcribe(audio_path)
    return {
        "accuracy_before": accuracy(expected, result["parakeet"]),
        "accuracy_after": accuracy(expected, result["final"]),
        "patterns_learned": len(passport.corrections),
        "stt_wired": True,
        "expected_text": expected,
        **result,
    }


def transcript_only(audio_path: str) -> str:
    return transcribe(audio_path)["final"]


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python stt.py transcribe <audio> | benchmark <audio> <expected> | transcript <audio>")
        raise SystemExit(1)
    cmd = sys.argv[1]
    try:
        if cmd == "transcribe":
            print(json.dumps(transcribe(sys.argv[2])))
        elif cmd == "transcript":
            print(transcript_only(sys.argv[2]))
        elif cmd == "benchmark":
            expected = sys.argv[3] if len(sys.argv) >= 4 else ""
            print(json.dumps(benchmark(sys.argv[2], expected)))
        else:
            raise RuntimeError(f"unknown command: {cmd}")
    finally:
        cleanup()
