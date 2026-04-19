# Voice Right — Design Document

**Last updated:** 2026-04-18 (hackathon day)
**Team:** Jenny Ruan (MSME Acoustics, Georgia Tech · founder of YBuffet · 80+ stand-up comedy shows) + Neil Nair (SWE, audio ML research, ex-JPMorgan)
**Event:** Cactus + Gemma 4 Voice Agents Hackathon at Y Combinator, April 18-19, 2026
**Target:** Main prize — winner-takes-all YC Interview + $150K GCP credits
**Tagline:** *The agent that learns you — then translates how you speak 🗣️ into how you type ⌨️.*

---

## 1. The one-sentence pitch

> **Voice Right is the agent that learns you — then translates how you speak 🗣️ into how you type ⌨️. Per app, per context, on-device, yours forever.**

It's not transcription. Not dictation. Not an AI writing tool. It's the **translation layer** between two things only you can define: how you speak, and how you'd type in whichever app you're in. The portable `.voicepassport` file IS the translator — it learns from five compounding sources (screenshots, content ingest, voice-right.md preferences, correction capture, voiceprint) and travels with you.

The "learns you" half is what makes the translation actually yours. Without it, we'd just be another voice-to-text tool. With it, every correction and every recording makes the next translation more faithful to your actual voice and your actual writing.

---

## 2. Why now

Three converging shifts make Voice Right possible in 2026 and impossible in 2025:

1. **Intelligence per watt crossed a threshold.** Gemma 4 1B roughly matches last year's 27B. Our brain (E4B, 4B effective params) pushes close to old 200B cloud quality. A year ago, voice AI this smart needed a datacenter. Today it runs in your pocket.
2. **Cactus unlocks on-device multimodal.** Vision + audio + function calling, all local, low-latency, with automatic hybrid routing to cloud when the small model struggles. Sub-150ms transcription with cloud-level accuracy.
3. **Cost re-ranks the market.** Cloud voice APIs charge per minute. Voice Right costs zero after install. Same engine on premium and budget phones. That's Cactus's mission — Voice Right is the voice use case of it.

---

## 3. The problem

**Voice should be 3-4x faster than typing.** You speak ~150 words per minute and type ~40. But voice interfaces fail on the parts that matter most: your name, your company, your jargon, your accent, your style. So you repeat yourself, correct errors, or give up and type. Every voice interface today starts from zero.

**Nobody owns this problem.** The competitive landscape:

| What exists | What's missing |
|---|---|
| Accent translation (Sanas, Krisp, Tomato) — changes speaker output | Improving the machine's UNDERSTANDING of input |
| Per-ecosystem profiles (Siri, Google, Alexa) — locked to vendor | PORTABLE profile across all apps and vendors |
| Enterprise custom models (Dragon, Deepgram) — weeks to set up | CONSUMER calibration in minutes |
| Universal models (Whisper, Speechmatics) — one-size-fits-all | PER-USER tuning on top of universal models |

Google patent `US10629192B1` describes this exact concept ("Intelligent Personalized Speech Recognition"). Granted. Never shipped. Genuine whitespace.

---

## 4. The moat — four inputs to the voice identity layer

The `.voicepassport` is a **user-owned JSON file** that captures who you are. It has four compounding inputs, all on-device:

| # | Input | How the user triggers it | What it captures |
|---|---|---|---|
| 1 | **Screenshots** | Drag-drop into Step 2 (Learn) | Visual vocabulary, brand/product names, per-app UI conventions. Gemma 4 E4B vision, ~19s per image. |
| 2 | **Content ingest** | One-time import (iMessage local SQLite, Gmail .mbox export, Slack JSON, Notion MD) | How the user actually writes at scale — tone, length, emoji usage, signature lines. |
| 3 | **`voice-right.md` preferences** | User authors / edits a markdown file in the repo | Explicit writing style, dictation habits, per-app overrides. Like `CLAUDE.md` for voice. |
| 4 | **Correction capture** | Every edit the user makes to styled output | STT error signatures specific to this user's voice + evolving vocabulary. |

**Compounding principle:** every input feeds the same file. The profile gets smarter every day. It's portable (users can export and move between devices). No vendor owns it. No cloud sync required.

> *"Every voice interface starts from zero. Voice Right starts from four sources of signal about who you are and how you speak. It compounds from there. That's the identity layer."*

---

## 5. Architecture

### 5.1 Full pipeline (voice → styled output)

```
mic input (user's device)
    │
    ▼
Parakeet CTC 1.1B ─────┐     (raw baseline — fast, no custom vocab support)
                       │
Whisper small + ───────┤     (biased by passport terms, custom_vocabulary at boost=0.5)
  custom_vocabulary    │
                       │
Whisper Pass 2 ────────┤     (iterative re-transcription: Pass 1 result fed back
  (prompt = Pass 1)    │      as initial_prompt, self-correcting)
                       │
                       ▼
              Gemma 4 E4B reconciliation     (picks best, applies passport corrections)
                       │
                       ▼
              Active app detection           (macOS System Events or FunctionGemma 270M tool call)
                       │
                       ▼
              Gemma 4 E4B style transfer     (reads voice-right.md preferences + per-app learned style)
                       │
                       ▼
              Auto-paste via clipboard        (Cmd+V into active app)
```

### 5.2 Hybrid routing (per Cactus's native mechanism)

Every Cactus response carries a `confidence` score and a `cloud_handoff: bool` flag. When the on-device model's confidence is below threshold (noisy audio, ambiguous pronunciation, unseen vocabulary), the engine automatically routes that specific call to a cloud model. User sees the cloud-cleaned result transparently — no manual routing code.

**Voice Right's posture:**
- **Voice audio never leaves the device.** Parakeet, Whisper, Gemma 4 E4B all local.
- **Only anonymized text may hybrid-route** for reconciliation if the on-device model truly falters.
- Toggled via `cactus auth` — configurable. User controls it.

### 5.3 Abhishek Datta's 4-layer STT failure framework

Voice Right addresses each failure layer explicitly:

| Layer | Failure mode | Our fix |
|---|---|---|
| 1. Acoustic | Sounds misheard (accent) | Custom_vocabulary biases the Whisper decoder at the audio level |
| 2. Lexical | Word not in model's vocabulary | Passport terms feed into custom_vocabulary |
| 3. LM priors | Word exists but rare given training | Iterative re-transcription (Pass 2 uses Pass 1 as prompt) |
| 4. Disambiguation | Two words sound identical | Gemma 4 E4B post-correction with user context |

### 5.4 Data model (JSON, human-readable, portable)

File: `data/profiles/{name}.voicepassport.json`

```json
{
  "version": "1.0",
  "name": "jenny",
  "created": "ISO-8601",
  "updated": "ISO-8601",
  "language": "en",
  "terms": [{"id", "text", "source": "screenshot|manual|correction|import", "added"}],
  "corrections": [{"id", "wrong", "right", "confidence", "uses", "last_applied"}],
  "style_per_app": {"gmail": {"tone", "sample_size", "last_learned"}, ...},
  "calibration": {"last_run_id", "accuracy_before", "accuracy_after"}
}
```

Full schema: [`data/schema.md`](data/schema.md).

---

## 6. Demo flow (2 minutes on stage)

Rehearsed, timed, with backup for every failure mode.

| Time | Moment | What judges see |
|---|---|---|
| 0:00–0:15 | **Hook** | *"You speak 150 wpm. You type 40. Voice should be 3x faster than typing. But it's not. Because voice apps don't understand you. Watch."* |
| 0:15–0:30 | **Problem** | Speak a sentence with "YBuffet" and "Deal Table." Parakeet hears "ypefa". Whisper hears "Webapp Day". Both side by side, both wrong. |
| 0:30–0:50 | **Fix — screenshot** | Drop a screenshot of Gmail into Voice Right. 19 seconds later, chips appear: "YBuffet, Deal Table, investors, Jenny Ruan." Zero typing. |
| 0:50–1:20 | **Magic — same voice, 3 apps** | Say *"so we just launched ybuffet and the deal table is live."* Click Gmail → professional email. Click iMessage → casual with 🎉. Click Discord → short, fun. Same voice, three styles. |
| 1:20–1:40 | **Audience participation** | Ask someone their name + company. Type it into a new passport. They speak, it transcribes correctly. QR code on screen → everyone scans to try it on their phone. |
| 1:40–1:55 | **On-device proof** | Disconnect WiFi visibly. Demo still works. *"Your voice never leaves this laptop. Not to Google. Not to OpenAI. Not to anyone."* |
| 1:55–2:00 | **Close** | *"Voice Right. You shouldn't have to change how you talk for a machine. Thank you."* |

**Backup plans:**
- Live recording fails → pre-recorded audio file
- Audience participation awkward → skip directly to QR
- Gemma 4 latency spikes → pre-cached outputs for demo phrases
- Ngrok fails → show mobile view on laptop

---

## 7. Rubric coverage

**Rubric 1: Relevance + realness of the problem, appeal to enterprises and VCs**
- Real pain — voice interfaces fail for 100% of users on personal vocabulary. Demoable in 15 seconds.
- Market — ambient AI scribes (Nabla, Abridge, Suki) raised $100M+ each on cloud voice. Every one is gated by HIPAA. On-device unlocks every regulated vertical.
- Global accessibility — zero per-minute cost. Works on budget phones in markets where cloud pricing is prohibitive (Henry's Nigerian case).
- Platform thesis — we're not competing with Nabla/Otter/Dragon. We're the substrate they should have built on top of.

**Rubric 2: Correctness + quality of MVP and demo**
- Four on-device Cactus models working together (Parakeet, Whisper, Gemma 4 E4B, optional FunctionGemma 270M).
- Abhishek's four STT failure layers all addressed explicitly.
- Iterative re-transcription (Whisper Pass 2 with Pass 1 as prompt) = non-obvious technical depth.
- Full pipeline runs live during demo, not scripted. Portable profile is a real file the user owns.

---

## 8. Competitive positioning

**Direct competitors: none.** Google patented this in `US10629192B1` and abandoned it.

| Adjacent player | Their bet | Why we're different |
|---|---|---|
| **Sanas, Krisp, Tomato** | Change the speaker's accent in real time | We improve the MACHINE's understanding — user speaks normally |
| **Nuance Dragon** | Personal voice profile, cloud-synced | We're portable, user-owned, not locked to one vendor's ecosystem |
| **Voiceitt** | Accessibility (non-standard speech, 200+ phrase calibration) | Consumer mainstream, minutes not hours |
| **Siri / Google Assistant / Alexa** | Per-ecosystem profiles | Locked — ours works across every app |
| **Otter, Nabla, Abridge** | Cloud-first transcription services | On-device, so your voiceprint stays yours |

---

## 9. Stack & key technical notes

### 9.1 Models (from `models.json` in Cactus)

| Model | Role | Size (INT4) | NPU | Key flag |
|---|---|---|---|---|
| `nvidia/parakeet-ctc-1.1b` | Primary STT, raw baseline | 1.7 GB | ✅ Apple | Does NOT support `custom_vocabulary` |
| `openai/whisper-small` | Secondary STT with vocab biasing | 284 MB | ✅ Apple | `custom_vocabulary` + `vocabulary_boost` |
| `google/gemma-4-E4B-it` | Brain (vision + audio + reasoning) | 8.1 GB | ✅ Apple | Native audio input via `pcm_data` param |
| `google/functiongemma-270m-it` | App detection / function routing | Small | — | Function calling optimized |
| `snakers4/silero-vad` | VAD (built into Cactus) | 1 MB | — | Use to cut silence pre-Whisper |

### 9.2 Critical gotchas (verified from Cactus docs + prototype learnings)

1. **Parakeet does NOT accept `custom_vocabulary`.** Only Whisper and Moonshine do. Parakeet = raw baseline, Whisper = vocab-biased branch.
2. **`vocabulary_boost=0.5` (prototype)** — prior memory says 0.5 is the sweet spot; Cactus docs example uses `3.0`. We test empirically, pick the winner, save to passport config.
3. **Whisper Python SDK prompt token:** `<|startoftranscript|><|en|><|transcribe|><|notimestamps|>` — CLI auto-prepends, Python SDK does NOT. Use the `prompt` param on `cactus_transcribe` for iterative re-transcription.
4. **`parakeet-tdt-0.6b-v3` returns empty transcription** — use `parakeet-ctc-1.1b` instead.
5. **Browser audio is WebM.** Convert before Cactus: `ffmpeg -i in.webm -ar 16000 -ac 1 -sample_fmt s16 out.wav`.
6. **Gemma 4 E4B has native audio input** — you can skip Whisper on some paths and feed raw audio directly to E4B via `pcm_data`. Tested future direction.
7. **Hybrid cloud handoff is automatic** — run `cactus auth` once, response has `cloud_handoff: true/false`. No manual routing logic.
8. **Speaker embedding built-in** — `pyannote/wespeaker-voxceleb-resnet34-LM` → 256-dim voiceprint. Adds true biometric ID to the passport.

### 9.3 Python SDK — verbatim signatures we use

```python
from src.cactus import cactus_init, cactus_complete, cactus_transcribe, cactus_destroy
from src.downloads import ensure_model
import json

# Load a model once, reuse handle
model = cactus_init(str(ensure_model("google/gemma-4-E4B-it")), None, False)

# Vision completion (screenshot → terms)
messages = json.dumps([
    {"role": "system", "content": "Extract ONLY visible terms. Do NOT hallucinate."},
    {"role": "user", "content": "Extract vocabulary from this screenshot.",
     "images": ["/path/to/screenshot.png"]},
])
result = json.loads(cactus_complete(model, messages, None, None, None))

# Transcription with custom vocabulary
options = json.dumps({"custom_vocabulary": ["YBuffet", "Deal Table"], "vocabulary_boost": 0.5})
result = json.loads(cactus_transcribe(model, "audio.wav", prompt=None, options_json=options, callback=None, pcm_data=None))

cactus_destroy(model)
```

Full reference at `~/.claude/projects/-Users-jennyr/memory/project_cactus_docs_reference.md`.

---

## 10. Product roadmap

**Today (hackathon):** Consumer dictation assistant with portable voice profile.

**Year 1 verticals (enterprise, compliance-friendly because on-device):**
- **Medical dictation** — HIPAA covered. MedGemma (DeepMind's specialized Gemma variant) proves the vertical-specialization path exists.
- **Legal dictation** — privileged communications can't go to third-party cloud. Portable passport + local Gemma = compliant.
- **Financial dictation** — SEC/MiFID audit trails. Same story.
- **Defense / air-gapped** — no network at all. Gemma 4 + Parakeet + Whisper all local.

**Year 2+:** Per-user fine-tuned LoRAs (micro-models personal to each user's acoustic profile). API for third-party apps to consume `.voicepassport` files as voice-context OAuth.

---

## 11. Risks + kill switches

| Risk | Kill switch |
|---|---|
| API key exposure in git | `.env` in `.gitignore`; `git status` before every `git add`; rotate immediately on leak |
| Gemma 4 E4B live latency spike | Pre-cache styled outputs for demo phrases; run from memory |
| Whisper custom_vocabulary corrupts output | Test boost=0.5 vs 3.0, pick winner before demo; cap at winner value |
| Screen-recording permission not granted | Fall back to drag-drop screenshot from user's filesystem |
| Ngrok tunnel fails | Skip audience participation, show mobile view on laptop instead |
| Live mic fails in the venue | Pre-recorded audio file; or phone-record → upload |
| Parallel Claude terminals clash on git | Main-only workflow; commit directly to `main`; `~/yc-ops` for coordination work |

---

## 12. Team

**Jenny Ruan** — Founder of YBuffet (structured communication SaaS). MSME Acoustics from Georgia Tech. 80+ stand-up comedy shows. Drives the AI infrastructure (Claude Code, gstack, memory sync). Handles product, UX, pitch delivery.

**Neil Nair** — Software engineer. Audio ML research background. Formerly at JPMorgan. Owns `stt.py` (dual STT engine) and the hackathon repo at `github.com/Neil-N4/yc-voice-agents-hackathon`. Flexible AI tooling.

**Working model:** Co-located at YC HQ. Verbal real-time comms. Jenny drives the main build machine. Neil pairs verbally + reviews commits async.

---

## 13. How to run

```bash
# One-time setup
git clone https://github.com/Neil-N4/yc-voice-agents-hackathon
cd yc-voice-agents-hackathon
cp .env.example .env   # fill in CACTUS_TOKEN and GEMINI_API_KEY
cactus build --python
cactus download google/gemma-4-E4B-it
cactus download nvidia/parakeet-ctc-1.1b
cactus download openai/whisper-small

# Start server
bun run server.ts

# Visit http://localhost:3000
```

**File layout:**
- `server.ts` — Bun server, serves `index.html`, exposes `/api/profile`, `/api/style`, `/api/screenshot-to-terms`, `/api/transcribe` (stub until `stt.py` lands)
- `index.html` — 4-step UI (Mic → Learn → Calibrate → Use), dark mode, glassmorphism
- `brain.py` — Gemma 4 E4B brain: vision, style transfer, reconciliation, calibration
- `stt.py` — Dual STT pipeline (Neil, in progress)
- `voice-right.md` — User preferences file (explicit style + per-app overrides)
- `data/profiles/*.voicepassport.json` — user-owned voice identity files (gitignored)
- `data/schema.md` — voice passport schema

---

## 14. One line to remember

> **Voice is the one interface where cloud can't win. Physics, privacy, portability — all three break the cloud model. Voice Right is what's on the other side.**
