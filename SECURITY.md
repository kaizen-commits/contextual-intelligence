# Security Policy

## Supported status

Contextual Intelligence is currently a run-from-source developer preview. Security-sensitive behavior may change while the project is pre-release, but the default posture is local-first and explicit-user-action-first.

## Data and endpoint trust

Contextual Intelligence can read selected text and clipboard text when a user triggers a workflow. By default, text is sent only to the configured OpenAI-compatible endpoint, typically LM Studio on the same machine.

If you configure the endpoint to use another machine or a cloud service, selected or copied text will be sent to that endpoint. Treat the configured endpoint as trusted infrastructure. Non-local `http://` endpoints are rejected; keep HTTP endpoints on `localhost`/`127.0.0.1` or use `https://` for remote endpoints.

The app should not persist clipboard history or selected text to disk. Diagnostic logs should describe capture tier, timing, and failure categories rather than storing user content.

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
