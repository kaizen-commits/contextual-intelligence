# Manual regression checklist

Use this checklist before closing a milestone, after changes to hotkeys/UI workers/capture/clipboard behavior, or when issue-tracker acceptance criteria ask for manual QA evidence.

Record results in the task tracker as pass/fail notes with the branch, commit, app restart state, and any observed logs. Keep product signals separate from blockers: a signal can become a follow-up issue without blocking the parent unless an acceptance criterion failed.

## Pre-flight

- [ ] Confirm the app was restarted after the latest build/commit.
- [ ] Confirm the tested branch and commit.
- [ ] Confirm LM Studio is running with the expected model.
- [ ] Confirm Windows focus is in the intended source app before triggering a hotkey.
- [ ] Keep the tested branch checked out until manual QA is complete.

## Contextual Lookup smoke tests

- [ ] Select a normal word in Notepad and trigger `Ctrl+Alt+D`; popup appears near cursor and returns a contextual answer.
- [ ] Select a short phrase in a browser and trigger `Ctrl+Alt+D`; answer references the selected phrase and does not expose raw capture errors.
- [ ] Trigger Lookup with no active selection; app shows clear guidance rather than an internal all-tiers-failed error.
- [ ] Select an oversized paragraph and trigger Lookup; app explains the Lookup size/capability boundary and points to Smart Paste for paragraph work.
- [ ] Select a likely misspelled word with surrounding context; app either gives a useful inferred answer with visible uncertainty or clearly explains the limitation.

## Smart Paste smoke tests

- [ ] Copy plain text, trigger `Ctrl+Alt+V`, leave **Plain** selected, type an instruction, send, preview result, click Copy, and manually paste the result.
- [ ] Confirm the format picker lists **Plain**, **Markdown**, **Markdown table**, **JSON**, and **Action items** in that order.
- [ ] With **Plain** selected and no instruction, confirm Send remains disabled.
- [ ] Select each structured preset with a blank instruction and confirm Send is enabled because the preset supplies the transformation contract.
- [ ] Run a format-only transform for Markdown, Markdown table, JSON, and Action items; confirm the preview follows the selected contract without introductory or concluding commentary.
- [ ] Add an optional instruction to a structured preset and confirm it refines rather than removes the selected output format.
- [ ] Ask for Markdown output and confirm Markdown syntax is preserved when requested.
- [ ] Submit a second instruction after a result; primary button state is clear before and after the result.
- [ ] Change the selected preset after a result; the primary button returns to Send instead of copying a stale result.
- [ ] Use Up/Down instruction history and confirm the associated preset is restored with the instruction.
- [ ] Drag/reposition the palette and confirm it remains usable.
- [ ] Close or press Escape during/after a transform; app does not crash or leave a worker thread running.

## Lookup ↔ Smart Paste coexistence

- [ ] Run Lookup, then Smart Paste; Smart Paste opens normally and accepts keyboard input.
- [ ] Run Smart Paste, then Lookup on a normal source-app selection; Lookup still works.
- [ ] Copy a short word/phrase from the Smart Paste preview/result and trigger Lookup; Lookup uses the fresh Smart Paste copy handoff.
- [ ] Copy unrelated text in another app and trigger Lookup with no selection; app shows guidance and does not define stale clipboard content.
- [ ] Confirm one overlay closes or yields cleanly when the other workflow starts.

## Clipboard safety

- [ ] Empty clipboard or whitespace-only text: Smart Paste shows a clean error and makes no LLM call.
- [ ] Image clipboard, such as a screenshot/Paint image: Smart Paste declines safely and preserves the clipboard.
- [ ] File copied from File Explorer: Smart Paste declines safely and preserves the clipboard.
- [ ] Clipboard lock/copy failure: result remains visible and the app shows actionable feedback.
- [ ] Smart Paste never mutates clipboard until the user explicitly clicks Copy.

## Multi-monitor and placement

- [ ] Horizontal monitor: Lookup popup and Smart Paste palette open near cursor and stay on-screen.
- [ ] Vertical/left monitor: windows appear on the same monitor as the trigger/cursor where practical.
- [ ] Near screen edges: windows clamp inside visible screen bounds.
- [ ] Long status/error text wraps without exploding popup height or jumping off-screen.

## Model and service failure states

- [ ] LM Studio unavailable: app reports a clear local-model/service error.
- [ ] Empty model response: app reports the capability boundary or retry guidance, not a silent failure.
- [ ] Slow response: cancel/close paths remain safe and late worker output does not corrupt the current UI.
- [ ] Logs contain technical reasons while the user-facing UI stays plain-language.

## Graceful degradation acceptance framework

Add these acceptance criteria to new feature/bug issues unless explicitly irrelevant:

- [ ] Unsupported, ambiguous, failed, or edge-case paths either complete the expected action or explain the next useful action in plain language.
- [ ] Raw internal errors are logged, not shown as the primary user-facing message.
- [ ] The UI distinguishes user-correctable conditions from system/service failures.
- [ ] The failure path preserves user data and clipboard state.
- [ ] The feature has at least one negative/manual QA case, not only a happy-path test.
- [ ] Any fallback behavior is explicit in docs or issue-tracker comments, including what it does not cover.

## Phase 4 STT additions

Use this section after Speech Input / Voice-to-Transform work begins.

- [ ] Mic permission/device unavailable: app shows clear recovery guidance.
- [ ] No speech detected: app returns to an editable/idle state without sending anything.
- [ ] Transcript appears in the Smart Paste instruction field and is editable before Send.
- [ ] Speech completion does not automatically trigger transform.
- [ ] Audio recording/transcription does not mutate the clipboard.
- [ ] STT backend coexists with LM Studio without obvious VRAM/resource thrash.
