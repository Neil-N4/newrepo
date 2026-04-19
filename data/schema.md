# Voice Right Data Schema

JSON-based storage. No DB. Every file is human-readable.

## `data/profiles/{name}.voicepassport.json`

The moat. Portable profile file the user owns. Can be exported and re-imported.

```json
{
  "version": "1.0",
  "name": "default",
  "created": "2026-04-18T14:00:00Z",
  "updated": "2026-04-18T14:00:00Z",
  "language": "en",

  "terms": [
    {
      "id": "t_01",
      "text": "YBuffet",
      "source": "screenshot" | "manual" | "correction",
      "added": "2026-04-18T14:00:00Z"
    }
  ],

  "corrections": [
    {
      "id": "c_01",
      "wrong": "ypefa",
      "right": "YBuffet",
      "confidence": 0.9,
      "uses": 4,
      "last_applied": "2026-04-18T14:00:00Z"
    }
  ],

  "style_per_app": {
    "gmail": {
      "tone": "professional",
      "sample_size": 3,
      "last_learned": "2026-04-18T14:00:00Z"
    },
    "imessage": {
      "tone": "casual+emoji",
      "sample_size": 2,
      "last_learned": "2026-04-18T14:00:00Z"
    }
  },

  "calibration": {
    "last_run_id": "cal_20260418_140000",
    "accuracy_before": 0.82,
    "accuracy_after": 0.97
  }
}
```

## `data/calibration/{cal_id}.json`

One file per calibration run. Not user-facing — debug/analysis only. Gitignored.

```json
{
  "id": "cal_20260418_140000",
  "profile": "default",
  "timestamp": "2026-04-18T14:00:00Z",
  "script": [
    "Sentence one using YBuffet.",
    "The Deal Table launched today."
  ],
  "results": [
    {
      "sentence_idx": 0,
      "expected": "Sentence one using YBuffet.",
      "parakeet": "sentence one using ypefa",
      "whisper_pass1": "sentence one using y buffet",
      "whisper_pass2": "sentence one using YBuffet",
      "final": "Sentence one using YBuffet.",
      "accuracy": 1.0
    }
  ]
}
```

## `data/recordings/{timestamp}.wav`

Raw audio recordings. Gitignored. Clean up after demo.

## Loading in code

Python (Cactus brain + STT):
```python
import json
with open("data/profiles/default.voicepassport.json") as f:
    profile = json.load(f)
```

TypeScript (Bun server):
```ts
const profile = await Bun.file("data/profiles/default.voicepassport.json").json();
```
