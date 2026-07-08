# Contextual Intelligence

Windows-native, local-first contextual lookup: select a word anywhere in
Windows, trigger a hotkey, and get a compact dictionary card explaining the
word *as used in that context* — answered by a local model via LM Studio.
Smart Paste (an Advanced-Paste-style clipboard transformer) is integrated for paragraph-level transformation and rewriting.

- **Plan (source of truth):** `obsidian-vault/Kaizen/projects/contextual-intelligence/implementation_plan.md`
- **Project rules:** [`PROJECT_RULES.md`](PROJECT_RULES.md) — canonical project-specific agent, QA, and graceful degradation rules. Tool-specific files such as `GEMINI.md`, `AGENTS.md`, or `CLAUDE.md`, if added, should point back there.
- **Linear:** [Contextual Intelligence](https://linear.app/kaizen-agent/project/contextual-intelligence-086654bde189)
- **Status:** Phase 2 (Smart Paste MVP) & Phase 3 (Robustness & Graceful Degradation) Complete

## Requirements

- Windows 11 (Win32 + UI Automation; will not run under WSL)
- [uv](https://docs.astral.sh/uv/) (Python 3.12 pinned via `.python-version`)
- LM Studio serving an OpenAI-compatible API on `localhost:1234`
  (default model: `google/gemma-4-e4b` — override in
  `%APPDATA%\contextual-intelligence\config.toml`)

## Phase 0 spike

```powershell
uv run ci-lookup smoke              # LM Studio round trip + loaded model list
uv run ci-lookup capture --delay 3  # select a word anywhere, see the captured payload
uv run ci-lookup lookup --delay 3   # full loop: capture -> validate -> streamed answer
uv run ci-lookup listen             # Ctrl+Alt+D triggers lookup until Ctrl+C
uv run pytest                       # payload validation, tier orchestration, lifecycle contract
```

`--delay N` waits N seconds so you can click into another app and select a
word — the full loop is testable without hotkey plumbing.

## Layout

```
src/contextual_intelligence/
├── models.py                    ContextPayload, PastePayload, PasteResult — strict validation
├── clipboard.py                 Public text-only clipboard utility with retry backoff
├── capture/
│   ├── __init__.py              CaptureProvider protocol + tier orchestrator
│   ├── uia.py                   tier 1: UIA TextPattern (primary)
│   └── clipboard_fallback.py    tier 2: deterministic clipboard automation fallback
├── ui/
│   ├── tray.py                  QSystemTrayIcon + multi-hotkey background bridge
│   ├── popup.py                 Frameless near-cursor popup for Contextual Lookup
│   ├── palette.py               Frameless interactive palette for Smart Paste
│   ├── worker.py                LookupUIA capture & LLM streaming worker
│   ├── paste_worker.py          Smart Paste LLM streaming worker
│   └── positioning.py           Multi-monitor placement & DPI scaling fallback
├── llm.py                       LM Studio client + lookup/paste prompts & spelling inference
├── hotkey.py                    RegisterHotKey loop with multi-hotkey degradation
├── config.py                    settings + TOML override
├── log.py                       capture telemetry goes to stderr
└── cli.py                       smoke / capture / lookup / listen
```

Design rules carried from the original app's failure: capture listeners have
an explicit arm → capture → disarm lifecycle with tests proving nothing stays
armed after a cycle; every capture is validated (empty/mojibake/oversized
rejected) before it reaches the model; every attempt logs tier, duration, and
failure reason so fallback work is driven by telemetry, not guesses.
