# Contextual Intelligence project rules

This file is the canonical project-specific rules registry for Contextual Intelligence.

Tool-specific files such as `CLAUDE.md`, `GEMINI.md`, or `AGENTS.md`, if they are ever added at the repository root, should be thin discovery shims that point back here rather than duplicated rulebooks. The same rule applies to project-local `.agents/` entrypoint files, which may be hidden by default on Windows but can still be tracked by Git and read by agent tools. Global/user-level agent files must stay project-agnostic.

## Product identity

Contextual Intelligence is a Windows-native, local-first assistant for two complementary workflows:

- **Contextual Lookup**: select a word or short phrase anywhere in Windows and trigger `Ctrl+Alt+D` to get a compact explanation of the term as used in context.
- **Smart Paste**: copy text and trigger `Ctrl+Alt+V` to ask for a transformation, preview the result, and explicitly copy the transformed output.

The product should feel fast, private, understandable, and recoverable.

## Non-negotiable product rules

- Keep the app local-first. Do not add cloud fallback without explicit approval.
- Preserve user control. Do not add automatic paste-back unless separately scoped and accepted.
- Do not silently mutate the clipboard. Clipboard mutation must be explicit and visible.
- Keep Lookup and Smart Paste complementary, not competing.
- Make capability boundaries clear. Silent no-response behavior is a bug unless explicitly justified.
- Use plain-language guidance for user-facing failure states; avoid internal exception wording.

## Graceful degradation standard

Every unsupported, ambiguous, failed, or edge-case path must either complete the expected action or explain the next useful action in plain language.

Default acceptance criteria for feature and bug issues:

- Unsupported, ambiguous, failed, or edge-case paths either complete the expected action or explain the next useful action in plain language.
- Raw internal errors are logged, not shown as the primary user-facing message.
- The UI distinguishes user-correctable conditions from system/service failures.
- The failure path preserves user data and clipboard state.
- The change includes at least one negative/manual QA case, not only a happy-path test.
- Any fallback behavior is explicit in docs or issue-tracker comments, including what it does not cover.

Examples:

- If Contextual Lookup receives a paragraph or oversized selection, explain that Lookup is designed for words/short phrases and point to Smart Paste for paragraph-level questions or rewriting.
- If a selected term appears misspelled and surrounding context clearly disambiguates it, infer the likely spelling visibly rather than silently replacing the user's text.
- If Smart Paste sees empty, oversized, or high-value non-text clipboard content, decline safely and explain why.
- If copying a Smart Paste result fails because the clipboard is locked, keep the result visible and show an actionable error.
- If the wrong tool is being used, guide the user to the right tool rather than failing silently.

## UX and brand rules

- Be concise, useful, and specific.
- Prefer one clear next action over generic retry advice.
- State uncertainty when behavior depends on inference.
- Do not over-explain internal architecture in user-facing UI.
- Avoid fake confidence, filler, or conversational chatter in model output.
- Preserve the selected/copied content's intent unless the user explicitly asks for rewriting.

## Clipboard and capture rules

- Lookup is selection/context-first.
- Smart Paste is clipboard/instruction-first.
- Preserve the original clipboard until the user explicitly chooses to copy the transformed result.
- Treat high-value non-text clipboard formats conservatively.
- Source-app capture should refer to the app active at trigger time, not the palette/popup after it opens.

## Testing and validation

Use automated tests for deterministic behavior and manual QA for real Windows integration behavior.

Required validation for code changes:

```powershell
uv run pytest -q
uv run ruff check .
git diff --check
```

Use focused tests for:

- prompt construction
- graceful degradation messages
- clipboard preservation and copy failure
- worker lifecycle and cancellation
- overlay mutual exclusion
- palette button state and focus behavior
- positioning helpers

Manual QA remains required for:

- real global hotkeys
- Windows focus handoff between apps
- UI Automation capture in real applications
- mixed-DPI / multi-monitor placement
- clipboard locking or external app ownership
- end-to-end Lookup ↔ Smart Paste transitions

Use `docs/qa/manual-regression.md` as the repeatable manual QA baseline before milestone closeout or after changes to hotkeys, capture, clipboard behavior, overlays, worker lifecycles, model failure handling, or graceful degradation text.

`pytest-qt` may be added as a dev-only dependency for widget-level signal, focus, button-state, and event-filter regression tests. It does not replace live Windows manual QA.

Run Windows Qt test suites sequentially. Parallel full-suite runs contend for process-global clipboard, hotkey, window, and Qt event-loop state and can create false hangs or crashes. `tests/conftest.py` owns deterministic top-level-widget teardown between tests: close widgets, schedule deletion, explicitly flush `DeferredDelete` events, process remaining events, then run cyclic GC. Do not replace that sequence with `processEvents()` alone; it does not guarantee delivery of deferred deletions when pytest is driving Qt without the main event loop running.

Tests must also neutralize nonessential background threads that perform COM, UI Automation, clipboard, hotkey, or desktop calls. Such threads can outlive the test that spawned them and race later widget teardown even when the Qt disposal sequence is correct. The tray's UIA startup warm-up exists only to hide production cold-start latency, so the default test fixture replaces `_warmup_uia` with a no-op; a focused warm-up test must opt in deliberately and own the thread through completion.

## Issue tracker and Git evidence rules

For feature or bug work:

- Track durable decisions, implementation slices, review notes, and manual QA in an issue tracker.
- Include the relevant issue ID in branch names and commit messages when practical.
- In completion comments, separate automated validation from live manual QA.
- Report branch name, commit hash, validation commands, and any remaining manual-QA requirements.
- Do not switch away from a branch that needs human QA unless the QA state and checked-out branch are explicit.

## Documentation rules

- README should point to this file for project-specific implementation and QA rules.
- Human-facing plans, walkthroughs, QA summaries, and product reasoning can live in a local knowledge base.
- The LLM Wiki / knowledge navigation layer should point to source docs, not duplicate this file.
- Reusable generic rules should be extracted to the central `.agents` assets or a `new_project` template rather than copied between repos.

## Current source-of-truth pointers

- README: `README.md`
- Project rules: `PROJECT_RULES.md`
- Manual QA baseline: `docs/qa/manual-regression.md`
- Task tracker: maintained outside the repo with issue-based acceptance criteria and QA evidence.
- LLM Wiki / knowledge base: maintained outside the repo for longer design notes and navigation.
