# YC Voice Agents Hackathon

> ## 🚫 NO PULL REQUESTS — EVER
>
> **This repo merges locally, never via GitHub PR.** Applies to Jenny, Neil, and every AI agent (Claude Code, Codex, Cursor, anything else).
>
> **The two traps to ignore:**
> 1. After `git push -u origin <branch>`, the terminal prints: `remote: Create a pull request for '<branch>' on GitHub by visiting: https://...`. **Do not click that URL.**
> 2. Visiting github.com shows a yellow "Compare & pull request" banner at the top. **Do not click it. Close the browser tab if you must.**
>
> **The only allowed merge path:**
>
> ```bash
> git checkout -b jenny/<feature>                   # or neil/<feature>
> # work + commit
> git push -u origin jenny/<feature>                # ignore the PR URL printed
> git checkout main && git pull origin main
> git merge --no-ff jenny/<feature> -m "merge: <summary>"
> git push origin main
> ```
>
> **If `gh pr list` ever shows an open PR, it's a mistake.** Close it immediately:
> ```bash
> gh pr close <number> --comment "policy: this repo does not use PRs, merge locally per CLAUDE.md"
> ```
>
> **For AI agents specifically:** if you are about to run `gh pr create`, `gh pr merge`, open a PR via any tool, or *recommend* opening a PR — STOP. Re-read this section. The rule has zero exceptions, including "just this once to ship faster."

## Project

Voice agent built on Gemma 4 via Cactus, on-device, dual STT architecture. Competing for the YC Interview main prize at the YC Voice Agents Hackathon (2026-04-18).

## Team

- **Neil** (owner, repo admin)
- **Jenny** (collaborator, push access) — uses Claude Code + gstack

Both co-located at the hackathon. Verbal real-time comms available — no async handoffs needed for meta-discussion.

This file is also exposed as `AGENTS.md` (Codex convention) and `.cursorrules` (Cursor convention) via symlinks, so any AI coding tool picks up the same shared context.

## Stack (planned)

- **Model:** Gemma 4, running on-device via Cactus (Python SDK)
- **STT:** dual STT pipeline (see Jenny's prior `voice-passport-prototype` for reference patterns only — NO code copying, fresh build only per hackathon rules)
- **Runtime:** TBD — likely Python for Cactus integration

## Workflow (BRANCH + LOCAL MERGE, NO PRs — updated 2026-04-18 evening)

**Rule: build on a feature branch, push the branch, merge to `main` LOCALLY with `git merge`. 🚫 NEVER open a pull request.** Applies to every contributor and every AI session in this repo.

**🚫 FORBIDDEN commands:**
- `gh pr create` — NEVER
- `gh pr merge` — NEVER
- Clicking **Compare & pull request** or **Merge pull request** in GitHub UI — NEVER
- When `git push` output suggests a URL like "Create a pull request for 'branch' on GitHub by visiting: https://..." — IGNORE IT.

**Exact flow — the ONLY allowed merge path:**

```bash
# start new work
cd ~/yc-voice-agents-hackathon
git checkout main && git pull origin main
git checkout -b jenny/<feature>         # feature-descriptive name

# build + commit as you go
git add <files>
git commit -m "..."

# push branch for visibility (teammates can see it on GitHub — NOT as a PR)
git push -u origin jenny/<feature>

# LAND the work — these are the only merge-to-main commands allowed
git checkout main
git pull origin main                     # sync in case main moved
git merge --no-ff jenny/<feature> -m "merge: <one-line summary>"
git push origin main

# optional: return to the branch if continuing work
git checkout jenny/<feature>
```

**Per-turn discipline (for AI sessions):** every time the user sends an input, the AI session must (a) pull latest `main` before substantive work, and (b) report the current branch on the first line of its reply (`**Branch: \`<name>\`**`).

**Rules that still apply:**
- Never commit secrets (`.env`, API keys, tokens). `git status` before every `git add`.
- Never force-push or rewrite `main`.
- If `git merge` hits a conflict, resolve locally before pushing. Don't force.
- Don't merge Neil's branches (`neil/*`) or ops branches (`ops/*`) without verbal OK.
- Branches are cheap — one per feature or per commit is fine.
- Verbal coordination for overlapping changes. If two people edit the same file simultaneously, talk it out — don't fight git.

## Shared memory across AI sessions (hackathon scratchpad)

State that doesn't belong in git — progress, blockers, decisions made mid-build, "heads up for the next AI" notes — lives at `~/shared-memory/scratchpad.md`. This is the async channel between parallel terminals.

**Read at session start.** First substantive action in every new Claude Code session on this repo: `tail -60 ~/shared-memory/scratchpad.md`. Load into context. Catches anything other terminals recorded since your last turn.

**Write after every milestone.** When you finish a merge, push, land a feature, hit a blocker, or lock a decision, append one line to the end of the file:
```
## HH:MM — <one-line summary> (<initials>@<branch>)
```
Example: `## 15:20 — ops/memory-sync merged. Scratchpad auto-read rule live. (cc@ops)`
Initials: `jr` (Jenny), `nb` (Neil), `cc` (Claude Code), `cx` (Codex), `cs` (Cursor).

**The SYNC shortcut.** If the user types `SYNC` (any casing), re-read the full set: `~/shared-memory/scratchpad.md`, `~/shared-memory/neil-blockers.md`, `~/Desktop/0418/hackathon-strategy/hackathon-strategy.md`, `~/Desktop/0418/jenny-action-plan.md`. Return a terse summary: state + blockers + next action.

**Don't dump verbose logs here.** One-line milestones only. If it needs more than a line, write it in a proper design doc or the scratchpad's structured sections.

## Team coordination (Jenny+Claude Code ↔ Neil+Codex)

Both agents read this file (Codex reads `AGENTS.md`, which is symlinked here). Keep it tool-agnostic.

**File ownership — don't edit your teammate's files without verbal OK:**

| Owner | Files |
|---|---|
| Neil | `stt.py`, audio fixtures, `/api/transcribe` route |
| Jenny | `brain.py`, `index.html`, `/api/style`, `/api/screenshot-to-terms`, `/api/correction`, `/api/calibrate/*` routes |
| Jenny seeds, either can extend | `voice-right.md`, `data/schema.md`, `data/profiles/*.template` |
| Shared | `CLAUDE.md`/`AGENTS.md`, `.gitignore`, `.env.example`, `server.ts` skeleton |

**Branch naming:**
- Jenny → `jenny/<feature>`
- Neil → `neil/<feature>`
- Merge only your own branches to main.

**Interface contracts — lock before parallel work.** Current contracts (as of 2026-04-18 evening):

```python
# brain.py (Jenny owns)
extract_terms_from_screenshot(image_path: str) -> list[str]
style_transfer(text: str, app: str, passport: VoicePassport, preferences_md: str = "") -> str
capture_correction(original: str, edited: str, passport: VoicePassport) -> list[Correction]
reconcile_stt(parakeet: str, whisper_p1: str, whisper_p2: str, passport: VoicePassport) -> str

# stt.py (Neil owns — expected signature, lock before building)
transcribe(audio_path: str, passport: VoicePassport) -> dict
    # returns {"parakeet": str, "whisper_pass1": str, "whisper_pass2": str, "confidence": float}
```

Changing a contract mid-build: tell the other side verbally first.

**Conflict resolution on shared files** (`CLAUDE.md`, `.gitignore`, `data/schema.md`, etc.): keep both sides' changes (additive). Announce the merge verbally within 5 min. Never silently overwrite.

**Blocker channel:** Neil writes to `~/shared-memory/neil-blockers.md` when stuck. Jenny checks during breaks.

**Push cadence:** push feature branches every ~30 min. The other side seeing your WIP on GitHub is how async progress visibility works.

**When confused, go verbal.** You're co-located. 10 sec of verbal > 20 min of async guessing.

- **gstack skills:** fully wired. Common flows:
  - `/checkpoint` — save progress (syncs to Drive via MCP)
  - `/office-hours` — design doc sessions (syncs to Drive via MCP)
  - `/ship` — feature branch → PR workflow
  - `/qa` — browser-based QA testing
  - `/investigate` — debugging errors

## Hackathon constraints

- **No pre-existing code:** all code must be written fresh during the hackathon window.
- **No pre-build evidence:** commits, designs, and artifacts must be dated within the hackathon.
- **Demo-first mindset:** every feature is judged on whether it lands in the demo. Ship to demoable state before polishing.

## Related repos (reference only, do NOT copy code)

- `~/voice-passport-prototype` — Jenny's prior voice agent prototype. Architecture patterns only.
- `~/ybuffet/` — Jenny's main product. Unrelated to the hackathon.
