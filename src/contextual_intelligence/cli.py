"""Phase 0 spike CLI.

    ci-lookup smoke                # LM Studio round trip + loaded model list
    ci-lookup capture --delay 3    # capture current selection, print payload JSON
    ci-lookup lookup --delay 3     # capture + validate + stream model answer
    ci-lookup listen               # Ctrl+Alt+D triggers lookup until Ctrl+C

--delay gives you time to click into another app and select a word before
capture fires, so the whole loop is testable without hotkey plumbing.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import tomllib
from pathlib import Path

from openai import APIConnectionError
from pydantic import ValidationError

from contextual_intelligence.config import Settings, default_config_path, load_settings
from contextual_intelligence.llm import LlmClient
from contextual_intelligence.log import setup_logging
from contextual_intelligence.models import CaptureError, ContextPayload

log = logging.getLogger(__name__)


def build_orchestrator(settings: Settings):
    from contextual_intelligence.capture import CaptureOrchestrator
    from contextual_intelligence.capture.uia import UiaCaptureProvider

    providers = [
        UiaCaptureProvider(context_chars_per_side=settings.context_chars_per_side)
    ]
    if settings.enable_clipboard_fallback:
        from contextual_intelligence.capture.clipboard_fallback import ArmedClipboardCapture
        providers.append(ArmedClipboardCapture())

    return CaptureOrchestrator(providers)


def _countdown(seconds: int) -> None:
    if seconds <= 0:
        return
    print(f"Select a word in any app — capturing in {seconds}s...", file=sys.stderr)
    time.sleep(seconds)


def _capture(settings: Settings, delay: int) -> ContextPayload:
    _countdown(delay)
    return build_orchestrator(settings).capture()


def _lm_studio_unreachable(settings: Settings) -> int:
    print(
        f"cannot reach LM Studio at {settings.base_url} — is the server running?\n"
        "(LM Studio: Developer tab -> Start Server, or `lms server start`)",
        file=sys.stderr,
    )
    return 1


def cmd_smoke(settings: Settings) -> int:
    client = LlmClient(settings)
    try:
        models = client.list_models()
    except APIConnectionError:
        return _lm_studio_unreachable(settings)
    print(f"LM Studio at {settings.base_url} — loaded models: {models}")
    if settings.model not in models:
        print(f"warning: configured model {settings.model!r} not in list; "
              f"set `model` in {default_config_path()}")
    print(f"round trip ({settings.model}): {client.smoke()!r}")
    return 0


def cmd_capture(settings: Settings, delay: int) -> int:
    try:
        payload = _capture(settings, delay)
    except CaptureError as exc:
        print(f"capture failed: {exc}", file=sys.stderr)
        return 1
    print(payload.model_dump_json(indent=2))
    return 0


def cmd_lookup(settings: Settings, delay: int) -> int:
    try:
        payload = _capture(settings, delay)
    except CaptureError as exc:
        print(f"capture failed: {exc}", file=sys.stderr)
        return 1
    print(f"[{payload.tier}] {payload.selected_text!r} in {payload.app_name} "
          f"(context: {len(payload.before) + len(payload.after)} chars)\n",
          file=sys.stderr)
    try:
        for token in LlmClient(settings).stream_lookup(payload):
            print(token, end="", flush=True)
    except APIConnectionError:
        return _lm_studio_unreachable(settings)
    print()
    return 0


def cmd_listen(settings: Settings) -> int:
    from contextual_intelligence.hotkey import run_hotkey_loop

    def on_hotkey() -> None:
        cmd_lookup(settings, delay=0)

    run_hotkey_loop(on_hotkey)
    return 0


def cmd_tray(settings: Settings) -> int:
    # Imported lazily: the guard module binds kernel32 at import, and cli.py
    # must stay importable on any platform (the sys.platform guard depends on it).
    from contextual_intelligence.instance import acquire_single_instance_lock

    if not acquire_single_instance_lock():
        print(
            "contextual-intelligence is already running (check the system tray)",
            file=sys.stderr,
        )
        return 1

    from contextual_intelligence.ui.tray import TrayApplication

    app = TrayApplication(
        settings=settings,
        orchestrator=build_orchestrator(settings),
        llm_client=LlmClient(settings),
    )
    return app.run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ci-lookup", description=__doc__)
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "debug", "info", "warning", "error", "critical"],
        default=None,
    )
    parser.add_argument("--env-file", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("smoke")
    for name in ("capture", "lookup"):
        p = sub.add_parser(name)
        p.add_argument("--delay", type=int, default=3)
    sub.add_parser("listen")
    for name in ("tray", "gui"):
        sub.add_parser(name)

    args = parser.parse_args(argv)
    # Windows consoles/redirects default to legacy codepages; model output is UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if sys.platform != "win32":
        print("error: Contextual Intelligence requires Windows", file=sys.stderr)
        return 1

    env_file = Path(args.env_file) if args.env_file else None
    if env_file and not env_file.is_file():
        print(f"config error: env file not found: {args.env_file}", file=sys.stderr)
        return 1

    try:
        settings = load_settings(dotenv=env_file)
    except (ValidationError, tomllib.TOMLDecodeError, OSError, ValueError) as exc:
        err_msg = str(exc)
        if isinstance(exc, ValidationError):
            errors = exc.errors()
            if errors:
                first = errors[0]
                loc = " -> ".join(str(x) for x in first.get("loc", []))
                msg = first.get("msg", "invalid value")
                err_msg = f"{loc}: {msg}" if loc else msg
        print(f"config error: {err_msg} — check %APPDATA%\\contextual-intelligence\\config.toml", file=sys.stderr)
        return 1

    setup_logging(args.log_level or settings.log_level)

    match args.command:
        case "smoke":
            return cmd_smoke(settings)
        case "capture":
            return cmd_capture(settings, args.delay)
        case "lookup":
            return cmd_lookup(settings, args.delay)
        case "listen":
            return cmd_listen(settings)
        case "tray" | "gui":
            return cmd_tray(settings)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
