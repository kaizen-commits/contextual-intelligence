# Contextual Intelligence

Windows-native, local-first contextual lookup: select a word anywhere in
Windows, trigger a hotkey, and get a compact dictionary card explaining the
word *as used in that context* — answered by a local model via LM Studio.
Smart Paste (an Advanced-Paste-style clipboard transformer) is integrated for paragraph-level transformation and rewriting.

- **Project knowledge:** longer design notes are maintained in a local LLM Wiki / knowledge base; this repository keeps the public-facing implementation docs.
- **Project rules:** [`PROJECT_RULES.md`](PROJECT_RULES.md) — canonical project-specific agent, QA, and graceful degradation rules. Tool-specific files such as `GEMINI.md`, `AGENTS.md`, or `CLAUDE.md`, if added, should point back there.
- **Manual QA:** [`docs/qa/manual-regression.md`](docs/qa/manual-regression.md) — repeatable smoke, coexistence, clipboard, placement, failure-state, and graceful degradation checks.
- **Task tracking:** work is managed in an issue tracker with acceptance criteria and QA evidence.
- **Status:** Phase 2 (Smart Paste MVP) & Phase 3 (Robustness & Graceful Degradation) Complete; Phase 4 (Speech Input / Voice-to-Transform) planned.

## What it does

- Explains selected words or short phrases in the context where they appear.
- Transforms copied text through a preview-first Smart Paste palette.
- Keeps clipboard mutation explicit: transformed text is copied only when the user clicks Copy.
- Uses local model serving by default through LM Studio's OpenAI-compatible API.
- Treats unsupported or failed paths as product states that need clear user guidance, not raw internal errors.

## What it does not do

- It does not run under WSL; the app depends on Windows hotkeys, clipboard, UI Automation, and Qt UI behavior.
- It does not automatically paste transformed text back into the source application.
- It does not include a cloud fallback by default.
- It is not a general screen reader or OCR tool.

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

```text
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

## Development credits

Contextual Intelligence is designed and maintained by Kaizen, with assistance from AI coding and reasoning tools:

- Hermes / GPT-5.5 — project orchestration, architecture review, QA planning, documentation, and implementation support
- Claude Fable 5 — implementation and code review assistance
- ChatGPT — design exploration and product reasoning
- Gemini / Antigravity — implementation planning, review, and alternative design suggestions

Final product decisions, testing, integration, and release responsibility remain with the human maintainer.

## License

MIT. See [`LICENSE`](LICENSE).
