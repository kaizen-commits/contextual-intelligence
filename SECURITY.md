# Security Policy

## Supported status

Contextual Intelligence is currently a run-from-source developer preview. Security-sensitive behavior may change while the project is pre-release, but the default posture is local-first and explicit-user-action-first.

## Data and endpoint trust

Contextual Intelligence can read selected text and clipboard text when a user triggers a workflow. By default, text is sent only to the configured OpenAI-compatible endpoint, typically LM Studio on the same machine.

If you configure the endpoint to use another machine or a cloud service, selected or copied text will be sent to that endpoint. Treat the configured endpoint as trusted infrastructure. Plain HTTP is accepted only for loopback addresses and literal private/link-local IP addresses; public IPs and hostname-based non-local endpoints require `https://`. A private-LAN HTTP endpoint is not encrypted: other systems with access to that network path may be able to observe the traffic.

The app should not persist clipboard history or selected text to disk. Diagnostic logs should describe capture tier, timing, and failure categories rather than storing user content.

## Clipboard fallback disclosures

The clipboard-based capture fallback for Contextual Lookup is disabled by
default and must be explicitly enabled in `config.toml`
(`enable_clipboard_fallback = true`). When enabled, a Lookup invocation may
send a synthetic `Ctrl+C` to the focused application after UIA capture fails.
The security-relevant properties are:

- Clipboard text is temporarily replaced during the capture, then restored
  under a conditional, verified transaction: restoration runs only when the
  detected change is attributed to the target application's process family
  (by executable image name) and the clipboard sequence still matches that
  attributed write, compared while the clipboard is held open. Observed
  sequence changes are never overwritten. Attribution is deliberately
  family-level rather than exact process identity — multi-process apps
  (Chromium/Electron) set the clipboard from a sibling process — so a write
  from another process with the same executable name cannot always be
  distinguished from the synthetic copy.
- Rich clipboard formats are not preserved; captures are refused when the
  clipboard holds images, files, audio, or other unrestorable content.
- Windows Clipboard History (Win+V), cross-device clipboard sync, and
  third-party clipboard managers may observe and retain the temporary
  selection even after successful restoration. If the selected text is
  sensitive, treat those histories as having seen it.
- Password and protected fields are rejected before any clipboard mutation.
- Restoration failure is surfaced to the user immediately with actionable
  guidance and blocks the lookup.

## Configuration trust

The application never reads configuration from the current working directory or the launch directory (e.g. from an implicit `.env` file). Endpoint addresses, API keys, and other settings are loaded only from the user's secure `%APPDATA%` directory, the process environment variables, or an explicitly supplied `--env-file` argument at startup. This prevents arbitrary launch locations (such as a shared network drive or Downloads folder) from hijacking the configuration and redirecting your captured text to malicious endpoints.

## Reporting vulnerabilities

Please report security issues privately instead of opening a public issue with exploit details. Use GitHub's private vulnerability reporting / private security advisory flow for this repository if available. If private advisories are not enabled yet, contact the maintainer through their GitHub profile and include:

- affected version or commit
- operating system and app/runtime context
- reproduction steps
- expected impact
- whether sensitive text, clipboard data, logs, or endpoint configuration are involved

Expected response target: acknowledgement within 7 days where practical, followed by private coordination until a fix or disclosure decision is ready.

## Scope examples

Useful reports include:

- clipboard mutation without explicit user action
- selected or copied text being logged or persisted unexpectedly
- unsafe fallback behavior for images, files, audio, or other non-text clipboard formats
- accidental cloud or remote endpoint use that is not clearly documented
- failure states that expose internal exception text containing sensitive content

Out of scope for now:

- requests for cloud model support
- installer/package hardening before installer support exists
- unsupported operating systems such as WSL, Linux, or macOS
